from datetime import date, datetime
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db, get_control_db
from .. import models
from ..auth import require_login, require_write
from ..templates_config import render
from ..date_ranges import resolve_range, RANGE_LABELS
from ..invoicing import claim_invoice_number

router = APIRouter(prefix="/income", tags=["income"])


def _contacts_for_picker(db: Session, contact_type: str = "customer"):
    contacts = db.query(models.Contact).filter(models.Contact.contact_type == contact_type).order_by(models.Contact.display_name).all()
    return [{"id": c.id, "name": c.display_name, "type": c.contact_type} for c in contacts]


def _line_item_categories(db: Session):
    """Income line items can carry either an income category (e.g. Accommodation)
    or an expense category (e.g. OTA Commission), since fee lines are negative income."""
    income_cats = db.query(models.ExpenseCategory).filter(models.ExpenseCategory.category_type == "income").order_by(models.ExpenseCategory.name).all()
    expense_cats = db.query(models.ExpenseCategory).filter(models.ExpenseCategory.category_type == "expense").order_by(models.ExpenseCategory.name).all()
    return {"income": income_cats, "expense": expense_cats}


def _payment_method_names(db: Session):
    return [m.name for m in db.query(models.PaymentMethod).order_by(models.PaymentMethod.name).all()]


def _save_payments(db: Session, tx: models.IncomeTransaction, dates: List[str], amounts: List[str], methods: List[str]):
    for p in list(tx.payments):
        db.delete(p)
    db.flush()
    for i, (d, amt) in enumerate(zip(dates, amounts)):
        if not d.strip() or not amt.strip():
            continue
        method = methods[i].strip() if i < len(methods) and methods[i].strip() else "Cash"
        db.add(models.IncomePayment(
            transaction_id=tx.id,
            date=datetime.strptime(d, "%Y-%m-%d").date(),
            amount=float(amt),
            method=method,
        ))


@router.get("", response_class=HTMLResponse)
def list_income(
    request: Request,
    range: str = "all",
    custom_start: str = "",
    custom_end: str = "",
    customer: str = "",
    db: Session = Depends(get_db),
    user=Depends(require_login),
):
    q = db.query(models.IncomeTransaction)
    bounds = resolve_range(range, custom_start=custom_start, custom_end=custom_end)
    if bounds:
        start, end = bounds
        q = q.filter(models.IncomeTransaction.date >= start, models.IncomeTransaction.date <= end)
    if customer.strip():
        q = q.join(models.Contact, models.IncomeTransaction.contact_id == models.Contact.id).filter(
            models.Contact.display_name.ilike(f"%{customer.strip()}%")
        )
    transactions = q.order_by(models.IncomeTransaction.date.desc()).all()
    return render(request, "income_list.html", {
        "request": request, "user": user, "transactions": transactions,
        "selected_range": range, "range_labels": RANGE_LABELS,
        "custom_start": custom_start, "custom_end": custom_end, "customer_query": customer,
    })


@router.get("/new", response_class=HTMLResponse)
def new_income_form(request: Request, from_quote: int = None, db: Session = Depends(get_db), user=Depends(require_write)):
    quote_prefill = None
    selected_contact = None
    if from_quote:
        quote = db.query(models.Quote).filter(models.Quote.id == from_quote).first()
        if quote:
            quote_prefill = {
                "quote_id": quote.id,
                "reference": quote.quote_number,
                "notes": quote.notes,
                "line_items": [{"description": li.description, "amount": float(li.amount)} for li in quote.line_items],
            }
            selected_contact = quote.contact
    return render(request, "income_form.html", {
        "request": request, "user": user, "transaction": None, "today": date.today().isoformat(),
        "contacts": _contacts_for_picker(db), "selected_contact": selected_contact,
        "categories": _line_item_categories(db), "error": None,
        "payment_methods": _payment_method_names(db), "can_write": True,
        "quote_prefill": quote_prefill, "audit_log": [], "smtp_configured": False,
    })


