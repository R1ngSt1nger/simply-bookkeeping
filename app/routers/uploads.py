import os
from datetime import datetime
from typing import List
from fastapi import APIRouter, Request, Depends, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..auth import require_login, require_write
from ..templates_config import render
from ..files import business_pending_dir, safe_filename
from ..settings_helper import active_business_slug
from .. import ocr

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.get("", response_class=HTMLResponse)
def list_uploads(request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    files = db.query(models.UploadedFile).order_by(models.UploadedFile.uploaded_at.desc()).all()
    return render(request, "uploads.html", {"request": request, "user": user, "files": files})


@router.post("")
async def upload_files(request: Request, files: List[UploadFile] = File(...), db: Session = Depends(get_db), user=Depends(require_write)):
    slug = active_business_slug(request)
    ocr_wanted = user.ocr_enabled is not False  # None (unset) or True both mean "on"
    ocr_extensions = ocr.IMAGE_EXTENSIONS | ocr.PDF_EXTENSIONS

    suppliers = None
    if ocr_wanted:
        suppliers = [(c.id, c.display_name) for c in db.query(models.Contact).filter(models.Contact.contact_type == "supplier").all()]

    for f in files:
        if not f.filename:
            continue
        stored_name = f"{datetime.utcnow().timestamp()}_{safe_filename(f.filename)}"
        path = os.path.join(business_pending_dir(slug), stored_name)
        content = await f.read()
        with open(path, "wb") as out:
            out.write(content)

        upload = models.UploadedFile(original_filename=f.filename, stored_filename=stored_name)

        ext = os.path.splitext(f.filename)[1].lower()
        if ocr_wanted and ext in ocr_extensions:
            try:
                text = ocr.extract_text(path)
                parsed = ocr.parse_invoice_text(text, suppliers)
                upload.ocr_processed = True
                upload.ocr_date = parsed["date"]
                upload.ocr_invoice_number = parsed["invoice_number"]
                upload.ocr_supplier_contact_id = parsed["supplier_contact_id"]
                upload.ocr_line_items_json = ocr.line_items_to_json(parsed["line_items"])
            except Exception:
                pass  # OCR is a nice-to-have — never let a recognition failure block the upload

        db.add(upload)
    db.commit()
    return RedirectResponse("/uploads", status_code=303)


@router.get("/{upload_id}/view")
def view_upload(upload_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    upload = db.query(models.UploadedFile).filter(models.UploadedFile.id == upload_id).first()
    if not upload:
        return {"error": "Upload not found"}
    slug = active_business_slug(request)
    path = os.path.join(business_pending_dir(slug), upload.stored_filename)
    if not os.path.exists(path):
        return {"error": "File missing on disk"}
    return FileResponse(path, filename=upload.original_filename)


@router.post("/{upload_id}/delete")
def delete_upload(upload_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_write)):
    upload = db.query(models.UploadedFile).filter(models.UploadedFile.id == upload_id).first()
    if upload:
        slug = active_business_slug(request)
        path = os.path.join(business_pending_dir(slug), upload.stored_filename)
        if os.path.exists(path):
            os.remove(path)
        db.delete(upload)
        db.commit()
    return RedirectResponse("/uploads", status_code=303)
