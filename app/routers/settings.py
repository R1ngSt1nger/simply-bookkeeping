import os
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from ..database import get_db, get_control_db
from .. import models
from .. import control_models
from ..auth import require_owner, require_login, require_write
from ..templates_config import render
from ..settings_helper import get_app_settings, all_businesses
from ..files import business_logo_path

router = APIRouter(prefix="/settings", tags=["settings"])


def _redirect_uri(request: Request) -> str:
    return str(request.url_for("sso_callback"))


def _categories(db: Session, selected_type: str = "all"):
    query = db.query(models.ExpenseCategory)
    if selected_type in ("income", "expense"):
        query = query.filter(models.ExpenseCategory.category_type == selected_type)
    return query.order_by(models.ExpenseCategory.name.asc()).all()


def _user_access_map(control_db: Session):
    """{user_id: set(business_id)} for every accountant login."""
    access_map = {}
    for row in control_db.query(control_models.UserBusinessAccess).all():
        access_map.setdefault(row.user_id, set()).add(row.business_id)
    return access_map


def _context(request: Request, db: Session, control_db: Session, user, error=None, success=None, category_type="all", opened=None):
    return {
        "user": user,
        "settings": get_app_settings(control_db),
        "redirect_uri": _redirect_uri(request),
        "categories": _categories(db, category_type),
        "selected_category_type": category_type,
        "logins": control_db.query(control_models.User).order_by(control_models.User.username).all(),
        "all_businesses_full": all_businesses(control_db),
        "user_access_map": _user_access_map(control_db),
        "payment_methods": db.query(models.PaymentMethod).order_by(models.PaymentMethod.name).all(),
        "opened": opened,
        "error": error,
        "success": success,
    }


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request, category_type: str = "all", opened: str = None,
    db: Session = Depends(get_db), control_db: Session = Depends(get_control_db),
    user=Depends(require_write),
):
    error_map = {
        "last_business": "You can't delete your only business.",
        "name_mismatch": "The typed name didn't match — deletion cancelled.",
        "login_missing_fields": "Username and password are required.",
        "login_username_taken": "That username is already taken.",
        "login_cant_delete_self": "You can't delete your own account while logged in as it.",
        "login_cant_change_self": "You can't change your own access level while logged in as it.",
    }
    error = error_map.get(request.query_params.get("error"))
    return render(request, "settings.html", _context(request, db, control_db, user, error=error, category_type=category_type, opened=opened))


@router.post("/business-details")
async def update_business_details(
    request: Request,
    abn: str = Form(""),
    address_line: str = Form(""),
    suburb: str = Form(""),
    state: str = Form(""),
    postcode: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    payment_bsb: str = Form(""),
    payment_account_number: str = Form(""),
    payment_account_name: str = Form(""),
    payment_payid: str = Form(""),
    logo: UploadFile = File(None),
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    user=Depends(require_write),
):
    from ..settings_helper import get_active_business
    biz = get_active_business(request)
    biz = control_db.query(control_models.Business).filter(control_models.Business.id == biz.id).first()

    biz.abn = abn.strip() or None
    biz.address_line = address_line.strip() or None
    biz.suburb = suburb.strip() or None
    biz.state = state.strip() or None
    biz.postcode = postcode.strip() or None
    biz.phone = phone.strip() or None
    biz.email = email.strip() or None
    biz.payment_bsb = payment_bsb.strip() or None
    biz.payment_account_number = payment_account_number.strip() or None
    biz.payment_account_name = payment_account_name.strip() or None
    biz.payment_payid = payment_payid.strip() or None

    if logo and logo.filename:
        if biz.logo_filename:
            old_path = business_logo_path(biz.slug, biz.logo_filename)
            if os.path.exists(old_path):
                os.remove(old_path)
        ext = os.path.splitext(logo.filename)[1] or ".png"
        new_filename = f"logo{ext}"
        content = await logo.read()
        with open(business_logo_path(biz.slug, new_filename), "wb") as f:
            f.write(content)
        biz.logo_filename = new_filename

    control_db.commit()
    return render(request, "settings.html", _context(request, db, control_db, user, success="Business details updated.", opened="branding"))


@router.get("/business-logo/{slug}")
def get_business_logo(slug: str, control_db: Session = Depends(get_control_db), user=Depends(require_login)):
    biz = control_db.query(control_models.Business).filter(control_models.Business.slug == slug).first()
    if not biz or not biz.logo_filename:
        return {"error": "No logo set"}
    path = business_logo_path(slug, biz.logo_filename)
    if not os.path.exists(path):
        return {"error": "File missing on disk"}
    return FileResponse(path)


