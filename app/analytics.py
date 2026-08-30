from datetime import date
from sqlalchemy.orm import Session

from . import models
from .date_ranges import MONTH_ABBR, current_financial_year, shift_month


def income_monthly_totals(db: Session):
    """{(year, month): total_income_float} across all recorded transactions."""
    totals = {}
    for t in db.query(models.IncomeTransaction).all():
        key = (t.date.year, t.date.month)
        totals[key] = totals.get(key, 0) + float(t.total)
    return totals


def expense_monthly_totals(db: Session):
    """{(year, month): total_expense_float} across all recorded expenses."""
    totals = {}
    for e in db.query(models.Expense).all():
        key = (e.date.year, e.date.month)
        totals[key] = totals.get(key, 0) + float(e.total)
    return totals


def build_comparison_series(monthly_totals: dict, months_back: int, today: date = None):
    """For the last `months_back` calendar months (inclusive of the current one),
    build {labels, this_year, last_year, total} comparing this year to the same
    month last year — feeds the Income / Expenses dashboard bar charts."""
    today = today or date.today()
    labels, this_year, last_year = [], [], []
    total_this_year = 0

    y, m = shift_month(today.year, today.month, -(months_back - 1))
    for _ in range(months_back):
        labels.append(MONTH_ABBR[m])
        val_this = round(monthly_totals.get((y, m), 0), 2)
        val_last = round(monthly_totals.get((y - 1, m), 0), 2)
        this_year.append(val_this)
        last_year.append(val_last)
        total_this_year += val_this
        y, m = shift_month(y, m, 1)

    return {
        "labels": labels,
        "this_year": this_year,
        "last_year": last_year,
        "total": round(total_this_year, 2),
    }


def build_range_charts(monthly_totals: dict, today: date = None):
    """Precompute the 1/3/6-month comparison series so the frontend can switch
    between them instantly without a round trip."""
    return {
        "1": build_comparison_series(monthly_totals, 1, today),
        "3": build_comparison_series(monthly_totals, 3, today),
        "6": build_comparison_series(monthly_totals, 6, today),
    }


def build_financial_position_series(income_totals: dict, expense_totals: dict, today: date = None):
    """Cumulative Income / Expense / Net profit from the start of the current
    financial year up to the current month — feeds the Financial position chart."""
    today = today or date.today()
    fy_start, fy_end = current_financial_year(today)
    last_month = min(fy_end, today)

    labels, income_cum, expense_cum, net_cum = [], [], [], []
    running_income = 0
    running_expense = 0

    y, m = fy_start.year, fy_start.month
    while (y, m) <= (last_month.year, last_month.month):
        running_income += income_totals.get((y, m), 0)
        running_expense += expense_totals.get((y, m), 0)
        labels.append(MONTH_ABBR[m])
        income_cum.append(round(running_income, 2))
        expense_cum.append(round(running_expense, 2))
        net_cum.append(round(running_income - running_expense, 2))
        y, m = shift_month(y, m, 1)

    net_total = net_cum[-1] if net_cum else 0

    return {
        "labels": labels,
        "income": income_cum,
        "expense": expense_cum,
        "net": net_cum,
        "net_total": net_total,
    }
