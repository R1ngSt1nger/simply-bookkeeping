import io
from datetime import date, datetime
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..auth import require_login
from ..templates_config import render
from ..settings_helper import get_active_business, active_theme_key
from ..themes import get_theme, MONEY_POSITIVE, MONEY_NEGATIVE
from ..category_records import category_records

router = APIRouter(prefix="/reports", tags=["reports"])


def financial_year_options(today: date, count: int = 6):
    """Return list of (label, start_iso, end_iso) for the last `count` Australian financial years."""
    if today.month >= 7:
        start_year = today.year
    else:
        start_year = today.year - 1
    options = []
    for i in range(count):
        y = start_year - i
        start = date(y, 7, 1)
        end = date(y + 1, 6, 30)
        label = f"FY {y}/{str(y + 1)[-2:]}"
        options.append((label, start.isoformat(), end.isoformat()))
    return options


def shift_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        # 29 Feb on a non-leap target year
        return d.replace(year=d.year - years, day=28)


def last_year_range(start: date, end: date):
    return shift_year(start, 1), shift_year(end, 1)


def _category_breakdown(db: Session, start: date, end: date):
    """Bucket every income and expense line item into Income / Expense sections by
    the *category's* type — so a negative income line tagged with an expense
    category (e.g. OTA Commission) shows up as a positive figure under Expenses."""
    income_lines = db.query(models.IncomeLineItem).join(models.IncomeTransaction).filter(
        models.IncomeTransaction.date >= start, models.IncomeTransaction.date <= end
    ).all()
    expense_lines = db.query(models.ExpenseLineItem).join(models.Expense).filter(
        models.Expense.date >= start, models.Expense.date <= end
    ).all()

    income_bucket = {}
    expense_bucket = {}

    for li in income_lines:
        if li.category and li.category.category_type == "expense":
            key = li.category_id
            b = expense_bucket.setdefault(key, {"name": li.category.name, "amount": 0.0})
            b["amount"] += -float(li.amount)
        else:
            key = li.category_id
            name = li.category.name if li.category else "Uncategorised"
            b = income_bucket.setdefault(key, {"name": name, "amount": 0.0})
            b["amount"] += float(li.amount)

    for li in expense_lines:
        key = li.category_id
        name = li.category.name if li.category else "Uncategorised"
        b = expense_bucket.setdefault(key, {"name": name, "amount": 0.0})
        b["amount"] += float(li.amount)

    return income_bucket, expense_bucket


def _compute_variance(actual: float, last_year: float, section: str):
    variance = actual - last_year
    pct = (variance / abs(last_year) * 100) if last_year else None
    if variance > 0.005:
        direction = "up"
    elif variance < -0.005:
        direction = "down"
    else:
        direction = "flat"
    favorable = (variance >= 0) if section == "income" else (variance <= 0)
    return {"amount": variance, "pct": pct, "direction": direction, "favorable": favorable}


def _merge_rows(actual_bucket: dict, last_bucket: dict, section: str):
    rows = []
    for key in set(actual_bucket) | set(last_bucket):
        name = (actual_bucket.get(key) or last_bucket.get(key))["name"]
        actual = actual_bucket.get(key, {"amount": 0.0})["amount"]
        last = last_bucket.get(key, {"amount": 0.0})["amount"]
        rows.append({
            "category_id": key, "name": name, "actual": actual, "last_year": last,
            "variance": _compute_variance(actual, last, section),
        })
    rows.sort(key=lambda r: r["name"])
    return rows


def compute_variance_report(db: Session, start: date, end: date):
    last_start, last_end = last_year_range(start, end)

    income_actual, expense_actual = _category_breakdown(db, start, end)
    income_last, expense_last = _category_breakdown(db, last_start, last_end)

    income_rows = _merge_rows(income_actual, income_last, "income")
    expense_rows = _merge_rows(expense_actual, expense_last, "expense")

    total_income_actual = sum(r["actual"] for r in income_rows)
    total_income_last = sum(r["last_year"] for r in income_rows)
    total_expense_actual = sum(r["actual"] for r in expense_rows)
    total_expense_last = sum(r["last_year"] for r in expense_rows)

    net_actual = total_income_actual - total_expense_actual
    net_last = total_income_last - total_expense_last

    return {
        "income_rows": income_rows,
        "expense_rows": expense_rows,
        "total_income": {
            "actual": total_income_actual, "last_year": total_income_last,
            "variance": _compute_variance(total_income_actual, total_income_last, "income"),
        },
        "total_expense": {
            "actual": total_expense_actual, "last_year": total_expense_last,
            "variance": _compute_variance(total_expense_actual, total_expense_last, "expense"),
        },
        "net_profit": {
            "actual": net_actual, "last_year": net_last,
            "variance": _compute_variance(net_actual, net_last, "income"),
        },
        "last_start": last_start, "last_end": last_end,
    }