@router.post("/business-details/remove-logo")
def remove_business_logo(
    request: Request,
    db: Session = Depends(get_db), control_db: Session = Depends(get_control_db),
    user=Depends(require_write),
):
    from ..settings_helper import get_active_business
    active = get_active_business(request)
    biz = control_db.query(control_models.Business).filter(control_models.Business.id == active.id).first()

    if biz.logo_filename:
        path = business_logo_path(biz.slug, biz.logo_filename)
        if os.path.exists(path):
            os.remove(path)
        biz.logo_filename = None
        control_db.commit()

    return render(request, "settings.html", _context(request, db, control_db, user, success="Logo removed.", opened="branding"))


@router.post("/oidc")
def update_oidc(
    request: Request,
    oidc_enabled: str = Form(None),
    oidc_issuer: str = Form(""),
    oidc_client_id: str = Form(""),
    oidc_client_secret: str = Form(""),
    oidc_button_text: str = Form(""),
    oidc_auto_create_users: str = Form(None),
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    user=Depends(require_owner),
):
    settings = get_app_settings(control_db)

    settings.oidc_issuer = oidc_issuer.strip() or None
    settings.oidc_client_id = oidc_client_id.strip() or None
    if oidc_client_secret.strip():
        # Only overwrite the stored secret if a new one was actually entered —
        # the form never redisplays the real value.
        settings.oidc_client_secret = oidc_client_secret.strip()
    settings.oidc_button_text = oidc_button_text.strip() or None
    settings.oidc_auto_create_users = bool(oidc_auto_create_users)

    enabling = bool(oidc_enabled)
    if enabling and not (settings.oidc_issuer and settings.oidc_client_id and settings.oidc_client_secret):
        settings.oidc_enabled = False
        control_db.commit()
        return render(request, "settings.html", _context(
            request, db, control_db, user,
            error="SSO needs a provider URL, client ID, and client secret before it can be enabled."
        ), status_code=400)

    settings.oidc_enabled = enabling
    control_db.commit()
    return render(request, "settings.html", _context(request, db, control_db, user, success="Single sign-on settings updated."))


@router.post("/oidc/disable")
def disable_oidc(request: Request, db: Session = Depends(get_db), control_db: Session = Depends(get_control_db), user=Depends(require_owner)):
    settings = get_app_settings(control_db)
    settings.oidc_enabled = False
    control_db.commit()
    return render(request, "settings.html", _context(request, db, control_db, user, success="Single sign-on disabled."))


# ---------- SMTP / email ----------

@router.post("/smtp")
def update_smtp(
    request: Request,
    smtp_enabled: str = Form(None),
    smtp_host: str = Form(""),
    smtp_port: str = Form(""),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_encryption: str = Form("starttls"),
    smtp_from_email: str = Form(""),
    smtp_from_name: str = Form(""),
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    user=Depends(require_owner),
):
    settings = get_app_settings(control_db)

    settings.smtp_host = smtp_host.strip() or None
    try:
        settings.smtp_port = int(smtp_port) if smtp_port.strip() else None
    except ValueError:
        settings.smtp_port = None
    settings.smtp_username = smtp_username.strip() or None
    if smtp_password.strip():
        # Only overwrite the stored password if a new one was actually entered —
        # the form never redisplays the real value.
        settings.smtp_password = smtp_password.strip()
    settings.smtp_encryption = smtp_encryption if smtp_encryption in ("starttls", "ssl", "none") else "starttls"
    settings.smtp_from_email = smtp_from_email.strip() or None
    settings.smtp_from_name = smtp_from_name.strip() or None

    enabling = bool(smtp_enabled)
    if enabling and not (settings.smtp_host and settings.smtp_from_email):
        settings.smtp_enabled = False
        control_db.commit()
        return render(request, "settings.html", _context(
            request, db, control_db, user,
            error="SMTP needs at least a host and a from-address before it can be enabled."
        ), status_code=400)

    settings.smtp_enabled = enabling
    control_db.commit()
    return render(request, "settings.html", _context(request, db, control_db, user, success="SMTP settings updated."))


