from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..auth import require_login
from ..templates_config import render
from ..category_records import category_records
from ..date_ranges import resolve_range, RANGE_LABELS

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_class=HTMLResponse)
def list_categories(request: Request, type: str = "all", db: Session = Depends(get_db), user=Depends(require_login)):
    query = db.query(models.ExpenseCategory)
    if type in ("income", "expense"):
        query = query.filter(models.ExpenseCategory.category_type == type)
    categories = query.order_by(models.ExpenseCategory.name.asc()).all()
    return render(request, "categories.html", {
        "request": request, "user": user, "categories": categories, "selected_type": type,
    })


@router.get("/{category_id}", response_class=HTMLResponse)
def category_detail(
    category_id: int, request: Request,
    range: str = "all", custom_start: str = "", custom_end: str = "",
    db: Session = Depends(get_db), user=Depends(require_login),
):
    category = db.query(models.ExpenseCategory).filter(models.ExpenseCategory.id == category_id).first()
    if not category:
        return RedirectResponse("/categories", status_code=303)
    bounds = resolve_range(range, custom_start=custom_start, custom_end=custom_end)
    start, end = bounds if bounds else (None, None)
    rows = category_records(db, category, start, end)
    total = sum(r["amount"] for r in rows)
    return render(request, "category_detail.html", {
        "request": request, "user": user, "category": category, "rows": rows, "total": total,
        "selected_range": range, "range_labels": RANGE_LABELS,
        "custom_start": custom_start, "custom_end": custom_end,
    })