@router.post("/new")
def create_income(
    request: Request,
    tx_date: str = Form(...),
    reference: str = Form(""),
    notes: str = Form(""),
    contact_id: str = Form(""),
    line_desc: List[str] = Form(...),
    line_amount: List[str] = Form(...),
    line_category: List[str] = Form([]),
    payment_date: List[str] = Form([]),
    payment_amount: List[str] = Form([]),
    payment_method: List[str] = Form([]),
    from_quote_id: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_write),
):
    if not contact_id:
        return render(request, "income_form.html", {
            "request": request, "user": user, "transaction": None, "today": tx_date or date.today().isoformat(),
            "contacts": _contacts_for_picker(db), "selected_contact": None,
            "categories": _line_item_categories(db), "error": "Please choose a customer.",
            "payment_methods": _payment_method_names(db), "can_write": True, "quote_prefill": None, "audit_log": [], "smtp_configured": False,
        }, status_code=400)

    tx = models.IncomeTransaction(
        date=datetime.strptime(tx_date, "%Y-%m-%d").date(),
        reference=reference.strip() or None,
        notes=notes.strip() or None,
        contact_id=int(contact_id),
    )
    db.add(tx)
    db.flush()

    for i, (desc, amt) in enumerate(zip(line_desc, line_amount)):
        if not desc.strip() or not amt.strip():
            continue
        cat = line_category[i] if i < len(line_category) and line_category[i] else None
        db.add(models.IncomeLineItem(
            transaction_id=tx.id,
            description=desc.strip(),
            amount=float(amt),
            category_id=int(cat) if cat else None,
        ))

    _save_payments(db, tx, payment_date, payment_amount, payment_method)

    creation_note = "Income record created"
    quote = None
    if from_quote_id:
        quote = db.query(models.Quote).filter(models.Quote.id == int(from_quote_id)).first()
        if quote:
            quote.status = "accepted"
            quote.accepted_income_transaction_id = tx.id
            creation_note = f"Income record created from quote {quote.quote_number}"
    db.add(models.AuditLogEntry(record_type="income", record_id=tx.id, event=creation_note))
    if quote:
        db.add(models.AuditLogEntry(record_type="quote", record_id=quote.id, event="Converted to income record"))

    db.commit()
    return RedirectResponse("/income", status_code=303)


@router.get("/{tx_id}/edit", response_class=HTMLResponse)
def edit_income_form(tx_id: int, request: Request, success: str = None, error: str = None, db: Session = Depends(get_db), control_db: Session = Depends(get_control_db), user=Depends(require_login)):
    from ..settings_helper import get_app_settings
    from ..email_sender import is_smtp_configured
    tx = db.query(models.IncomeTransaction).filter(models.IncomeTransaction.id == tx_id).first()
    audit_log = db.query(models.AuditLogEntry).filter(
        models.AuditLogEntry.record_type == "income", models.AuditLogEntry.record_id == tx_id
    ).order_by(models.AuditLogEntry.created_at.desc()).all() if tx else []
    success_map = {"invoice_emailed": "Invoice emailed to customer."}
    error_map = {
        "no_email": "This customer doesn't have an email address on file.",
        "smtp_not_configured": "SMTP isn't configured yet — set it up in Settings first.",
        "send_failed": "Something went wrong sending that email — please try again.",
    }
    return render(request, "income_form.html", {
        "request": request, "user": user, "transaction": tx, "today": date.today().isoformat(),
        "contacts": _contacts_for_picker(db), "selected_contact": tx.contact if tx else None,
        "categories": _line_item_categories(db), "error": error_map.get(error),
        "success": success_map.get(success),
        "payment_methods": _payment_method_names(db),
        "can_write": user.role in ("owner", "bookkeeper"),
        "quote_prefill": None, "audit_log": audit_log,
        "smtp_configured": is_smtp_configured(get_app_settings(control_db)),
    })


