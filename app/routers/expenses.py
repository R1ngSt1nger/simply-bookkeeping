import os
import shutil
from datetime import date, datetime
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
from typing import Optional, List

from ..database import get_db
from .. import models
from ..auth import require_login, require_write
from ..templates_config import render
from ..date_ranges import resolve_range, RANGE_LABELS
from ..files import business_attachments_dir, business_pending_dir, safe_filename
from ..settings_helper import active_business_slug

router = APIRouter(prefix="/expenses", tags=["expenses"])


def _contacts_for_picker(db: Session, contact_type: str = "supplier"):
    contacts = db.query(models.Contact).filter(models.Contact.contact_type == contact_type).order_by(models.Contact.display_name).all()
    return [{"id": c.id, "name": c.display_name, "type": c.contact_type} for c in contacts]


def _expense_categories(db: Session):
    return db.query(models.ExpenseCategory).filter(models.ExpenseCategory.category_type == "expense").order_by(models.ExpenseCategory.name).all()


def _payment_method_names(db: Session):
    return [m.name for m in db.query(models.PaymentMethod).order_by(models.PaymentMethod.name).all()]


@router.get("", response_class=HTMLResponse)
def list_expenses(
    request: Request,
    range: str = "all",
    custom_start: str = "",
    custom_end: str = "",
    supplier: str = "",
    db: Session = Depends(get_db),
    user=Depends(require_login),
):
    q = db.query(models.Expense)
    bounds = resolve_range(range, custom_start=custom_start, custom_end=custom_end)
    if bounds:
        start, end = bounds
        q = q.filter(models.Expense.date >= start, models.Expense.date <= end)
    if supplier.strip():
        q = q.join(models.Contact, models.Expense.contact_id == models.Contact.id).filter(
            models.Contact.display_name.ilike(f"%{supplier.strip()}%")
        )
    expenses = q.order_by(models.Expense.date.desc()).all()
    return render(request, "expense_list.html", {
        "request": request, "user": user, "expenses": expenses,
        "selected_range": range, "range_labels": RANGE_LABELS,
        "custom_start": custom_start, "custom_end": custom_end, "supplier_query": supplier,
    })


@router.get("/new", response_class=HTMLResponse)
def new_expense_form(request: Request, upload_id: Optional[int] = None, db: Session = Depends(get_db), user=Depends(require_write)):
    pending_upload = None
    if upload_id:
        pending_upload = db.query(models.UploadedFile).filter(models.UploadedFile.id == upload_id).first()
    return render(request, "expense_form.html", {
        "request": request, "user": user, "expense": None,
        "today": date.today().isoformat(), "pending_upload": pending_upload,
        "contacts": _contacts_for_picker(db), "selected_contact": None,
        "categories": _expense_categories(db), "error": None,
        "payment_methods": _payment_method_names(db), "can_write": True,
    })


def _attach_from_pending(request: Request, db: Session, expense: models.Expense, upload_id: str, supplier_name: str, exp_date: str):
    pending = db.query(models.UploadedFile).filter(models.UploadedFile.id == int(upload_id)).first()
    if not pending:
        return
    slug = active_business_slug(request)
    src_path = os.path.join(business_pending_dir(slug), pending.stored_filename)
    if not os.path.exists(src_path):
        db.delete(pending)
        return
    ext = os.path.splitext(pending.original_filename)[1]
    display_name = f"{safe_filename(supplier_name)}_{exp_date}{ext}"
    disk_name = f"{datetime.utcnow().timestamp()}_{display_name}"
    disk_path = os.path.join(business_attachments_dir(slug), disk_name)
    shutil.move(src_path, disk_path)
    expense.attachment_filename = display_name
    expense.attachment_path = disk_name
    db.delete(pending)


def _save_payments(db: Session, expense: models.Expense, dates: List[str], amounts: List[str], methods: List[str]):
    for p in list(expense.payments):
        db.delete(p)
    db.flush()
    for i, (d, amt) in enumerate(zip(dates, amounts)):
        if not d.strip() or not amt.strip():
            continue
        method = methods[i].strip() if i < len(methods) and methods[i].strip() else "Cash"
        db.add(models.ExpensePayment(
            expense_id=expense.id,
            date=datetime.strptime(d, "%Y-%m-%d").date(),
            amount=float(amt),
            method=method,
        ))


def _form_error(request, user, db, expense, error, status_code=400, pending_upload=None):
    return render(request, "expense_form.html", {
        "request": request, "user": user, "expense": expense,
        "today": date.today().isoformat(), "pending_upload": pending_upload,
        "contacts": _contacts_for_picker(db), "selected_contact": expense.contact if expense else None,
        "categories": _expense_categories(db), "error": error,
        "payment_methods": _payment_method_names(db), "can_write": True,
    }, status_code=status_code)


