from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..auth import require_login, require_write
from ..templates_config import render

router = APIRouter(prefix="/contacts", tags=["contacts"])

PAGE_SIZE = 30
AU_STATES = ["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"]


def compute_display_name(designation: str, company_name: str, first_name: str, last_name: str) -> str:
    if designation == "company":
        return (company_name or "").strip()
    return f"{(first_name or '').strip()} {(last_name or '').strip()}".strip()


@router.post("/quick-create")
def quick_create_contact(
    contact_type: str = Form(...),
    designation: str = Form(...),
    company_name: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_write),
):
    """Lightweight contact creation used by the searchable picker on the
    Income/Expense forms, so the user doesn't have to leave the page."""
    from fastapi.responses import JSONResponse

    if contact_type not in ("customer", "supplier") or designation not in ("company", "individual"):
        return JSONResponse({"error": "Invalid contact type."}, status_code=400)
    if designation == "company" and not company_name.strip():
        return JSONResponse({"error": "Company name is required."}, status_code=400)
    if designation == "individual" and (not first_name.strip() or not last_name.strip()):
        return JSONResponse({"error": "First name and surname are required."}, status_code=400)

    display_name = compute_display_name(designation, company_name, first_name, last_name)
    contact = models.Contact(
        contact_type=contact_type,
        designation=designation,
        company_name=company_name.strip() or None if designation == "company" else None,
        first_name=first_name.strip() or None if designation == "individual" else None,
        last_name=last_name.strip() or None if designation == "individual" else None,
        email=email.strip() or None,
        phone=phone.strip() or None,
        display_name=display_name,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)

    return JSONResponse({"id": contact.id, "name": contact.display_name, "type": contact.contact_type})


