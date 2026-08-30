from sqlalchemy import (
    Column, Integer, String, Numeric, Date, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from .database import Base


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True)
    contact_type = Column(String, nullable=False)  # "customer" or "supplier"
    designation = Column(String, nullable=False)  # "company" or "individual"

    company_name = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    contact_person = Column(String, nullable=True)  # company only
    website = Column(String, nullable=True)  # company only

    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)

    street_address = Column(String, nullable=True)
    suburb = Column(String, nullable=True)
    state = Column(String, nullable=True)
    postcode = Column(String, nullable=True)

    notes = Column(Text, nullable=True)

    display_name = Column(String, nullable=False)  # denormalised for sorting/searching
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


DEFAULT_PAYMENT_METHODS = ["Cash", "Bank Transfer", "Credit Card"]


class IncomeTransaction(Base):
    __tablename__ = "income_transactions"

    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    reference = Column(String, nullable=True)  # e.g. guest name / booking ref
    notes = Column(Text, nullable=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True)
    invoice_number = Column(String, nullable=True)  # claimed once, on first "Generate Invoice"
    invoice_due_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    line_items = relationship(
        "IncomeLineItem", back_populates="transaction",
        cascade="all, delete-orphan", order_by="IncomeLineItem.id"
    )
    payments = relationship(
        "IncomePayment", back_populates="transaction",
        cascade="all, delete-orphan", order_by="IncomePayment.date"
    )
    contact = relationship("Contact")

    @property
    def total(self):
        return sum((li.amount for li in self.line_items), start=0)

    @property
    def amount_received(self):
        return sum((p.amount for p in self.payments), start=0)

    @property
    def balance_due(self):
        return self.total - self.amount_received


class IncomePayment(Base):
    __tablename__ = "income_payments"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("income_transactions.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(String, nullable=False, default="bank_transfer")

    transaction = relationship("IncomeTransaction", back_populates="payments")


class IncomeLineItem(Base):
    __tablename__ = "income_line_items"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("income_transactions.id"), nullable=False)
    description = Column(String, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)  # positive = income line, negative = fee/deduction
    category_id = Column(Integer, ForeignKey("expense_categories.id"), nullable=True)

    transaction = relationship("IncomeTransaction", back_populates="line_items")
    category = relationship("ExpenseCategory")


class ExpenseCategory(Base):
    """Shared category list for both Income and Expense line items, distinguished by category_type."""
    __tablename__ = "expense_categories"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    category_type = Column(String, nullable=False, default="expense")  # "income" or "expense"
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Expense(Base):
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    invoice_number = Column(String, nullable=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True)  # the "Supplier"
    description = Column(Text, nullable=True)  # notes
    attachment_filename = Column(String, nullable=True)  # original display name
    attachment_path = Column(String, nullable=True)  # path on disk
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    line_items = relationship(
        "ExpenseLineItem", back_populates="expense",
        cascade="all, delete-orphan", order_by="ExpenseLineItem.id"
    )
    payments = relationship(
        "ExpensePayment", back_populates="expense",
        cascade="all, delete-orphan", order_by="ExpensePayment.date"
    )
    contact = relationship("Contact")

    @property
    def total(self):
        return sum((li.amount for li in self.line_items), start=0)

    @property
    def amount_paid(self):
        return sum((p.amount for p in self.payments), start=0)

    @property
    def balance_due(self):
        return self.total - self.amount_paid


class ExpensePayment(Base):
    __tablename__ = "expense_payments"

    id = Column(Integer, primary_key=True)
    expense_id = Column(Integer, ForeignKey("expenses.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(String, nullable=False, default="bank_transfer")

    expense = relationship("Expense", back_populates="payments")


class ExpenseLineItem(Base):
    __tablename__ = "expense_line_items"

    id = Column(Integer, primary_key=True)
    expense_id = Column(Integer, ForeignKey("expenses.id"), nullable=False)
    description = Column(String, nullable=False)
    category_id = Column(Integer, ForeignKey("expense_categories.id"), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)

    expense = relationship("Expense", back_populates="line_items")
    category = relationship("ExpenseCategory")


class UploadedFile(Base):
    """Receipts/documents dropped in the Uploads holding area, before being
    assigned to a specific expense record."""
    __tablename__ = "uploaded_files"

    id = Column(Integer, primary_key=True)
    original_filename = Column(String, nullable=False)
    stored_filename = Column(String, nullable=False)  # filename within attachments/pending
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class InvoiceCounter(Base):
    """Singleton row (id=1) — a strictly-increasing counter for this business's
    invoice numbers, so a number is never reused even if a record is later deleted."""
    __tablename__ = "invoice_counter"

    id = Column(Integer, primary_key=True)
    next_number = Column(Integer, nullable=False, default=1)


class PaymentMethod(Base):
    """User-editable list of payment methods (Cash, Bank Transfer, etc.) offered
    in the Payments Received/Made dropdown on Income and Expense records. Payment
    rows store the method name as plain text at the time it was recorded, so
    renaming or deleting an option here never changes historical records."""
    __tablename__ = "payment_methods"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
