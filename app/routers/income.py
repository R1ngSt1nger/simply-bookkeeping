import os
from datetime import date, datetime
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db
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
def new_income_form(request: Request, db: Session = Depends(get_db), user=Depends(require_write)):
    return render(request, "income_form.html", {
        "request": request, "user": user, "transaction": None, "today": date.today().isoformat(),
        "contacts": _contacts_for_picker(db), "selected_contact": None,
        "categories": _line_item_categories(db), "error": None,
        "payment_methods": _payment_method_names(db), "can_write": True,
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
    db: Session = Depends(get_db),
    user=Depends(require_write),
):
    if not contact_id:
        return render(request, "income_form.html", {
            "request": request, "user": user, "transaction": None, "today": tx_date or date.today().isoformat(),
            "contacts": _contacts_for_picker(db), "selected_contact": None,
            "categories": _line_item_categories(db), "error": "Please choose a customer.",
            "payment_methods": _payment_method_names(db), "can_write": True,
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

    db.commit()
    return RedirectResponse("/income", status_code=303)


@router.get("/{tx_id}/edit", response_class=HTMLResponse)
def edit_income_form(tx_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    tx = db.query(models.IncomeTransaction).filter(models.IncomeTransaction.id == tx_id).first()
    return render(request, "income_form.html", {
        "request": request, "user": user, "transaction": tx, "today": date.today().isoformat(),
        "contacts": _contacts_for_picker(db), "selected_contact": tx.contact if tx else None,
        "categories": _line_item_categories(db), "error": None,
        "payment_methods": _payment_method_names(db),
        "can_write": user.role in ("owner", "bookkeeper"),
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
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from fastapi.responses import StreamingResponse
    from ..settings_helper import get_active_business, active_theme_key
    from ..themes import get_theme
    from ..files import business_logo_path

    tx = db.query(models.IncomeTransaction).filter(models.IncomeTransaction.id == tx_id).first()
    if not tx:
        return RedirectResponse("/income", status_code=303)

    biz = get_active_business(request)
    theme = get_theme(active_theme_key(request))

    ink = colors.HexColor(theme["ink"])
    rust = colors.HexColor("#D1493C")
    green = colors.HexColor("#1E9E6B")
    grey = colors.HexColor("#8A8D85")
    line_col = colors.HexColor(theme["line"])

    def wrap_text(text, font_name, font_size, max_width):
        """Word-wrap plain text to fit within max_width, returning a list of lines."""
        words = text.split()
        if not words:
            return [""]
        lines = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if stringWidth(candidate, font_name, font_size) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    left = 20 * mm
    right = width - 20 * mm

    y = height - 20 * mm

    # --- Header: logo (top-left), business details to its right, invoice meta top-right ---
    logo_width = 22 * mm
    logo_height = 16 * mm
    logo_bottom = y
    text_x = left

    if biz.logo_filename:
        logo_path = business_logo_path(biz.slug, biz.logo_filename)
        if os.path.exists(logo_path):
            try:
                c.drawImage(logo_path, left, y - logo_height, width=logo_width, height=logo_height,
                             preserveAspectRatio=True, mask="auto")
                logo_bottom = y - logo_height
                text_x = left + logo_width + 6 * mm  # push business details clear of the logo
            except Exception:
                pass

    name_y = y
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(ink)
    c.drawString(text_x, name_y, biz.name)
    name_y -= 6 * mm

    c.setFont("Helvetica", 9)
    c.setFillColor(grey)
    detail_lines = []
    if biz.abn:
        detail_lines.append(f"ABN {biz.abn}")
    addr_bits = [biz.address_line, " ".join(filter(None, [biz.suburb, biz.state, biz.postcode]))]
    addr_bits = [b for b in addr_bits if b]
    detail_lines.extend(addr_bits)
    contact_bits = [b for b in [biz.phone, biz.email] if b]
    if contact_bits:
        detail_lines.append(" · ".join(contact_bits))
    for line in detail_lines:
        c.drawString(text_x, name_y, line)
        name_y -= 4.5 * mm

    # Invoice meta, top right
    meta_y = y
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(ink)
    c.drawRightString(right, meta_y, "INVOICE")
    meta_y -= 8 * mm
    c.setFont("Helvetica", 9.5)
    c.setFillColor(ink)
    c.drawRightString(right, meta_y, f"Invoice #: {tx.invoice_number or '—'}")
    meta_y -= 5 * mm
    c.drawRightString(right, meta_y, f"Issue date: {tx.date.strftime('%d %b %Y')}")
    meta_y -= 5 * mm
    if tx.invoice_due_date:
        c.drawRightString(right, meta_y, f"Due date: {tx.invoice_due_date.strftime('%d %b %Y')}")
        meta_y -= 5 * mm

    y = min(name_y, logo_bottom, meta_y) - 8 * mm

    # --- Bill To ---
    c.setStrokeColor(line_col)
    c.line(left, y, right, y)
    y -= 8 * mm

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(grey)
    c.drawString(left, y, "BILL TO")
    y -= 5.5 * mm
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(ink)
    if tx.contact:
        c.drawString(left, y, tx.contact.display_name)
        y -= 5.5 * mm
        c.setFont("Helvetica", 9.5)
        if tx.contact.email:
            c.drawString(left, y, tx.contact.email)
            y -= 4.5 * mm
        if tx.contact.phone:
            c.drawString(left, y, tx.contact.phone)
            y -= 4.5 * mm
    if tx.reference:
        c.setFont("Helvetica", 9)
        c.setFillColor(grey)
        c.drawString(left, y, f"Reference: {tx.reference}")
        y -= 4.5 * mm

    y -= 6 * mm

    # --- Line items table ---
    amount_col_width = 28 * mm
    desc_max_width = (right - amount_col_width) - left

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(grey)
    c.drawString(left, y, "DESCRIPTION")
    c.drawRightString(right, y, "AMOUNT")
    y -= 3 * mm
    c.setStrokeColor(line_col)
    c.line(left, y, right, y)
    y -= 6 * mm

    c.setFont("Helvetica", 10)
    c.setFillColor(ink)
    row_shade = colors.HexColor("#F3F2EE")
    for i, li in enumerate(tx.line_items):
        desc_lines = wrap_text(li.description, "Helvetica", 10, desc_max_width)
        row_height = (4.5 * mm) * (len(desc_lines) - 1) + 6 * mm

        if i % 2 == 1:
            c.setFillColor(row_shade)
            c.rect(left - 3 * mm, y - row_height + 4.5 * mm, (right - left) + 6 * mm, row_height, fill=1, stroke=0)
            c.setFillColor(ink)

        amt = float(li.amount)
        c.drawString(left, y, desc_lines[0])
        c.drawRightString(right, y, f"{'-' if amt < 0 else ''}${abs(amt):,.2f}")
        for extra_line in desc_lines[1:]:
            y -= 4.5 * mm
            c.drawString(left, y, extra_line)
        y -= 6 * mm

    c.setStrokeColor(line_col)
    c.line(left, y, right, y)
    y -= 7 * mm

    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(ink)
    c.drawString(left, y, "Total")
    c.drawRightString(right, y, f"${float(tx.total):,.2f}")
    y -= 10 * mm

    # --- Payment history ---
    if tx.payments:
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(grey)
        c.drawString(left, y, "PAYMENTS RECEIVED")
        y -= 5.5 * mm
        c.setFont("Helvetica", 9.5)
        c.setFillColor(ink)
        for p in tx.payments:
            method_label = p.method
            c.drawString(left, y, f"{p.date.strftime('%d %b %Y')} — {method_label}")
            c.drawRightString(right, y, f"${float(p.amount):,.2f}")
            y -= 5 * mm
        y -= 4 * mm

    balance = float(tx.balance_due)
    c.setStrokeColor(ink)
    c.setLineWidth(1.2)
    c.line(left, y, right, y)
    y -= 8 * mm
    c.setFont("Helvetica-Bold", 13)
    if balance <= 0:
        c.setFillColor(green)
        c.drawString(left, y, "PAID IN FULL")
        c.drawRightString(right, y, "$0.00")
    else:
        c.setFillColor(rust)
        c.drawString(left, y, "Balance due")
        c.drawRightString(right, y, f"${balance:,.2f}")
    y -= 14 * mm

    # --- Payment details ---
    payment_bits = [b for b in [
        ("BSB:", biz.payment_bsb) if biz.payment_bsb else None,
        ("Account:", biz.payment_account_number) if biz.payment_account_number else None,
        ("Name:", biz.payment_account_name) if biz.payment_account_name else None,
        ("PayID:", biz.payment_payid) if biz.payment_payid else None,
    ] if b]
    if payment_bits:
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(grey)
        c.drawString(left, y, "PAYMENT DETAILS")
        y -= 5.5 * mm
        c.setFillColor(ink)
        for label, value in payment_bits:
            c.setFont("Helvetica-Bold", 9.5)
            c.drawString(left, y, label)
            label_width = stringWidth(label, "Helvetica-Bold", 9.5)
            c.setFont("Helvetica", 9.5)
            c.drawString(left + label_width + 1.5 * mm, y, value)
            y -= 4.5 * mm

    c.showPage()
    c.save()
    buf.seek(0)

    filename = f"{tx.invoice_number or 'invoice'}.pdf"
    return StreamingResponse(buf, media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename={filename}"
    })