@router.post("/new")
async def create_expense(
    request: Request,
    exp_date: str = Form(...),
    invoice_number: str = Form(""),
    contact_id: str = Form(""),
    description: str = Form(""),
    line_desc: List[str] = Form(...),
    line_amount: List[str] = Form(...),
    line_category: List[str] = Form([]),
    payment_date: List[str] = Form([]),
    payment_amount: List[str] = Form([]),
    payment_method: List[str] = Form([]),
    attachment: UploadFile = File(None),
    upload_id: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_write),
):
    if not contact_id:
        return _form_error(request, user, db, None, "Please choose a supplier.")

    contact = db.query(models.Contact).filter(models.Contact.id == int(contact_id)).first()
    supplier_name = contact.display_name if contact else "Expense"
    slug = active_business_slug(request)

    expense = models.Expense(
        date=datetime.strptime(exp_date, "%Y-%m-%d").date(),
        invoice_number=invoice_number.strip() or None,
        contact_id=int(contact_id),
        description=description.strip() or None,
    )

    if attachment and attachment.filename:
        ext = os.path.splitext(attachment.filename)[1]
        display_name = f"{safe_filename(supplier_name)}_{exp_date}{ext}"
        disk_name = f"{datetime.utcnow().timestamp()}_{display_name}"
        disk_path = os.path.join(business_attachments_dir(slug), disk_name)
        content = await attachment.read()
        with open(disk_path, "wb") as f:
            f.write(content)
        expense.attachment_filename = display_name
        expense.attachment_path = disk_name
    elif upload_id:
        _attach_from_pending(request, db, expense, upload_id, supplier_name, exp_date)

    db.add(expense)
    db.flush()

    for i, (desc, amt) in enumerate(zip(line_desc, line_amount)):
        if not desc.strip() or not amt.strip():
            continue
        cat = line_category[i] if i < len(line_category) and line_category[i] else None
        db.add(models.ExpenseLineItem(
            expense_id=expense.id,
            description=desc.strip(),
            amount=float(amt),
            category_id=int(cat) if cat else None,
        ))

    _save_payments(db, expense, payment_date, payment_amount, payment_method)

    db.commit()
    return RedirectResponse("/expenses", status_code=303)


@router.get("/{expense_id}/edit", response_class=HTMLResponse)
def edit_expense_form(expense_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    expense = db.query(models.Expense).filter(models.Expense.id == expense_id).first()
    return render(request, "expense_form.html", {
        "request": request, "user": user, "expense": expense,
        "today": date.today().isoformat(), "pending_upload": None,
        "contacts": _contacts_for_picker(db), "selected_contact": expense.contact if expense else None,
        "categories": _expense_categories(db), "error": None,
        "payment_methods": _payment_method_names(db),
        "can_write": user.role in ("owner", "bookkeeper"),
    })


@router.post("/{expense_id}/edit")
async def update_expense(
    expense_id: int,
    request: Request,
    exp_date: str = Form(...),
    invoice_number: str = Form(""),
    contact_id: str = Form(""),
    description: str = Form(""),
    line_desc: List[str] = Form(...),
    line_amount: List[str] = Form(...),
    line_category: List[str] = Form([]),
    payment_date: List[str] = Form([]),
    payment_amount: List[str] = Form([]),
    payment_method: List[str] = Form([]),
    attachment: UploadFile = File(None),
    db: Session = Depends(get_db),
    user=Depends(require_write),
):
    expense = db.query(models.Expense).filter(models.Expense.id == expense_id).first()

    if not contact_id:
        return _form_error(request, user, db, expense, "Please choose a supplier.")

    contact = db.query(models.Contact).filter(models.Contact.id == int(contact_id)).first()
    supplier_name = contact.display_name if contact else "Expense"
    slug = active_business_slug(request)

    expense.date = datetime.strptime(exp_date, "%Y-%m-%d").date()
    expense.invoice_number = invoice_number.strip() or None
    expense.contact_id = int(contact_id)
    expense.description = description.strip() or None

    if attachment and attachment.filename:
        if expense.attachment_path:
            old_path = os.path.join(business_attachments_dir(slug), expense.attachment_path)
            if os.path.exists(old_path):
                os.remove(old_path)
        ext = os.path.splitext(attachment.filename)[1]
        display_name = f"{safe_filename(supplier_name)}_{exp_date}{ext}"
        disk_name = f"{datetime.utcnow().timestamp()}_{display_name}"
        disk_path = os.path.join(business_attachments_dir(slug), disk_name)
        content = await attachment.read()
        with open(disk_path, "wb") as f:
            f.write(content)
        expense.attachment_filename = display_name
        expense.attachment_path = disk_name

    for li in list(expense.line_items):
        db.delete(li)
    db.flush()

    for i, (desc, amt) in enumerate(zip(line_desc, line_amount)):
        if not desc.strip() or not amt.strip():
            continue
        cat = line_category[i] if i < len(line_category) and line_category[i] else None
        db.add(models.ExpenseLineItem(
            expense_id=expense.id,
            description=desc.strip(),
            amount=float(amt),
            category_id=int(cat) if cat else None,
        ))

    _save_payments(db, expense, payment_date, payment_amount, payment_method)

    db.commit()
    return RedirectResponse("/expenses", status_code=303)


@router.post("/{expense_id}/delete")
def delete_expense(expense_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_write)):
    expense = db.query(models.Expense).filter(models.Expense.id == expense_id).first()
    if expense:
        if expense.attachment_path:
            slug = active_business_slug(request)
            path = os.path.join(business_attachments_dir(slug), expense.attachment_path)
            if os.path.exists(path):
                os.remove(path)
        db.delete(expense)
        db.commit()
    return RedirectResponse("/expenses", status_code=303)