@router.post("/smtp/disable")
def disable_smtp(request: Request, db: Session = Depends(get_db), control_db: Session = Depends(get_control_db), user=Depends(require_owner)):
    settings = get_app_settings(control_db)
    settings.smtp_enabled = False
    control_db.commit()
    return render(request, "settings.html", _context(request, db, control_db, user, success="SMTP disabled."))


@router.post("/smtp/test")
def test_smtp(request: Request, db: Session = Depends(get_db), control_db: Session = Depends(get_control_db), user=Depends(require_owner)):
    from ..email_sender import send_email, is_smtp_configured

    settings = get_app_settings(control_db)
    if not is_smtp_configured(settings):
        return render(request, "settings.html", _context(request, db, control_db, user, error="SMTP isn't enabled or fully configured yet."), status_code=400)

    target = settings.smtp_from_email
    html = (
        "<div style='font-family:sans-serif;padding:16px;'>"
        "<h2 style='color:#173B3D;'>Test email</h2>"
        "<p>This is a test message from your Simply Bookkeeping SMTP settings. "
        "If you're reading this, sending email works correctly.</p></div>"
    )
    try:
        send_email(settings, target, "Simply Bookkeeping — test email", html, embed_logo=False)
    except Exception as exc:
        return render(request, "settings.html", _context(request, db, control_db, user, error=f"Couldn't send test email: {exc}"), status_code=400)

    return render(request, "settings.html", _context(request, db, control_db, user, success=f"Test email sent to {target}."))


# ---------- Category management ----------

@router.post("/categories/new")
def create_category(
    request: Request, name: str = Form(...), category_type: str = Form(...),
    db: Session = Depends(get_db), control_db: Session = Depends(get_control_db), user=Depends(require_write),
):
    name = name.strip()
    category_type = category_type if category_type in ("income", "expense") else "expense"
    if name:
        existing = db.query(models.ExpenseCategory).filter(
            models.ExpenseCategory.name == name, models.ExpenseCategory.category_type == category_type
        ).first()
        if not existing:
            db.add(models.ExpenseCategory(name=name, category_type=category_type))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
    return RedirectResponse("/settings?opened=categories", status_code=303)


@router.post("/categories/{category_id}/rename")
def rename_category(
    category_id: int, name: str = Form(...), category_type: str = Form(...),
    db: Session = Depends(get_db), user=Depends(require_write),
):
    cat = db.query(models.ExpenseCategory).filter(models.ExpenseCategory.id == category_id).first()
    if cat and name.strip():
        cat.name = name.strip()
        if category_type in ("income", "expense"):
            cat.category_type = category_type
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return RedirectResponse("/settings?opened=categories", status_code=303)


@router.post("/categories/{category_id}/delete")
def delete_category(category_id: int, db: Session = Depends(get_db), user=Depends(require_write)):
    cat = db.query(models.ExpenseCategory).filter(models.ExpenseCategory.id == category_id).first()
    if cat:
        db.query(models.IncomeLineItem).filter(models.IncomeLineItem.category_id == category_id).update({"category_id": None})
        db.query(models.ExpenseLineItem).filter(models.ExpenseLineItem.category_id == category_id).update({"category_id": None})
        db.delete(cat)
        db.commit()
    return RedirectResponse("/settings?opened=categories", status_code=303)


# ---------- Payment methods ----------

@router.post("/payment-methods/new")
def create_payment_method(name: str = Form(...), db: Session = Depends(get_db), user=Depends(require_write)):
    name = name.strip()
    if name and not db.query(models.PaymentMethod).filter(models.PaymentMethod.name == name).first():
        db.add(models.PaymentMethod(name=name))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return RedirectResponse("/settings?opened=branding", status_code=303)


@router.post("/payment-methods/{method_id}/rename")
def rename_payment_method(method_id: int, name: str = Form(...), db: Session = Depends(get_db), user=Depends(require_write)):
    method = db.query(models.PaymentMethod).filter(models.PaymentMethod.id == method_id).first()
    if method and name.strip():
        method.name = name.strip()
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return RedirectResponse("/settings?opened=branding", status_code=303)


@router.post("/payment-methods/{method_id}/delete")
def delete_payment_method(method_id: int, db: Session = Depends(get_db), user=Depends(require_write)):
    method = db.query(models.PaymentMethod).filter(models.PaymentMethod.id == method_id).first()
    total = db.query(models.PaymentMethod).count()
    if method and total > 1:
        db.delete(method)
        db.commit()
    return RedirectResponse("/settings?opened=branding", status_code=303)
