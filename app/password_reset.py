import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from . import control_models

TOKEN_LIFETIME = timedelta(hours=1)


def create_reset_token(db: Session, user: control_models.User) -> str:
    """Invalidate any earlier unused tokens for this user, then issue a fresh one."""
    db.query(control_models.PasswordResetToken).filter(
        control_models.PasswordResetToken.user_id == user.id,
        control_models.PasswordResetToken.used_at.is_(None),
    ).delete()

    token = secrets.token_urlsafe(32)
    row = control_models.PasswordResetToken(
        user_id=user.id,
        token=token,
        expires_at=datetime.now(timezone.utc) + TOKEN_LIFETIME,
    )
    db.add(row)
    db.commit()
    return token


def get_valid_token(db: Session, token: str):
    """Return the PasswordResetToken row if it exists, is unused, and hasn't
    expired — otherwise None."""
    row = db.query(control_models.PasswordResetToken).filter(
        control_models.PasswordResetToken.token == token
    ).first()
    if not row or row.used_at is not None:
        return None
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None
    return row


def consume_token(db: Session, row: control_models.PasswordResetToken):
    row.used_at = datetime.now(timezone.utc)
    db.commit()