@router.get("", response_class=HTMLResponse)
def list_contacts(
    request: Request,
    page: int = 1,
    q: str = "",
    type: str = "all",
    db: Session = Depends(get_db),
    user=Depends(require_login),
):
    query = db.query(models.Contact)
    if type in ("customer", "supplier"):
        query = query.filter(models.Contact.contact_type == type)
    if q.strip():
        query = query.filter(models.Contact.display_name.ilike(f"%{q.strip()}%"))

    total = query.count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    contacts = query.order_by(models.Contact.display_name.asc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    return render(request, "contacts_list.html", {
        "request": request, "user": user, "contacts": contacts,
        "page": page, "total_pages": total_pages, "total": total,
        "q": q, "selected_type": type,
    })


@router.get("/new", response_class=HTMLResponse)
def new_contact_form(request: Request, user=Depends(require_write)):
    return render(request, "contact_form.html", {
        "request": request, "user": user, "contact": None, "states": AU_STATES, "error": None,
    })


@router.post("/new")
def create_contact(
    request: Request,
    contact_type: str = Form(...),
    designation: str = Form(...),
    company_name: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
    contact_person: str = Form(""),
    website: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    street_address: str = Form(""),
    suburb: str = Form(""),
    state: str = Form(""),
    postcode: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_write),
):
    if contact_type not in ("customer", "supplier") or designation not in ("company", "individual"):
        return render(request, "contact_form.html", {
            "request": request, "user": user, "contact": None, "states": AU_STATES,
            "error": "Choose a contact type and designation.",
        }, status_code=400)

    if designation == "company" and not company_name.strip():
        return render(request, "contact_form.html", {
            "request": request, "user": user, "contact": None, "states": AU_STATES,
            "error": "Company name is required.",
        }, status_code=400)
    if designation == "individual" and (not first_name.strip() or not last_name.strip()):
        return render(request, "contact_form.html", {
            "request": request, "user": user, "contact": None, "states": AU_STATES,
            "error": "First name and surname are required for an individual contact.",
        }, status_code=400)

    display_name = compute_display_name(designation, company_name, first_name, last_name)

    contact = models.Contact(
        contact_type=contact_type,
        designation=designation,
        company_name=company_name.strip() or None if designation == "company" else None,
        first_name=first_name.strip() or None if designation == "individual" else None,
        last_name=last_name.strip() or None if designation == "individual" else None,
        contact_person=contact_person.strip() or None if designation == "company" else None,
        website=website.strip() or None if designation == "company" else None,
        email=email.strip() or None,
        phone=phone.strip() or None,
        street_address=street_address.strip() or None,
        suburb=suburb.strip() or None,
        state=state.strip() or None,
        postcode=postcode.strip() or None,
        notes=notes.strip() or None,
        display_name=display_name,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return RedirectResponse(f"/contacts/{contact.id}", status_code=303)


@router.get("/{contact_id}", response_class=HTMLResponse)
def contact_detail(contact_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    contact = db.query(models.Contact).filter(models.Contact.id == contact_id).first()
    if not contact:
        return RedirectResponse("/contacts", status_code=303)
    income_records = db.query(models.IncomeTransaction).filter(
        models.IncomeTransaction.contact_id == contact_id
    ).order_by(models.IncomeTransaction.date.desc()).all()
    expense_records = db.query(models.Expense).filter(
        models.Expense.contact_id == contact_id
    ).order_by(models.Expense.date.desc()).all()
    return render(request, "contact_detail.html", {
        "request": request, "user": user, "contact": contact,
        "income_records": income_records, "expense_records": expense_records,
    })


@router.get("/{contact_id}/edit", response_class=HTMLResponse)
def edit_contact_form(contact_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_write)):
    contact = db.query(models.Contact).filter(models.Contact.id == contact_id).first()
    return render(request, "contact_form.html", {
        "request": request, "user": user, "contact": contact, "states": AU_STATES, "error": None,
    })


@router.post("/{contact_id}/edit")
def update_contact(
    contact_id: int,
    request: Request,
    contact_type: str = Form(...),
    designation: str = Form(...),
    company_name: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
    contact_person: str = Form(""),
    website: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    street_address: str = Form(""),
    suburb: str = Form(""),
    state: str = Form(""),
    postcode: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_write),
):
    contact = db.query(models.Contact).filter(models.Contact.id == contact_id).first()

    if designation == "company" and not company_name.strip():
        return render(request, "contact_form.html", {
            "request": request, "user": user, "contact": contact, "states": AU_STATES,
            "error": "Company name is required.",
        }, status_code=400)
    if designation == "individual" and (not first_name.strip() or not last_name.strip()):
        return render(request, "contact_form.html", {
            "request": request, "user": user, "contact": contact, "states": AU_STATES,
            "error": "First name and surname are required for an individual contact.",
        }, status_code=400)

    contact.contact_type = contact_type
    contact.designation = designation
    contact.company_name = company_name.strip() or None if designation == "company" else None
    contact.first_name = first_name.strip() or None if designation == "individual" else None
    contact.last_name = last_name.strip() or None if designation == "individual" else None
    contact.contact_person = contact_person.strip() or None if designation == "company" else None
    contact.website = website.strip() or None if designation == "company" else None
    contact.email = email.strip() or None
    contact.phone = phone.strip() or None
    contact.street_address = street_address.strip() or None
    contact.suburb = suburb.strip() or None
    contact.state = state.strip() or None
    contact.postcode = postcode.strip() or None
    contact.notes = notes.strip() or None
    contact.display_name = compute_display_name(designation, company_name, first_name, last_name)

    db.commit()
    return RedirectResponse(f"/contacts/{contact.id}", status_code=303)


@router.post("/{contact_id}/delete")
def delete_contact(contact_id: int, db: Session = Depends(get_db), user=Depends(require_write)):
    contact = db.query(models.Contact).filter(models.Contact.id == contact_id).first()
    if contact:
        db.query(models.IncomeTransaction).filter(models.IncomeTransaction.contact_id == contact_id).update({"contact_id": None})
        db.query(models.Expense).filter(models.Expense.contact_id == contact_id).update({"contact_id": None})
        db.delete(contact)
        db.commit()
    return RedirectResponse("/contacts", status_code=303)