@router.post("/{tx_id}/edit")
def update_income(
    tx_id: int,
    request: Request,
    tx_date: str = Form(...),
    reference: str = Form(""),
    notes: str = Form(""),
    contact_id: str = Form(""),
    line_desc: List[str] = Form(...),
    line_amount: List[str] = Form(...),
    line_category: List[str] = Form([]),
    payment_date: List[str] = Form([]),
    payment_amount: List[str] = Form([]),
    payment_method: List[str] = Form([]),
    invoice_due_date: str = Form(""),
    generate_invoice: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_write),
):
    tx = db.query(models.IncomeTransaction).filter(models.IncomeTransaction.id == tx_id).first()

    if not contact_id:
        return render(request, "income_form.html", {
            "request": request, "user": user, "transaction": tx, "today": date.today().isoformat(),
            "contacts": _contacts_for_picker(db), "selected_contact": tx.contact if tx else None,
            "categories": _line_item_categories(db), "error": "Please choose a customer.",
            "payment_methods": _payment_method_names(db), "can_write": True,
            "quote_prefill": None, "audit_log": [], "smtp_configured": False,
        }, status_code=400)

    tx.date = datetime.strptime(tx_date, "%Y-%m-%d").date()
    tx.reference = reference.strip() or None
    tx.notes = notes.strip() or None
    tx.contact_id = int(contact_id)
    tx.invoice_due_date = datetime.strptime(invoice_due_date, "%Y-%m-%d").date() if invoice_due_date.strip() else None

    for li in list(tx.line_items):
        db.delete(li)
    db.flush()

    for i, (desc, amt) in enumerate(zip(line_desc, line_amount)):
        if not desc.strip() or not amt.strip():
            continue
        cat = line_category[i] if i < len(line_category) and line_category[i] else None
        db.add(models.IncomeLineItem(
            transaction_id=tx.id,
            description=desc.strip(),
            amount=float(amt),
            category_id=int(cat) if cat else None,
        ))

    _save_payments(db, tx, payment_date, payment_amount, payment_method)

    if generate_invoice and not tx.invoice_number:
        tx.invoice_number = claim_invoice_number(db)
        db.add(models.AuditLogEntry(record_type="income", record_id=tx.id, event=f"Invoice {tx.invoice_number} created"))

    db.commit()

    if generate_invoice:
        return RedirectResponse(f"/income/{tx.id}/invoice.pdf", status_code=303)
    return RedirectResponse("/income", status_code=303)


@router.post("/{tx_id}/delete")
def delete_income(tx_id: int, db: Session = Depends(get_db), user=Depends(require_write)):
    tx = db.query(models.IncomeTransaction).filter(models.IncomeTransaction.id == tx_id).first()
    if tx:
        db.delete(tx)
        db.commit()
    return RedirectResponse("/income", status_code=303)


@router.get("/{tx_id}/invoice.pdf")
def invoice_pdf(tx_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    from fastapi.responses import StreamingResponse
    import io
    from ..settings_helper import get_active_business, active_theme_key
    from ..pdf_generation import generate_invoice_pdf

    tx = db.query(models.IncomeTransaction).filter(models.IncomeTransaction.id == tx_id).first()
    if not tx:
        return RedirectResponse("/income", status_code=303)

    biz = get_active_business(request)
    pdf_bytes = generate_invoice_pdf(tx, biz, active_theme_key(request))

    filename = f"{tx.invoice_number or 'invoice'}.pdf"
    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename={filename}"
    })


@router.post("/{tx_id}/email-invoice")
def email_invoice(tx_id: int, request: Request, db: Session = Depends(get_db), control_db: Session = Depends(get_control_db), user=Depends(require_write)):
    from ..settings_helper import get_active_business, active_theme_key
    from ..pdf_generation import generate_invoice_pdf
    from ..email_sender import send_email, is_smtp_configured
    from ..settings_helper import get_app_settings

    tx = db.query(models.IncomeTransaction).filter(models.IncomeTransaction.id == tx_id).first()
    if not tx or not tx.contact or not tx.contact.email:
        return RedirectResponse(f"/income/{tx_id}/edit?error=no_email", status_code=303)

    settings = get_app_settings(control_db)
    if not is_smtp_configured(settings):
        return RedirectResponse(f"/income/{tx_id}/edit?error=smtp_not_configured", status_code=303)

    biz = get_active_business(request)
    pdf_bytes = generate_invoice_pdf(tx, biz, active_theme_key(request))
    filename = f"{tx.invoice_number or 'invoice'}.pdf"

    html_body = f"""
    <div style="font-family:sans-serif;color:#173B3D;max-width:480px;margin:0 auto;">
      <img src="__LOGO_CID__" style="height:40px;margin-bottom:16px;" />
      <p>Hi {tx.contact.display_name},</p>
      <p>Please find attached invoice {tx.invoice_number or ''} from {biz.name}.</p>
      <p>Total: ${float(tx.total):,.2f}{' — balance due: $' + format(float(tx.balance_due), ',.2f') if tx.balance_due > 0 else ' — paid in full'}</p>
      <p>Thanks,<br/>{biz.name}</p>
    </div>
    """
    try:
        send_email(settings, tx.contact.email, f"Invoice {tx.invoice_number or ''} from {biz.name}".strip(), html_body, attachments=[(filename, pdf_bytes)])
    except Exception:
        return RedirectResponse(f"/income/{tx_id}/edit?error=send_failed", status_code=303)

    db.add(models.AuditLogEntry(record_type="income", record_id=tx.id, event=f"Invoice emailed to {tx.contact.email}"))
    db.commit()
    return RedirectResponse(f"/income/{tx_id}/edit?success=invoice_emailed", status_code=303)
