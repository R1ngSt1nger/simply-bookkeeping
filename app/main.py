import os
from datetime import date, datetime

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from .database import get_db, get_control_db, ControlSessionLocal, get_or_create_secret_key, DEFAULT_BUSINESS_SLUG
from . import models
from . import control_models
from .auth import hash_password, verify_password, get_current_user
from .templates_config import render, BASE_DIR
from . import analytics
from .settings_helper import sso_login_context, ensure_default_business
from .migrations import run_migrations

from .routers import income, expenses, categories, reports, attachments, profile, settings, sso, contacts, uploads, businesses, backup, password_reset

run_migrations()

app = FastAPI(title="Simply Bookkeeping")

SECRET_KEY = get_or_create_secret_key()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "app", "static")), name="static")


@app.middleware("http")
async def require_setup(request: Request, call_next):
    """If no users exist yet, this is a fresh install — send everything to /setup
    until the owner account is created there."""
    if request.url.path.startswith("/setup") or request.url.path.startswith("/static"):
        return await call_next(request)
    db = ControlSessionLocal()
    try:
        no_users = db.query(control_models.User).count() == 0
    finally:
        db.close()
    if no_users:
        return RedirectResponse("/setup", status_code=303)
    return await call_next(request)


# ---------- First-run setup ----------

@app.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, db: Session = Depends(get_control_db)):
    if db.query(control_models.User).count() > 0:
        return RedirectResponse("/login", status_code=303)
    return render(request, "setup.html", {"error": None})


@app.post("/setup", response_class=HTMLResponse)
def setup_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_control_db),
):
    if db.query(control_models.User).count() > 0:
        return RedirectResponse("/login", status_code=303)

    username = username.strip()
    if not username or not password:
        return render(request, "setup.html", {"error": "Username and password are required."}, status_code=400)
    if password != confirm_password:
        return render(request, "setup.html", {"error": "Passwords don't match."}, status_code=400)

    user = control_models.User(
        username=username,
        password_hash=hash_password(password),
        role="owner",
        display_name=username,
    )
    db.add(user)
    ensure_default_business(db)
    db.commit()
    db.refresh(user)

    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = user.role
    request.session["business_slug"] = DEFAULT_BUSINESS_SLUG
    return RedirectResponse("/", status_code=303)


# ---------- Auth routes ----------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_control_db)):
    if get_current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", sso_login_context(db))


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_control_db)):
    user = db.query(control_models.User).filter(control_models.User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return render(request, "login.html", sso_login_context(db, error="Those details don't match any account."), status_code=401)
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = user.role
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------- Dashboard ----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), control_db: Session = Depends(get_control_db)):
    user = get_current_user(request, control_db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    today = date.today()

    income_monthly = analytics.income_monthly_totals(db)
    expense_monthly = analytics.expense_monthly_totals(db)

    income_charts = analytics.build_range_charts(income_monthly, today)
    expense_charts = analytics.build_range_charts(expense_monthly, today)
    position_chart = analytics.build_financial_position_series(income_monthly, expense_monthly, today)

    recent_income = db.query(models.IncomeTransaction).order_by(models.IncomeTransaction.date.desc()).limit(5).all()
    recent_expenses = db.query(models.Expense).order_by(models.Expense.date.desc()).limit(5).all()

    hour = datetime.now().hour
    if hour < 12:
        greeting_word = "Good morning"
    elif hour < 17:
        greeting_word = "Good afternoon"
    else:
        greeting_word = "Good evening"

    return render(request, "dashboard.html", {
        "request": request,
        "user": user,
        "greeting_word": greeting_word,
        "income_charts": income_charts,
        "expense_charts": expense_charts,
        "position_chart": position_chart,
        "recent_income": recent_income,
        "recent_expenses": recent_expenses,
    })


app.include_router(income.router)
app.include_router(expenses.router)
app.include_router(categories.router)
app.include_router(reports.router)
app.include_router(attachments.router)
app.include_router(profile.router)
app.include_router(settings.router)
app.include_router(sso.router)
app.include_router(contacts.router)
app.include_router(uploads.router)
app.include_router(businesses.router)
app.include_router(backup.router)
app.include_router(password_reset.router)


@app.exception_handler(HTTPException)
async def friendly_http_exception(request: Request, exc: HTTPException):
    if exc.status_code == 403:
        return HTMLResponse(
            "<div style='font-family:sans-serif;max-width:32rem;margin:15vh auto;text-align:center;color:#173B3D'>"
            "<h2>Read-only access</h2>"
            f"<p style='color:#666'>{exc.detail}</p>"
            "<p><a href='/' style='color:#B98B2E'>← Back to dashboard</a></p></div>",
            status_code=403,
        )
    if exc.headers and exc.headers.get("Location"):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    raise exc
