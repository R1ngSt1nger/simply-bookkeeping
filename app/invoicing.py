from sqlalchemy.orm import Session
from . import models


def claim_invoice_number(db: Session) -> str:
    """Atomically claim the next invoice number for this business. Numbers are
    never reused, even if the record they were claimed for is later deleted."""
    counter = db.query(models.InvoiceCounter).first()
    if not counter:
        counter = models.InvoiceCounter(next_number=1)
        db.add(counter)
        db.flush()
    number = counter.next_number
    counter.next_number = number + 1
    return f"INV-{number:04d}"


def claim_quote_number(db: Session) -> str:
    """Same idea as claim_invoice_number, but for quotes — a separate counter
    and prefix so the two sequences never collide or share numbering."""
    counter = db.query(models.QuoteCounter).first()
    if not counter:
        counter = models.QuoteCounter(next_number=1)
        db.add(counter)
        db.flush()
    number = counter.next_number
    counter.next_number = number + 1
    return f"QUO-{number:04d}"
