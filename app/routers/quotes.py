from datetime import date, datetime, timedelta
from typing import List
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from ..database import get_db, get_control_db
from .. import models
from ..auth import require_login, require_write
from ..templates_config import render
from ..date_ranges import resolve_range, RANGE_LABELS
from ..invoicing import claim_quote_number

router = APIRouter(prefix="/quotes", tags=["quotes"])


def _contacts_for_picker(db: Session):
    contacts = db.query(models.Contact).filter(models.Contact.contact_type == "customer").order_by(models.Contact.display_name).all()
    return [{"id": c.id, "name": c.display_name, "type": c.contact_type} for c in contacts]


@router.get("", response_class=HTMLResponse)
def list_quotes(
    request: Request,
    range: str = "all",
    custom_start: str = "",
    custom_end: str = "",
    customer: str = "",
    status: str = "all",
    db: Session = Depends(get_db),
    user=Depends(require_login),
):
    q = db.query(models.Quote)
    bounds = resolve_range(range, custom_start=custom_start, custom_end=custom_end)
    if bounds:
        start, end = bounds
        q = q.filter(models.Quote.date >= start, models.Quote.date <= end)
    if customer.strip():
        q = q.join(models.Contact, models.Quote.contact_id == models.Contact.id).filter(
            models.Contact.display_name.ilike(f"%{customer.strip()}%")
        )
    if status == "accepted":
        q = q.filter(models.Quote.status == "accepted")
    elif status == "pending":
        q = q.filter(models.Quote.status == "pending")

    quotes = q.order_by(models.Quote.date.desc()).all()
    return render(request, "quotes_list.html", {
        "request": request, "user": user, "quotes": quotes,
        "selected_range": range, "range_labels": RANGE_LABELS,
        "custom_start": custom_start, "custom_end": custom_end, "customer_query": customer,
        "selected_status": status,
    })


@router.get("/new", response_class=HTMLResponse)
def new_quote_form(request: Request, db: Session = Depends(get_db), user=Depends(require_write)):
    return render(request, "quote_form.html", {
        "request": request, "user": user, "quote": None, "today": date.today().isoformat(),
        "expiry_default": (date.today() + timedelta(days=14)).isoformat(),
        "contacts": _contacts_for_picker(db), "selected_contact": None,
        "error": None, "success": None, "can_write": True, "audit_log": [], "smtp_configured": False,
    })


@router.post("/new")
def create_quote(
    request: Request,
    q_date: str = Form(...),
    expiry_date: str = Form(""),
    notes: str = Form(""),
    contact_id: str = Form(""),
    line_desc: List[str] = Form(...),
    line_amount: List[str] = Form(...),
    db: Session = Depends(get_db),
    user=Depends(require_write),
):
    if not contact_id:
        return render(request, "quote_form.html", {
            "request": request, "user": user, "quote": None, "today": q_date or date.today().isoformat(),
            "expiry_default": expiry_date, "contacts": _contacts_for_picker(db), "selected_contact": None,
            "error": "Please choose a customer.", "success": None, "can_write": True, "audit_log": [], "smtp_configured": False,
        }, status_code=400)

    quote = models.Quote(
        date=datetime.strptime(q_date, "%Y-%m-%d").date(),
        quote_number=claim_quote_number(db),
        expiry_date=datetime.strptime(expiry_date, "%Y-%m-%d").date() if expiry_date.strip() else None,
        notes=notes.strip() or None,
        contact_id=int(contact_id),
    )
    db.add(quote)
    db.flush()

    for desc, amt in zip(line_desc, line_amount):
        if not desc.strip() or not amt.strip():
            continue
        db.add(models.QuoteLineItem(quote_id=quote.id, description=desc.strip(), amount=float(amt)))

    db.add(models.AuditLogEntry(record_type="quote", record_id=quote.id, event=f"Quote {quote.quote_number} created"))
    db.commit()
    return RedirectResponse(f"/quotes/{quote.id}/edit", status_code=303)


@router.get("/{quote_id}/edit", response_class=HTMLResponse)
def edit_quote_form(quote_id: int, request: Request, db: Session = Depends(get_db), control_db: Session = Depends(get_control_db), user=Depends(require_login)):
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if not quote:
        return RedirectResponse("/quotes", status_code=303)
    audit_log = db.query(models.AuditLogEntry).filter(
        models.AuditLogEntry.record_type == "quote", models.AuditLogEntry.record_id == quote_id
    ).order_by(models.AuditLogEntry.created_at.desc()).all()
    from ..settings_helper import get_app_settings
    from ..email_sender import is_smtp_configured
    return render(request, "quote_form.html", {
        "request": request, "user": user, "quote": quote, "today": date.today().isoformat(),
        "expiry_default": quote.expiry_date.isoformat() if quote.expiry_date else "",
        "contacts": _contacts_for_picker(db), "selected_contact": quote.contact,
        "error": None,
        "can_write": user.role in ("owner", "bookkeeper") and quote.status != "accepted",
        "audit_log": audit_log,
        "smtp_configured": is_smtp_configured(get_app_settings(control_db)),
    })


