import secrets
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_control_db
from .. import control_models
from ..auth import hash_password
from ..templates_config import render
from ..settings_helper import get_app_settings, sso_login_context
from ..oidc import build_oidc_client

router = APIRouter(prefix="/auth/sso", tags=["sso"])


@router.get("/login")
async def sso_login(request: Request, db: Session = Depends(get_control_db)):
    settings = get_app_settings(db)
    if not settings.oidc_enabled:
        return RedirectResponse("/login", status_code=303)
    client = build_oidc_client(settings)
    if not client:
        return RedirectResponse("/login", status_code=303)
    redirect_uri = str(request.url_for("sso_callback"))
    try:
        return await client.authorize_redirect(request, redirect_uri)
    except Exception:
        return render(request, "login.html", sso_login_context(
            db, error="Couldn't reach the SSO provider. Check the provider URL in Settings, or try again shortly."
        ), status_code=502)


@router.get("/callback", name="sso_callback")
async def sso_callback(request: Request, db: Session = Depends(get_control_db)):
    settings = get_app_settings(db)
    client = build_oidc_client(settings)
    if not client or not settings.oidc_enabled:
        return RedirectResponse("/login", status_code=303)

    try:
        token = await client.authorize_access_token(request)
    except Exception:
        return render(request, "login.html", sso_login_context(
            db, error="SSO login failed or was cancelled. Try again, or use your username and password below."
        ), status_code=400)

    userinfo = token.get("userinfo") or {}
    identifier = (userinfo.get("preferred_username") or userinfo.get("email") or "").strip()
    email = (userinfo.get("email") or "").strip() or None

    if not identifier:
        return render(request, "login.html", sso_login_context(
            db, error="Your identity provider didn't return a username or email to match against."
        ), status_code=400)

    conditions = [control_models.User.username == identifier, control_models.User.email == identifier]
    if email:
        conditions.append(control_models.User.email == email)
    user = db.query(control_models.User).filter(or_(*conditions)).first()

    if not user and settings.oidc_auto_create_users:
        base_username = identifier.split("@")[0]
        username = base_username
        suffix = 1
        while db.query(control_models.User).filter(control_models.User.username == username).first():
            suffix += 1
            username = f"{base_username}{suffix}"
        user = control_models.User(
            username=username,
            password_hash=hash_password(secrets.token_hex(16)),  # unusable random password — SSO-only account
            role="accountant",
            display_name=userinfo.get("name") or identifier,
            email=email,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    if not user:
        return render(request, "login.html", sso_login_context(
            db, error=f"No local account matches the SSO identity '{identifier}'. Ask the owner to add a login with a matching username or email first."
        ), status_code=403)

    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = user.role
    return RedirectResponse("/", status_code=303)
