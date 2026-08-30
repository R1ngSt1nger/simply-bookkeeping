import os
import re

BASE_ATTACHMENTS_DIR = "/data/attachments"
os.makedirs(BASE_ATTACHMENTS_DIR, exist_ok=True)


def safe_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]", "", name)
    return name.strip().replace(" ", "_")


def business_attachments_dir(slug: str) -> str:
    """Attachments are scoped per-business so files never mix between businesses
    and can be cleanly removed if a business is deleted."""
    path = os.path.join(BASE_ATTACHMENTS_DIR, slug)
    os.makedirs(path, exist_ok=True)
    return path


def business_pending_dir(slug: str) -> str:
    path = os.path.join(business_attachments_dir(slug), "pending")
    os.makedirs(path, exist_ok=True)
    return path


def business_logo_path(slug: str, logo_filename: str) -> str:
    return os.path.join(business_attachments_dir(slug), logo_filename)