@router.post("/{quote_id}/edit")
def update_quote(
    quote_id: int,
    request: Request,
    q_date: str = Form(...),
    expiry_date: str = Form(""),
    notes: str = Form(""),
    contact_id: str = Form(""),
    line_desc: List[str] = Form(...),
    line_amount: List[str] = Form(...),
    db: Session = Depends(get_db),
    user=Depends(require_write),
):
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if not quote:
        return RedirectResponse("/quotes", status_code=303)
    if quote.status == "accepted":
        return RedirectResponse(f"/quotes/{quote_id}/edit", status_code=303)

    if not contact_id:
        return render(request, "quote_form.html", {
            "request": request, "user": user, "quote": quote, "today": date.today().isoformat(),
            "expiry_default": expiry_date, "contacts": _contacts_for_picker(db), "selected_contact": quote.contact,
            "error": "Please choose a customer.", "success": None, "can_write": True, "audit_log": [], "smtp_configured": False,
        }, status_code=400)

    quote.date = datetime.strptime(q_date, "%Y-%m-%d").date()
    quote.expiry_date = datetime.strptime(expiry_date, "%Y-%m-%d").date() if expiry_date.strip() else None
    quote.notes = notes.strip() or None
    quote.contact_id = int(contact_id)

    for li in list(quote.line_items):
        db.delete(li)
    db.flush()
    for desc, amt in zip(line_desc, line_amount):
        if not desc.strip() or not amt.strip():
            continue
        db.add(models.QuoteLineItem(quote_id=quote.id, description=desc.strip(), amount=float(amt)))

    db.commit()
    return RedirectResponse(f"/quotes/{quote_id}/edit", status_code=303)


@router.post("/{quote_id}/delete")
def delete_quote(quote_id: int, db: Session = Depends(get_db), user=Depends(require_write)):
    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if quote:
        db.delete(quote)
        db.commit()
    return RedirectResponse("/quotes", status_code=303)


@router.get("/{quote_id}/quote.pdf")
def quote_pdf(quote_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    from fastapi.responses import StreamingResponse
    import io
    from ..settings_helper import get_active_business, active_theme_key
    from ..pdf_generation import generate_quote_pdf

    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if not quote:
        return RedirectResponse("/quotes", status_code=303)

    biz = get_active_business(request)
    pdf_bytes = generate_quote_pdf(quote, biz, active_theme_key(request))

    filename = f"{quote.quote_number}.pdf"
    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename={filename}"
    })


@router.post("/{quote_id}/email")
def email_quote(quote_id: int, request: Request, db: Session = Depends(get_db), control_db: Session = Depends(get_control_db), user=Depends(require_write)):
    from fastapi.responses import JSONResponse
    from ..settings_helper import get_active_business, active_theme_key, get_app_settings
    from ..pdf_generation import generate_quote_pdf
    from ..email_sender import send_email, is_smtp_configured

    quote = db.query(models.Quote).filter(models.Quote.id == quote_id).first()
    if not quote or not quote.contact or not quote.contact.email:
        return JSONResponse({"success": False, "message": "This customer doesn't have an email address on file."}, status_code=400)

    settings = get_app_settings(control_db)
    if not is_smtp_configured(settings):
        return JSONResponse({"success": False, "message": "SMTP isn't configured yet — set it up in Settings first."}, status_code=400)

    biz = get_active_business(request)
    pdf_bytes = generate_quote_pdf(quote, biz, active_theme_key(request))
    filename = f"{quote.quote_number}.pdf"

    expiry_line = f"<p>This quote is valid until {quote.expiry_date.strftime('%d %B %Y')}.</p>" if quote.expiry_date else ""
    html_body = f"""
    <div style="font-family:sans-serif;color:#173B3D;max-width:480px;margin:0 auto;">
      <img src="__LOGO_CID__" style="height:40px;margin-bottom:16px;" />
      <p>Hi {quote.contact.display_name},</p>
      <p>Please find attached quote {quote.quote_number} from {biz.name}.</p>
      <p>Total: ${float(quote.total):,.2f}</p>
      {expiry_line}
      <p>Thanks,<br/>{biz.name}</p>
    </div>
    """
    try:
        send_email(settings, quote.contact.email, f"Quote {quote.quote_number} from {biz.name}", html_body, attachments=[(filename, pdf_bytes)])
    except Exception as e:
        return JSONResponse({"success": False, "message": f"Something went wrong sending that email: {e}"}, status_code=502)

    db.add(models.AuditLogEntry(record_type="quote", record_id=quote.id, event=f"Quote emailed to {quote.contact.email}"))
    db.commit()
    return JSONResponse({"success": True, "message": f"Quote successfully emailed to {quote.contact.email}."})
