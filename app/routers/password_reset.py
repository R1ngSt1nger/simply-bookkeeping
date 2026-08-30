from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_control_db
from .. import control_models
from ..auth import hash_password
from ..templates_config import render, templates
from ..settings_helper import get_app_settings, sso_login_context, active_theme_key
from ..themes import get_theme
from ..email_sender import send_email, is_smtp_configured
from ..password_reset import create_reset_token, get_valid_token, consume_token

router = APIRouter(tags=["password-reset"])


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_form(request: Request, db: Session = Depends(get_control_db)):
    settings = get_app_settings(db)
    if not is_smtp_configured(settings):
        return RedirectResponse("/login?error=smtp_not_configured", status_code=303)
    return render(request, "forgot_password.html", {"error": None, "sent": False})


@router.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_submit(request: Request, username: str = Form(...), db: Session = Depends(get_control_db)):
    settings = get_app_settings(db)
    if not is_smtp_configured(settings):
        return RedirectResponse("/login?error=smtp_not_configured", status_code=303)

    user = db.query(control_models.User).filter(control_models.User.username == username.strip()).first()

    if user and user.email:
        token = create_reset_token(db, user)
        reset_url = str(request.url_for("reset_password_form")) + f"?token={token}"
        theme = get_theme(active_theme_key(request))
        html = templates.env.get_template("email_password_reset.html").render(
            display_name=user.display_name or user.username,
            username=user.username,
            reset_url=reset_url,
            accent_color=theme["accent"],
        )
        try:
            send_email(settings, user.email, "Reset your Simply Bookkeeping password", html)
        except Exception:
            pass  # still show the generic message below — don't leak send failures to the requester

    # Always the same message, regardless of whether the account/email exists —
    # avoids confirming which usernames are valid.
    return render(request, "forgot_password.html", {"error": None, "sent": True})


@router.get("/reset-password", response_class=HTMLResponse, name="reset_password_form")
def reset_password_form(request: Request, token: str = "", db: Session = Depends(get_control_db)):
    row = get_valid_token(db, token) if token else None
    if not row:
        return render(request, "reset_password.html", {"invalid": True, "token": token, "error": None})
    return render(request, "reset_password.html", {"invalid": False, "token": token, "error": None})


@router.post("/reset-password", response_class=HTMLResponse)
def reset_password_submit(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_control_db),
):
    row = get_valid_token(db, token)
    if not row:
        return render(request, "reset_password.html", {"invalid": True, "token": token, "error": None})

    if not new_password:
        return render(request, "reset_password.html", {"invalid": False, "token": token, "error": "New password can't be empty."}, status_code=400)
    if new_password != confirm_password:
        return render(request, "reset_password.html", {"invalid": False, "token": token, "error": "Passwords don't match."}, status_code=400)

    user = db.query(control_models.User).filter(control_models.User.id == row.user_id).first()
    if not user:
        return render(request, "reset_password.html", {"invalid": True, "token": token, "error": None})

    user.password_hash = hash_password(new_password)
    consume_token(db, row)

    context = sso_login_context(db, error=None)
    context["success"] = "Your password has been reset. You can log in now."
    return render(request, "login.html", context)
