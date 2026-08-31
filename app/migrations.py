from .database import (
    control_engine, ControlBase, ControlSessionLocal,
    get_business_engine, ensure_column,
)
from . import control_models
from .settings_helper import ensure_default_business


def run_migrations():
    ControlBase.metadata.create_all(bind=control_engine)
    ensure_column(control_engine, "businesses", "logo_filename", "VARCHAR")
    ensure_column(control_engine, "businesses", "abn", "VARCHAR")
    ensure_column(control_engine, "businesses", "address_line", "VARCHAR")
    ensure_column(control_engine, "businesses", "suburb", "VARCHAR")
    ensure_column(control_engine, "businesses", "state", "VARCHAR")
    ensure_column(control_engine, "businesses", "postcode", "VARCHAR")
    ensure_column(control_engine, "businesses", "phone", "VARCHAR")
    ensure_column(control_engine, "businesses", "email", "VARCHAR")
    ensure_column(control_engine, "businesses", "payment_bsb", "VARCHAR")
    ensure_column(control_engine, "businesses", "payment_account_number", "VARCHAR")
    ensure_column(control_engine, "businesses", "payment_account_name", "VARCHAR")
    ensure_column(control_engine, "businesses", "payment_payid", "VARCHAR")
    ensure_column(control_engine, "app_settings", "smtp_enabled", "BOOLEAN")
    ensure_column(control_engine, "app_settings", "smtp_host", "VARCHAR")
    ensure_column(control_engine, "app_settings", "smtp_port", "INTEGER")
    ensure_column(control_engine, "app_settings", "smtp_username", "VARCHAR")
    ensure_column(control_engine, "app_settings", "smtp_password", "VARCHAR")
    ensure_column(control_engine, "app_settings", "smtp_encryption", "VARCHAR")
    ensure_column(control_engine, "app_settings", "smtp_from_email", "VARCHAR")
    ensure_column(control_engine, "app_settings", "smtp_from_name", "VARCHAR")
    ensure_column(control_engine, "users", "theme", "VARCHAR")  # per-user theme preference
    ensure_column(control_engine, "users", "ocr_enabled", "BOOLEAN")  # NULL treated as enabled

    with ControlSessionLocal() as cdb:
        ensure_default_business(cdb)
        businesses = cdb.query(control_models.Business).all()
        slugs = [b.slug for b in businesses]

    for slug in slugs:
        engine = get_business_engine(slug)  # also runs Base.metadata.create_all for this business
        _run_per_business_migrations(engine)


def _run_per_business_migrations(engine):
    """Additive column migrations for a single business database. Safe to run
    on every startup — each step checks before acting."""
    ensure_column(engine, "income_line_items", "category_id", "INTEGER")
    ensure_column(engine, "expense_categories", "category_type", "VARCHAR")
    ensure_column(engine, "income_transactions", "invoice_number", "VARCHAR")
    ensure_column(engine, "income_transactions", "invoice_due_date", "DATE")
    ensure_column(engine, "uploaded_files", "ocr_processed", "BOOLEAN")
    ensure_column(engine, "uploaded_files", "ocr_date", "DATE")
    ensure_column(engine, "uploaded_files", "ocr_invoice_number", "VARCHAR")
    ensure_column(engine, "uploaded_files", "ocr_supplier_contact_id", "INTEGER")
    ensure_column(engine, "uploaded_files", "ocr_line_items_json", "TEXT")

    with engine.connect() as conn:
        conn.exec_driver_sql(
            "UPDATE expense_categories SET category_type = 'expense' WHERE category_type IS NULL"
        )

        # Seed the default payment methods once, the first time this business's
        # payment_methods table is empty.
        count = conn.exec_driver_sql("SELECT COUNT(*) FROM payment_methods").fetchone()[0]
        if count == 0:
            from .models import DEFAULT_PAYMENT_METHODS
            for name in DEFAULT_PAYMENT_METHODS:
                conn.exec_driver_sql("INSERT INTO payment_methods (name) VALUES (?)", (name,))

        conn.commit()