@router.get("", response_class=HTMLResponse)
def report_view(request: Request, start: str = None, end: str = None, db: Session = Depends(get_db), user=Depends(require_login)):
    today = date.today()
    fy_options = financial_year_options(today)

    if start and end:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
    else:
        # default to current FY
        _, start_iso, end_iso = fy_options[0]
        start_date = datetime.strptime(start_iso, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_iso, "%Y-%m-%d").date()

    data = compute_variance_report(db, start_date, end_date)

    return render(request, "reports.html", {
        "request": request, "user": user, "start": start_date.isoformat(), "end": end_date.isoformat(),
        "fy_options": fy_options, **data,
    })


@router.get("/category/{category_id}", response_class=HTMLResponse)
def category_drilldown(category_id: int, start: str, end: str, request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    category = db.query(models.ExpenseCategory).filter(models.ExpenseCategory.id == category_id).first()
    if not category:
        return RedirectResponse(f"/reports?start={start}&end={end}", status_code=303)

    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    end_date = datetime.strptime(end, "%Y-%m-%d").date()

    rows = category_records(db, category, start_date, end_date)
    total = sum(r["amount"] for r in rows)

    return render(request, "report_category.html", {
        "request": request, "user": user, "category": category, "rows": rows, "total": total,
        "start": start, "end": end,
    })


@router.get("/pdf")
def report_pdf(start: str, end: str, request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas

    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    end_date = datetime.strptime(end, "%Y-%m-%d").date()
    data = compute_variance_report(db, start_date, end_date)
    biz_name = get_active_business(request).name
    active_theme = get_theme(active_theme_key(request))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    ink = colors.HexColor(active_theme["ink"])
    rust = colors.HexColor(MONEY_NEGATIVE)
    green = colors.HexColor(MONEY_POSITIVE)
    grey = colors.HexColor("#8A8D85")

    col_x = {"name": 20 * mm, "actual": 120 * mm, "last": 150 * mm, "var": 175 * mm, "pct": 195 * mm}

    def money_str(v):
        neg = v < 0
        s = f"${abs(v):,.2f}"
        return f"({s})" if neg else s

    def draw_row(y, name, actual, last, variance, bold=False, indent=False):
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, 9.5 if not bold else 10)
        c.setFillColor(ink)
        c.drawString(col_x["name"] + (5 * mm if indent else 0), y, name)
        c.drawRightString(col_x["actual"], y, money_str(actual))
        c.drawRightString(col_x["last"], y, money_str(last))
        var_color = green if variance["favorable"] else rust
        if not bold:
            c.setFillColor(var_color)
        c.drawRightString(col_x["var"], y, money_str(variance["amount"]))
        pct_text = f"{variance['pct']:.1f}%" if variance["pct"] is not None else "-"
        c.drawRightString(col_x["pct"], y, pct_text)
        c.setFillColor(ink)

    y = height - 20 * mm
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(ink)
    c.drawString(20 * mm, y, biz_name)
    y -= 9 * mm
    c.setFont("Helvetica-Bold", 13)
    c.drawString(20 * mm, y, "Profit and loss report")
    y -= 6 * mm
    c.setFont("Helvetica", 10)
    c.setFillColor(grey)
    c.drawString(20 * mm, y, f"{start_date.strftime('%d %b %Y')} - {end_date.strftime('%d %b %Y')}")
    c.setFillColor(ink)
    y -= 10 * mm

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(grey)
    c.drawRightString(col_x["actual"], y, "Actual")
    c.drawRightString(col_x["last"], y, "Last Year")
    c.drawRightString(col_x["var"], y, "Variance $")
    c.drawRightString(col_x["pct"], y, "Variance %")
    c.setFillColor(ink)
    y -= 5 * mm
    c.setStrokeColor(colors.HexColor(active_theme["line"]))
    c.line(20 * mm, y, width - 20 * mm, y)
    y -= 6 * mm

    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(20 * mm, y, "Income")
    y -= 6 * mm
    for row in data["income_rows"]:
        draw_row(y, row["name"], row["actual"], row["last_year"], row["variance"], indent=True)
        y -= 5.5 * mm
    draw_row(y, "Total Income", data["total_income"]["actual"], data["total_income"]["last_year"], data["total_income"]["variance"], bold=True)
    y -= 8 * mm

    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(20 * mm, y, "Expenses")
    y -= 6 * mm
    for row in data["expense_rows"]:
        draw_row(y, row["name"], row["actual"], row["last_year"], row["variance"], indent=True)
        y -= 5.5 * mm
    draw_row(y, "Total Expenses", data["total_expense"]["actual"], data["total_expense"]["last_year"], data["total_expense"]["variance"], bold=True)
    y -= 8 * mm

    c.setStrokeColor(ink)
    c.setLineWidth(1.2)
    c.line(20 * mm, y, width - 20 * mm, y)
    y -= 7 * mm
    c.setFont("Helvetica-Bold", 12)
    draw_row(y, "Net Profit", data["net_profit"]["actual"], data["net_profit"]["last_year"], data["net_profit"]["variance"], bold=True)

    c.setFont("Helvetica", 8)
    c.setFillColor(grey)
    c.drawString(20 * mm, 12 * mm, f"{biz_name} | Profit and loss report | Generated {date.today().strftime('%d %b %Y')}")

    c.showPage()
    c.save()
    buf.seek(0)

    filename = f"profit_loss_{start_date}_{end_date}.pdf"
    return StreamingResponse(buf, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename={filename}"
    })
