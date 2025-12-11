"""
SQLAlchemy models for all database tables.
Comprehensive schema for storing credit card statement data.
"""
from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional
from sqlalchemy import (
    Column, String, Integer, Date, DateTime, Numeric, Boolean, Text,
    ForeignKey, Index, UniqueConstraint, CheckConstraint, func
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.dialects.sqlite import JSON
from app.database import Base


class Statement(Base):
    """
    Credit card statement model.
    Stores statement-level metadata and financial summary.
    """
    __tablename__ = "statements"

    # Primary Key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # File Information
    filename: Mapped[str] = mapped_column(String(500), unique=True, nullable=False, index=True)
    pdf_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # SHA-256
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    password: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # Plaintext as requested

    # Bank & Account Information
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    account_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    card_type: Mapped[Optional[str]] = mapped_column(String(50))
    cardholder_name: Mapped[Optional[str]] = mapped_column(String(200))
    member_since: Mapped[Optional[int]] = mapped_column(Integer)

    # Statement Dates
    statement_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    statement_period_from: Mapped[date] = mapped_column(Date, nullable=False)
    statement_period_to: Mapped[date] = mapped_column(Date, nullable=False)
    payment_due_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    statement_number: Mapped[Optional[str]] = mapped_column(String(50))
    billing_cycle: Mapped[Optional[int]] = mapped_column(Integer)

    # Financial Summary - Balances
    previous_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    payments_credits: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    purchases: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    cash_advances: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    fees_charged: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    interest_charged: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    adjustments: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    new_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))

    # Payment Information
    total_amount_due: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    minimum_payment_due: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))

    # Credit Information
    credit_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    available_credit: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    cash_advance_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    credit_utilization_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    # Rewards/Points
    rewards_opening: Mapped[Optional[int]] = mapped_column(Integer)
    rewards_earned: Mapped[Optional[int]] = mapped_column(Integer)
    rewards_redeemed: Mapped[Optional[int]] = mapped_column(Integer)
    rewards_closing: Mapped[Optional[int]] = mapped_column(Integer)
    rewards_value_inr: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))

    # Currency
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    # Timestamps
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    transactions: Mapped[List["Transaction"]] = relationship("Transaction", back_populates="statement", cascade="all, delete-orphan")
    fees: Mapped[List["Fee"]] = relationship("Fee", back_populates="statement", cascade="all, delete-orphan")
    interest_charges: Mapped[List["InterestCharge"]] = relationship("InterestCharge", back_populates="statement", cascade="all, delete-orphan")
    rewards_summary: Mapped[Optional["RewardsSummary"]] = relationship("RewardsSummary", back_populates="statement", cascade="all, delete-orphan", uselist=False)
    category_summaries: Mapped[List["CategorySummary"]] = relationship("CategorySummary", back_populates="statement", cascade="all, delete-orphan")
    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="statement", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index('idx_statement_account_date', 'account_number', 'statement_date'),
    )

    def __repr__(self):
        return f"<Statement(id={self.id}, bank={self.bank_name}, date={self.statement_date})>"


class Transaction(Base):
    """
    Individual transaction model.
    Stores comprehensive transaction data for analysis.
    """
    __tablename__ = "transactions"

    # Primary Key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Foreign Key
    statement_id: Mapped[int] = mapped_column(Integer, ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, index=True)

    # Account Reference
    account_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Transaction Dates
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    posting_date: Mapped[Optional[date]] = mapped_column(Date)

    # Description
    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    description_cleaned: Mapped[Optional[str]] = mapped_column(Text)

    # Merchant Information
    merchant_name: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    merchant_category: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    merchant_city: Mapped[Optional[str]] = mapped_column(String(100))
    merchant_state: Mapped[Optional[str]] = mapped_column(String(100))
    merchant_country: Mapped[str] = mapped_column(String(2), default="IN")

    # Amount Information
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    # Foreign Currency Transaction
    foreign_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    foreign_currency: Mapped[Optional[str]] = mapped_column(String(3))
    exchange_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    # Transaction Type
    transaction_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    debit_credit: Mapped[str] = mapped_column(String(1))  # D or C

    # Reference Information
    reference_number: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    authorization_code: Mapped[Optional[str]] = mapped_column(String(50))
    card_last_four: Mapped[Optional[str]] = mapped_column(String(4))

    # Transaction Flags
    is_international: Mapped[bool] = mapped_column(Boolean, default=False)
    is_emi: Mapped[bool] = mapped_column(Boolean, default=False)
    emi_tenure: Mapped[Optional[int]] = mapped_column(Integer)
    emi_month: Mapped[Optional[int]] = mapped_column(Integer)

    # Rewards
    rewards_earned: Mapped[int] = mapped_column(Integer, default=0)
    rewards_multiplier: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 1))

    # User Classification
    category_manual: Mapped[Optional[str]] = mapped_column(String(100))
    tags: Mapped[Optional[dict]] = mapped_column(JSON)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Recurring Transaction
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    recurring_frequency: Mapped[Optional[str]] = mapped_column(String(20))

    # Additional Fields
    receipt_url: Mapped[Optional[str]] = mapped_column(String(500))
    is_business: Mapped[bool] = mapped_column(Boolean, default=False)
    tax_deductible: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    statement: Mapped["Statement"] = relationship("Statement", back_populates="transactions")

    # Constraints
    __table_args__ = (
        # Duplicate detection: same statement, date, description, and amount
        UniqueConstraint('statement_id', 'transaction_date', 'description_raw', 'amount', name='uq_transaction_duplicate'),
        CheckConstraint("debit_credit IN ('D', 'C')", name='ck_transaction_debit_credit'),
        Index('idx_transaction_merchant', 'merchant_name', 'merchant_category'),
        Index('idx_transaction_date_amount', 'transaction_date', 'amount'),
    )

    def __repr__(self):
        return f"<Transaction(id={self.id}, date={self.transaction_date}, amount={self.amount})>"


