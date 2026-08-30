from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_control_db, DEFAULT_BUSINESS_SLUG, business_db_path, forget_business_engine
from .. import control_models
from ..auth import require_login, require_owner
from ..settings_helper import unique_slug, accessible_businesses_for_user

router = APIRouter(tags=["businesses"])


@router.get("/switch-business/{slug}")
def switch_business(slug: str, request: Request, db: Session = Depends(get_control_db), user=Depends(require_login)):
    accessible = accessible_businesses_for_user(db, user)
    if any(b.slug == slug for b in accessible):
        request.session["business_slug"] = slug
    return RedirectResponse("/", status_code=303)


@router.post("/settings/businesses/new")
def create_business(request: Request, name: str = Form(...), db: Session = Depends(get_control_db), user=Depends(require_owner)):
    name = name.strip()
    if name:
        slug = unique_slug(db, name)
        biz = control_models.Business(slug=slug, name=name)
        db.add(biz)
        db.commit()
        from ..database import get_business_engine
        from ..migrations import _run_per_business_migrations
        engine = get_business_engine(slug)  # creates the new (empty) database file with the current schema
        _run_per_business_migrations(engine)  # seeds default payment methods, etc.
        request.session["business_slug"] = slug
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/businesses/{slug}/rename")
def rename_business(slug: str, name: str = Form(...), db: Session = Depends(get_control_db), user=Depends(require_owner)):
    biz = db.query(control_models.Business).filter(control_models.Business.slug == slug).first()
    if biz and name.strip():
        biz.name = name.strip()
        db.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/businesses/{slug}/delete")
def delete_business(slug: str, request: Request, confirm_name: str = Form(...), db: Session = Depends(get_control_db), user=Depends(require_owner)):
    biz = db.query(control_models.Business).filter(control_models.Business.slug == slug).first()
    total_businesses = db.query(control_models.Business).count()

    if not biz:
        return RedirectResponse("/settings", status_code=303)
    if total_businesses <= 1:
        return RedirectResponse("/settings?error=last_business", status_code=303)
    if confirm_name.strip() != biz.name:
        return RedirectResponse("/settings?error=name_mismatch", status_code=303)

    forget_business_engine(slug)
    db.query(control_models.UserBusinessAccess).filter(control_models.UserBusinessAccess.business_id == biz.id).delete()
    db.delete(biz)
    db.commit()

    path = business_db_path(slug)
    try:
        import os
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

    if request.session.get("business_slug") == slug:
        remaining = db.query(control_models.Business).first()
        request.session["business_slug"] = remaining.slug if remaining else DEFAULT_BUSINESS_SLUG

    return RedirectResponse("/settings", status_code=303)
