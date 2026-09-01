import os
from fastapi.templating import Jinja2Templates
import json
from datetime import timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "app", "templates"))


def money(value):
    if value is None:
        value = 0
    neg = value < 0
    v = abs(value)
    formatted = f"{v:,.2f}"
    return f"-${formatted}" if neg else f"${formatted}"


def local_dt(value):
    """Convert a stored UTC timestamp (e.g. audit log entries) to the
    container's local time for display. SQLite/SQLAlchemy silently strips
    timezone info on round-trip even though these are always stored via
    datetime.now(timezone.utc) — so a naive value here is UTC, not local,
    and must be explicitly labelled as UTC before converting, or
    .astimezone() would treat it as already-local and do nothing."""
    if value is None:
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone()


def tojson_filter(value):
    return json.dumps(value)


templates.env.filters["money"] = money
templates.env.filters["local_dt"] = local_dt
templates.env.filters["tojson"] = tojson_filter


def render(request, name, context=None, status_code=200):
    """Compatibility wrapper for Starlette's TemplateResponse(request, name, context).
    Also injects the active business's name/slug, the businesses the current user
    can see, and the active colour theme into every render, so templates (mainly
    the sidebar and base.html's Tailwind config) don't need it threaded through
    every route."""
    from .settings_helper import get_active_business, accessible_businesses_for_user, ControlSessionLocal, DEFAULT_BUSINESS_NAME
    from . import control_models
    from .themes import get_theme, DEFAULT_THEME

    context = context or {}
    context.pop("request", None)

    active_biz = get_active_business(request)
    context.setdefault("business_name", lambda: active_biz.name if active_biz else DEFAULT_BUSINESS_NAME)
    context.setdefault("active_business", active_biz)

    db = ControlSessionLocal()
    try:
        user_id = request.session.get("user_id")
        current_user = db.query(control_models.User).filter(control_models.User.id == user_id).first() if user_id else None
        context.setdefault("all_businesses", accessible_businesses_for_user(db, current_user))
        theme_key = current_user.theme if current_user and current_user.theme else DEFAULT_THEME
    finally:
        db.close()

    context.setdefault("theme", get_theme(theme_key))
    context.setdefault("theme_key", theme_key)

    return templates.TemplateResponse(request, name, context, status_code=status_code)
