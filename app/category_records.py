from datetime import date
from sqlalchemy.orm import Session

from . import models


def category_records(db: Session, category: models.ExpenseCategory, start: date = None, end: date = None):
    """Every income/expense line item tagged with this category, optionally
    bounded by a date range (None on either side means unbounded)."""
    rows = []

    if category.category_type == "income":
        q = db.query(models.IncomeLineItem).join(models.IncomeTransaction).filter(
            models.IncomeLineItem.category_id == category.id
        )
        if start:
            q = q.filter(models.IncomeTransaction.date >= start)
        if end:
            q = q.filter(models.IncomeTransaction.date <= end)
        for li in q.order_by(models.IncomeTransaction.date).all():
            t = li.transaction
            rows.append({
                "date": t.date, "record_type": "Income", "contact": t.contact.display_name if t.contact else None,
                "reference": t.reference, "description": li.description, "amount": float(li.amount),
                "url": f"/income/{t.id}/edit",
            })
    else:
        q = db.query(models.ExpenseLineItem).join(models.Expense).filter(
            models.ExpenseLineItem.category_id == category.id
        )
        if start:
            q = q.filter(models.Expense.date >= start)
        if end:
            q = q.filter(models.Expense.date <= end)
        for li in q.order_by(models.Expense.date).all():
            e = li.expense
            rows.append({
                "date": e.date, "record_type": "Expense", "contact": e.contact.display_name if e.contact else None,
                "reference": e.invoice_number, "description": li.description, "amount": float(li.amount),
                "url": f"/expenses/{e.id}/edit",
            })

        q2 = db.query(models.IncomeLineItem).join(models.IncomeTransaction).filter(
            models.IncomeLineItem.category_id == category.id
        )
        if start:
            q2 = q2.filter(models.IncomeTransaction.date >= start)
        if end:
            q2 = q2.filter(models.IncomeTransaction.date <= end)
        for li in q2.order_by(models.IncomeTransaction.date).all():
            t = li.transaction
            rows.append({
                "date": t.date, "record_type": "Income (fee)", "contact": t.contact.display_name if t.contact else None,
                "reference": t.reference, "description": li.description, "amount": -float(li.amount),
                "url": f"/income/{t.id}/edit",
            })

    rows.sort(key=lambda r: r["date"])
    return rows
