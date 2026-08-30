from typing import List
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from ..database import get_control_db
from .. import control_models
from ..auth import require_login, require_owner, hash_password, verify_password
from ..templates_config import render

router = APIRouter(prefix="/profile", tags=["profile"])


def _profile_context(user: control_models.User, error=None, success=None):
    from ..themes import THEMES
    return {"user": user, "theme_options": THEMES, "error": error, "success": success}


@router.get("", response_class=HTMLResponse)
def profile_page(request: Request, user=Depends(require_login)):
    return render(request, "profile.html", _profile_context(user))


@router.post("/display-name")
def update_display_name(request: Request, display_name: str = Form(...), db: Session = Depends(get_control_db), user=Depends(require_login)):
    user.display_name = display_name.strip() or user.username
    db.commit()
    request.session["display_name"] = user.display_name
    return render(request, "profile.html", _profile_context(user, success="Display name updated."))


@router.post("/email")
def update_email(request: Request, email: str = Form(""), db: Session = Depends(get_control_db), user=Depends(require_login)):
    email = email.strip()
    if email:
        existing = db.query(control_models.User).filter(
            control_models.User.email == email, control_models.User.id != user.id
        ).first()
        if existing:
            return render(request, "profile.html", _profile_context(user, error="Another account is already using that email address."), status_code=400)
    user.email = email or None
    db.commit()
    return render(request, "profile.html", _profile_context(user, success="Email address updated."))


@router.post("/theme")
def update_theme(request: Request, theme: str = Form(...), db: Session = Depends(get_control_db), user=Depends(require_login)):
    from ..themes import THEMES
    if theme in THEMES:
        user.theme = theme
        db.commit()
    return render(request, "profile.html", _profile_context(user, success="Colour theme updated."))


@router.post("/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_control_db),
    user=Depends(require_login),
):
    if not verify_password(current_password, user.password_hash):
        return render(request, "profile.html", _profile_context(user, error="Current password is incorrect."), status_code=400)
    if not new_password:
        return render(request, "profile.html", _profile_context(user, error="New password can't be empty."), status_code=400)
    if new_password != confirm_password:
        return render(request, "profile.html", _profile_context(user, error="New passwords don't match."), status_code=400)

    user.password_hash = hash_password(new_password)
    db.commit()
    return render(request, "profile.html", _profile_context(user, success="Password updated."))


# ---------- Manage logins (rendered from the Settings page) ----------

@router.post("/users/new")
def add_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    business_ids: List[int] = Form([]),
    db: Session = Depends(get_control_db),
    user=Depends(require_owner),
):
    username = username.strip()
    if role not in ("owner", "bookkeeper", "accountant"):
        role = "accountant"
    if not username or not password:
        return RedirectResponse("/settings?error=login_missing_fields&opened=logins", status_code=303)
    if db.query(control_models.User).filter(control_models.User.username == username).first():
        return RedirectResponse("/settings?error=login_username_taken&opened=logins", status_code=303)

    new_user = control_models.User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        display_name=username,
    )
    db.add(new_user)
    db.flush()

    if role in ("bookkeeper", "accountant"):
        for bid in business_ids:
            db.add(control_models.UserBusinessAccess(user_id=new_user.id, business_id=bid))

    db.commit()
    return RedirectResponse("/settings?opened=logins", status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user(user_id: int, request: Request, db: Session = Depends(get_control_db), user=Depends(require_owner)):
    if user_id == user.id:
        return RedirectResponse("/settings?error=login_cant_delete_self&opened=logins", status_code=303)
    target = db.query(control_models.User).filter(control_models.User.id == user_id).first()
    if target:
        db.query(control_models.UserBusinessAccess).filter(control_models.UserBusinessAccess.user_id == user_id).delete()
        db.delete(target)
        db.commit()
    return RedirectResponse("/settings?opened=logins", status_code=303)


@router.post("/users/{user_id}/access")
def update_user_access(
    user_id: int, business_ids: List[int] = Form([]),
    db: Session = Depends(get_control_db), user=Depends(require_owner),
):
    target = db.query(control_models.User).filter(control_models.User.id == user_id).first()
    if target and target.role in ("bookkeeper", "accountant"):
        db.query(control_models.UserBusinessAccess).filter(control_models.UserBusinessAccess.user_id == user_id).delete()
        for bid in business_ids:
            db.add(control_models.UserBusinessAccess(user_id=user_id, business_id=bid))
        db.commit()
    return RedirectResponse("/settings?opened=logins", status_code=303)


@router.post("/users/{user_id}/role")
def update_user_role(
    user_id: int, role: str = Form(...),
    db: Session = Depends(get_control_db), user=Depends(require_owner),
):
    if user_id == user.id:
        return RedirectResponse("/settings?error=login_cant_change_self&opened=logins", status_code=303)
    if role not in ("owner", "bookkeeper", "accountant"):
        return RedirectResponse("/settings?opened=logins", status_code=303)

    target = db.query(control_models.User).filter(control_models.User.id == user_id).first()
    if target:
        target.role = role
        if role == "owner":
            # Owners always see every business — any leftover per-business grants are moot.
            db.query(control_models.UserBusinessAccess).filter(control_models.UserBusinessAccess.user_id == user_id).delete()
        db.commit()
    return RedirectResponse("/settings?opened=logins", status_code=303)


@router.post("/users/{user_id}/reset-password")
def owner_reset_password(
    user_id: int, new_password: str = Form(...),
    db: Session = Depends(get_control_db), user=Depends(require_owner),
):
    target = db.query(control_models.User).filter(control_models.User.id == user_id).first()
    if target and new_password:
        target.password_hash = hash_password(new_password)
        db.commit()
    return RedirectResponse("/settings?opened=logins", status_code=303)
