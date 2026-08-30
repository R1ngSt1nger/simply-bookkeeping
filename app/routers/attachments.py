import os
from fastapi import APIRouter, Request, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..auth import require_login
from ..files import business_attachments_dir
from ..settings_helper import active_business_slug

router = APIRouter(prefix="/attachments", tags=["attachments"])


@router.get("/{expense_id}")
def get_attachment(expense_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    expense = db.query(models.Expense).filter(models.Expense.id == expense_id).first()
    if not expense or not expense.attachment_path:
        return {"error": "No attachment found"}
    slug = active_business_slug(request)
    path = os.path.join(business_attachments_dir(slug), expense.attachment_path)
    if not os.path.exists(path):
        return {"error": "File missing on disk"}
    return FileResponse(path, filename=expense.attachment_filename, content_disposition_type="inline")
