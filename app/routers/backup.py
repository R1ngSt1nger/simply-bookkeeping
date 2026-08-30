import io
import os
import shutil
import tempfile
import zipfile
from datetime import date

from fastapi import APIRouter, UploadFile, File, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse, HTMLResponse

from ..auth import require_owner
from ..database import DATA_DIR, dispose_all_engines
from ..files import BASE_ATTACHMENTS_DIR as ATTACHMENTS_DIR

router = APIRouter(prefix="/settings/backup", tags=["backup"])


@router.get("/export")
def export_backup(user=Depends(require_owner)):
    """A complete backup — every business's database, the login/settings
    database, and all attachments — zipped up for download."""
    buf = io.BytesIO()
    root = os.path.dirname(DATA_DIR)  # parent shared by data/ and attachments/

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for base_dir in (DATA_DIR, ATTACHMENTS_DIR):
            for dirpath, _dirnames, filenames in os.walk(base_dir):
                for fname in filenames:
                    full = os.path.join(dirpath, fname)
                    arcname = os.path.relpath(full, root)
                    zf.write(full, arcname)

    buf.seek(0)
    filename = f"simply-bookkeeping-backup-{date.today().isoformat()}.zip"
    return StreamingResponse(buf, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename={filename}"
    })


def _replace_dir_contents(target_dir: str, source_dir: str):
    """Empty target_dir (without removing the directory itself, since it may
    be a Docker bind mount) then move everything from source_dir into it."""
    for entry in os.listdir(target_dir):
        full = os.path.join(target_dir, entry)
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.remove(full)
            except OSError:
                pass
    for entry in os.listdir(source_dir):
        shutil.move(os.path.join(source_dir, entry), os.path.join(target_dir, entry))


def _restart_app():
    import time
    time.sleep(1.5)
    os._exit(1)  # let the container's restart policy bring it back up cleanly


@router.post("/import")
async def import_backup(background_tasks: BackgroundTasks, file: UploadFile = File(...), user=Depends(require_owner)):
    content = await file.read()

    try:
        buf = io.BytesIO(content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            db_folder = "db" if any(n.startswith("db/") for n in names) else (
                "data" if any(n.startswith("data/") for n in names) else None
            )
            if not db_folder:
                return HTMLResponse(
                    "<div style='font-family:sans-serif;max-width:32rem;margin:15vh auto;text-align:center;color:#173B3D'>"
                    "<h2>That doesn't look right</h2>"
                    "<p style='color:#666'>This doesn't look like a Simply Bookkeeping backup file.</p>"
                    "<p><a href='/settings' style='color:#B98B2E'>← Back to Settings</a></p></div>",
                    status_code=400,
                )
            extract_dir = tempfile.mkdtemp()
            zf.extractall(extract_dir)
    except zipfile.BadZipFile:
        return HTMLResponse(
            "<div style='font-family:sans-serif;max-width:32rem;margin:15vh auto;text-align:center;color:#173B3D'>"
            "<h2>Invalid file</h2>"
            "<p style='color:#666'>That file isn't a valid ZIP archive.</p>"
            "<p><a href='/settings' style='color:#B98B2E'>← Back to Settings</a></p></div>",
            status_code=400,
        )

    dispose_all_engines()

    extracted_data = os.path.join(extract_dir, db_folder)  # "db" for current backups, "data" for pre-upgrade ones
    extracted_attachments = os.path.join(extract_dir, "attachments")

    if os.path.isdir(extracted_data):
        _replace_dir_contents(DATA_DIR, extracted_data)
    if os.path.isdir(extracted_attachments):
        _replace_dir_contents(ATTACHMENTS_DIR, extracted_attachments)

    shutil.rmtree(extract_dir, ignore_errors=True)

    background_tasks.add_task(_restart_app)

    return HTMLResponse(
        "<div style='font-family:sans-serif;max-width:32rem;margin:15vh auto;text-align:center;color:#173B3D'>"
        "<h2>Restore complete</h2>"
        "<p style='color:#666'>The app is restarting to load the restored data — this takes a few seconds. "
        "Refresh the page shortly.</p></div>"
    )