class Fee(Base):
    """
    Fee and charges model.
    Tracks all fees with GST breakdown.
    """
    __tablename__ = "fees"

    # Primary Key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Foreign Key
    statement_id: Mapped[int] = mapped_column(Integer, ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, index=True)

    # Account Reference
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)

    # Fee Information
    fee_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    fee_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    fee_description: Mapped[Optional[str]] = mapped_column(Text)

    # Amount
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    # GST
    gst_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    gst_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))

    # Waiver
    waived: Mapped[bool] = mapped_column(Boolean, default=False)
    waiver_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    statement: Mapped["Statement"] = relationship("Statement", back_populates="fees")

    def __repr__(self):
        return f"<Fee(id={self.id}, type={self.fee_type}, amount={self.amount})>"


class InterestCharge(Base):
    """
    Interest charges model.
    Stores interest calculation details.
    """
    __tablename__ = "interest_charges"

    # Primary Key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Foreign Key
    statement_id: Mapped[int] = mapped_column(Integer, ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, index=True)

    # Account Reference
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)

    # Interest Type
    interest_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Calculation Details
    balance_subject_to_interest: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    apr: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 3))
    daily_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))
    days_in_cycle: Mapped[Optional[int]] = mapped_column(Integer)

    # Amount
    interest_charged: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    # Method
    calculation_method: Mapped[Optional[str]] = mapped_column(String(50))

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    statement: Mapped["Statement"] = relationship("Statement", back_populates="interest_charges")

    def __repr__(self):
        return f"<InterestCharge(id={self.id}, type={self.interest_type}, amount={self.interest_charged})>"


class RewardsSummary(Base):
    """
    Rewards summary model.
    Tracks rewards/points for each statement.
    """
    __tablename__ = "rewards_summary"

    # Primary Key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Foreign Key
    statement_id: Mapped[int] = mapped_column(Integer, ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # Account Reference
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)
    statement_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Opening Balance
    opening_balance: Mapped[int] = mapped_column(Integer, default=0)

    # Earned
    earned_purchases: Mapped[int] = mapped_column(Integer, default=0)
    earned_bonus: Mapped[int] = mapped_column(Integer, default=0)
    earned_welcome: Mapped[int] = mapped_column(Integer, default=0)

    # Redeemed
    redeemed_travel: Mapped[int] = mapped_column(Integer, default=0)
    redeemed_cashback: Mapped[int] = mapped_column(Integer, default=0)
    redeemed_vouchers: Mapped[int] = mapped_column(Integer, default=0)
    redeemed_other: Mapped[int] = mapped_column(Integer, default=0)

    # Adjustments
    expired: Mapped[int] = mapped_column(Integer, default=0)
    adjusted: Mapped[int] = mapped_column(Integer, default=0)

    # Closing Balance
    closing_balance: Mapped[int] = mapped_column(Integer, default=0)

    # Value
    points_value_inr: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))

    # Expiration
    points_expiring_next_month: Mapped[int] = mapped_column(Integer, default=0)

    # Lifetime
    lifetime_earned: Mapped[Optional[int]] = mapped_column(Integer)
    lifetime_redeemed: Mapped[Optional[int]] = mapped_column(Integer)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    statement: Mapped["Statement"] = relationship("Statement", back_populates="rewards_summary")

    def __repr__(self):
        return f"<RewardsSummary(id={self.id}, closing_balance={self.closing_balance})>"


class CategorySummary(Base):
    """
    Category summary model.
    Stores spending breakdown by category for each statement.
    """
    __tablename__ = "category_summary"

    # Primary Key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Foreign Key
    statement_id: Mapped[int] = mapped_column(Integer, ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, index=True)

    # Account Reference
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)

    # Category
    category_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Metrics
    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    percentage_of_spending: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    avg_transaction_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    rewards_earned: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    statement: Mapped["Statement"] = relationship("Statement", back_populates="category_summaries")

    # Constraints
    __table_args__ = (
        UniqueConstraint('statement_id', 'category_name', name='uq_category_per_statement'),
        Index('idx_category_name', 'category_name'),
    )

    def __repr__(self):
        return f"<CategorySummary(id={self.id}, category={self.category_name}, amount={self.total_amount})>"


class Payment(Base):
    """
    Payment model.
    Tracks payment history.
    """
    __tablename__ = "payments"

    # Primary Key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Foreign Key (optional - payment may not be linked to a statement yet)
    statement_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("statements.id", ondelete="SET NULL"), index=True)

    # Account Reference
    account_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Payment Details
    payment_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    # Method
    payment_method: Mapped[Optional[str]] = mapped_column(String(50))
    reference_number: Mapped[Optional[str]] = mapped_column(String(100), index=True)

    # Status
    payment_status: Mapped[Optional[str]] = mapped_column(String(20))
    processing_date: Mapped[Optional[date]] = mapped_column(Date)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    statement: Mapped[Optional["Statement"]] = relationship("Statement", back_populates="payments")

    def __repr__(self):
        return f"<Payment(id={self.id}, date={self.payment_date}, amount={self.payment_amount})>"
