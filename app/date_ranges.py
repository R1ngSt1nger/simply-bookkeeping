from datetime import date, timedelta

MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

RANGE_LABELS = {
    "all": "All time",
    "this_month": "This month",
    "last_month": "Last month",
    "last_3_months": "Last 3 months",
    "this_fy": "This financial year",
    "last_fy": "Last financial year",
    "custom": "Custom range…",
}


def month_range(year: int, month: int):
    """Return (start, end) date bounds for a single calendar month."""
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def current_financial_year(d: date = None):
    """Australian financial year: 1 Jul - 30 Jun."""
    d = d or date.today()
    if d.month >= 7:
        return date(d.year, 7, 1), date(d.year + 1, 6, 30)
    return date(d.year - 1, 7, 1), date(d.year, 6, 30)


def shift_month(year: int, month: int, delta: int):
    """Shift (year, month) by delta months (can be negative)."""
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def resolve_range(key: str, today: date = None, custom_start: str = None, custom_end: str = None):
    """Return (start, end) date bounds for a named range key, or None for 'all'/unrecognised."""
    today = today or date.today()

    if key == "custom":
        try:
            start = date.fromisoformat(custom_start) if custom_start else None
            end = date.fromisoformat(custom_end) if custom_end else None
        except ValueError:
            return None
        if start and end:
            return (start, end) if start <= end else (end, start)
        return None

    if key == "this_month":
        return month_range(today.year, today.month)

    if key == "last_month":
        y, m = shift_month(today.year, today.month, -1)
        return month_range(y, m)

    if key == "last_3_months":
        y, m = shift_month(today.year, today.month, -2)
        start, _ = month_range(y, m)
        _, end = month_range(today.year, today.month)
        return start, end

    if key == "this_fy":
        return current_financial_year(today)

    if key == "last_fy":
        fy_start, _ = current_financial_year(today)
        return date(fy_start.year - 1, 7, 1), date(fy_start.year, 6, 30)

    return None  # "all" or unrecognised — no filter applied
