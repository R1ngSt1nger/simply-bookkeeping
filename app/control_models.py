from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey
from datetime import datetime, timezone
from .database import ControlBase


class User(ControlBase):
    """Logins are global — the same account can access every business."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="owner")  # "owner", "bookkeeper", or "accountant"
    display_name = Column(String, nullable=True)
    email = Column(String, nullable=True)  # optional — used to match SSO identities
    theme = Column(String, nullable=True)  # each user's own colour theme preference
    ocr_enabled = Column(Boolean, nullable=True)  # NULL/unset is treated as enabled (default on)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Business(ControlBase):
    """Registry of businesses. Each one's actual bookkeeping data lives in its
    own SQLite file at data/businesses/{slug}.db."""
    __tablename__ = "businesses"

    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Branding & details — used on generated invoices
    logo_filename = Column(String, nullable=True)  # stored alongside this business's attachments
    abn = Column(String, nullable=True)
    address_line = Column(String, nullable=True)
    suburb = Column(String, nullable=True)
    state = Column(String, nullable=True)
    postcode = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)

    # Payment details — printed on invoices so customers know how to pay
    payment_bsb = Column(String, nullable=True)
    payment_account_number = Column(String, nullable=True)
    payment_account_name = Column(String, nullable=True)
    payment_payid = Column(String, nullable=True)


class UserBusinessAccess(ControlBase):
    """Which businesses a Bookkeeper or Accountant (non-owner) login can see. Owners always
    have access to every business, so this table is only consulted for them."""
    __tablename__ = "user_business_access"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)


class AppSettings(ControlBase):
    """Singleton row (id=1) holding app-wide (not per-business) configuration —
    SSO and SMTP. (Colour theme is a per-user preference, stored on User.)"""
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)

    oidc_enabled = Column(Boolean, nullable=False, default=False)
    oidc_issuer = Column(String, nullable=True)  # base URL — /.well-known/openid-configuration is appended
    oidc_client_id = Column(String, nullable=True)
    oidc_client_secret = Column(String, nullable=True)
    oidc_button_text = Column(String, nullable=True)
    oidc_auto_create_users = Column(Boolean, nullable=False, default=False)

    smtp_enabled = Column(Boolean, nullable=False, default=False)
    smtp_host = Column(String, nullable=True)
    smtp_port = Column(Integer, nullable=True)
    smtp_username = Column(String, nullable=True)
    smtp_password = Column(String, nullable=True)
    smtp_encryption = Column(String, nullable=True)  # "starttls", "ssl", or "none"
    smtp_from_email = Column(String, nullable=True)
    smtp_from_name = Column(String, nullable=True)


class PasswordResetToken(ControlBase):
    """Single-use, time-limited tokens for the 'forgot your password' email flow."""
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
