from sqlalchemy.orm import Session
from fastapi import Request

from . import control_models
from .database import DEFAULT_BUSINESS_SLUG, ControlSessionLocal, slugify

DEFAULT_BUSINESS_NAME = "Simply Bookkeeping"


def get_app_settings(db: Session) -> control_models.AppSettings:
    """Fetch the singleton global settings row (SSO config), creating it if needed.
    `db` must be a control-db session."""
    settings = db.query(control_models.AppSettings).first()
    if not settings:
        settings = control_models.AppSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def sso_login_context(db: Session, error=None):
    """Context needed by login.html to decide whether to show the SSO button."""
    app_settings = get_app_settings(db)
    return {
        "error": error,
        "sso_enabled": bool(app_settings.oidc_enabled and app_settings.oidc_client_id and app_settings.oidc_client_secret),
        "sso_button_text": app_settings.oidc_button_text or "Sign in with SSO",
    }


def ensure_default_business(control_db: Session):
    """Guarantee at least one business is registered — created automatically
    on first boot (or during the legacy single-business migration).

    This checks for ANY business existing, not specifically one with the
    default slug — once the user has their own business(es) set up, this
    must never recreate a "Simply Bookkeeping" business behind their back
    just because that particular slug isn't in use any more."""
    biz = control_db.query(control_models.Business).first()
    if not biz:
        biz = control_models.Business(slug=DEFAULT_BUSINESS_SLUG, name=DEFAULT_BUSINESS_NAME)
        control_db.add(biz)
        control_db.commit()
    return biz


def active_business_slug(request: Request) -> str:
    return request.session.get("business_slug") or DEFAULT_BUSINESS_SLUG


def accessible_businesses_for_user(db: Session, user):
    """Owners can always see every business. Bookkeepers and Accountants only
    see the ones explicitly granted to them."""
    if not user or user.role == "owner":
        return db.query(control_models.Business).order_by(control_models.Business.name).all()
    ids = [a.business_id for a in db.query(control_models.UserBusinessAccess).filter(
        control_models.UserBusinessAccess.user_id == user.id
    ).all()]
    if not ids:
        return []
    return db.query(control_models.Business).filter(
        control_models.Business.id.in_(ids)
    ).order_by(control_models.Business.name).all()


def resolve_active_business_slug(request: Request):
    """The business slug this request should actually use, enforcing per-user
    access — falls back to the user's first accessible business if their
    session points at one they've lost access to, or None if they have none."""
    db = ControlSessionLocal()
    try:
        slug = active_business_slug(request)
        user_id = request.session.get("user_id")
        if not user_id:
            return slug

        user = db.query(control_models.User).filter(control_models.User.id == user_id).first()
        if not user or user.role == "owner":
            return slug

        accessible = accessible_businesses_for_user(db, user)
        if any(b.slug == slug for b in accessible):
            return slug
        if accessible:
            request.session["business_slug"] = accessible[0].slug
            return accessible[0].slug
        return None
    finally:
        db.close()


def get_active_business(request: Request):
    """Direct, lightweight control-db lookup — used where a full Depends(get_control_db)
    isn't already in scope (e.g. inside the shared render() helper)."""
    slug = resolve_active_business_slug(request)
    db = ControlSessionLocal()
    try:
        if not slug:
            return None
        biz = db.query(control_models.Business).filter(control_models.Business.slug == slug).first()
        if not biz:
            # The remembered slug no longer exists (e.g. it was deleted).
            # Fall back to any other business that's still around rather than
            # silently recreating the named default — only create a fresh
            # default business if there's genuinely none left at all.
            biz = db.query(control_models.Business).order_by(control_models.Business.name).first()
            if not biz:
                biz = ensure_default_business(db)
            request.session["business_slug"] = biz.slug
        return biz
    finally:
        db.close()


def resolve_login_business_slug(db: Session, user) -> str | None:
    """Which business a user should land on right after logging in — their
    last-used business if it still exists and they still have access to it,
    otherwise their first accessible business, or None if they have none."""
    accessible = accessible_businesses_for_user(db, user)
    if not accessible:
        return None
    if user.last_business_slug:
        match = next((b for b in accessible if b.slug == user.last_business_slug), None)
        if match:
            return match.slug
    return accessible[0].slug


def set_session_business_for_login(request: Request, db: Session, user):
    """Call right after establishing a logged-in session (password login, SSO,
    first-run setup) so the session starts pointed at a business that still
    exists, instead of falling back to the hardcoded default slug."""
    slug = resolve_login_business_slug(db, user)
    if slug:
        request.session["business_slug"] = slug
    return slug


def set_active_business(request: Request, db: Session, user, slug: str):
    """Switch the session's active business AND remember it against the user,
    so it's restored automatically next time they log in."""
    request.session["business_slug"] = slug
    if user is not None and user.last_business_slug != slug:
        user.last_business_slug = slug
        db.commit()


def all_businesses(db: Session):
    return db.query(control_models.Business).order_by(control_models.Business.name).all()


def unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    slug = base
    n = 2
    while db.query(control_models.Business).filter(control_models.Business.slug == slug).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


def active_theme_key(request: Request) -> str:
    from .themes import DEFAULT_THEME
    db = ControlSessionLocal()
    try:
        user_id = request.session.get("user_id")
        user = db.query(control_models.User).filter(control_models.User.id == user_id).first() if user_id else None
        return user.theme if user and user.theme else DEFAULT_THEME
    finally:
        db.close()
