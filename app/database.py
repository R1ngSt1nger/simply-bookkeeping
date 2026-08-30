import os
import re
import secrets
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from fastapi import Request

DATA_DIR = "/data/db"
BUSINESSES_DIR = os.path.join(DATA_DIR, "businesses")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BUSINESSES_DIR, exist_ok=True)

DEFAULT_BUSINESS_SLUG = "default"

# Two separate declarative bases: one for the small control database (logins,
# global settings, the business registry), one for each per-business database
# (contacts, categories, income, expenses, uploads).
ControlBase = declarative_base()
Base = declarative_base()

# ---------- Control database (single file, always present) ----------

CONTROL_DB_PATH = os.path.join(DATA_DIR, "control.db")
control_engine = create_engine(f"sqlite:///{CONTROL_DB_PATH}", connect_args={"check_same_thread": False})
ControlSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=control_engine)


def get_control_db():
    db = ControlSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- Per-business databases (one SQLite file per business, lazily opened) ----------

_business_engines = {}
_business_sessionmakers = {}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "business"


def business_db_path(slug: str) -> str:
    return os.path.join(BUSINESSES_DIR, f"{slug}.db")


def get_business_engine(slug: str):
    if slug not in _business_engines:
        path = business_db_path(slug)
        engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        _business_engines[slug] = engine
        _business_sessionmakers[slug] = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return _business_engines[slug]


def get_business_sessionmaker(slug: str):
    get_business_engine(slug)
    return _business_sessionmakers[slug]


def dispose_all_engines():
    """Close every open database connection — used before an import/restore
    overwrites the underlying files."""
    control_engine.dispose()
    for engine in _business_engines.values():
        engine.dispose()
    _business_engines.clear()
    _business_sessionmakers.clear()


def forget_business_engine(slug: str):
    """Drop a cached engine (e.g. after deleting that business) so a stale
    connection to a now-deleted file isn't reused."""
    engine = _business_engines.pop(slug, None)
    if engine:
        engine.dispose()
    _business_sessionmakers.pop(slug, None)


def get_db(request: Request):
    """Business-scoped session — reads which business is active from the
    logged-in user's browser session, enforcing per-user access (an accountant
    can only reach a business they've been explicitly granted)."""
    from .settings_helper import resolve_active_business_slug  # lazy import — avoids a circular import at module load

    slug = resolve_active_business_slug(request)

    if not slug:
        # The user has no accessible businesses at all — serve a genuinely
        # empty, throwaway in-memory database rather than ever touching a
        # real business's data.
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
            engine.dispose()
        return

    SessionLocal = get_business_sessionmaker(slug)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_column(engine, table: str, column: str, coltype: str):
    """Lightweight auto-migration: adds a column if it doesn't already exist.
    We're on plain SQLite without Alembic, so this covers simple additive changes."""
    with engine.connect() as conn:
        existing = [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()]
        if column not in existing:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            conn.commit()


def get_or_create_secret_key() -> str:
    """Session signing key. Persisted on disk so it survives container restarts/rebuilds
    without needing an env var — set SECRET_KEY explicitly to override this."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    path = os.path.join(DATA_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    os.chmod(path, 0o600)
    return key
