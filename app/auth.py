import bcrypt
from fastapi import Request, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .database import get_control_db
from .control_models import User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def get_current_user(request: Request, db: Session = Depends(get_control_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


def require_login(request: Request, db: Session = Depends(get_control_db)) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def require_owner(request: Request, db: Session = Depends(get_control_db)) -> User:
    user = require_login(request, db)
    if user.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Read-only access — this action isn't available to your account.")
    return user


def require_write(request: Request, db: Session = Depends(get_control_db)) -> User:
    """Day-to-day data entry (Income, Expenses, Contacts, Uploads) — owners and
    bookkeepers can both do this. Administrative areas (Settings, business
    management, logins) stay strictly owner-only via require_owner."""
    user = require_login(request, db)
    if user.role not in ("owner", "bookkeeper"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Read-only access — this action isn't available to your account.")
    return user
