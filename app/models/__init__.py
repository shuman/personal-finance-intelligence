"""
SQLAlchemy models for all database tables.
Comprehensive schema for storing financial statement data across
multiple banks, card types, and account types.
"""
from datetime import datetime, date, time
from decimal import Decimal
from typing import List, Optional
from sqlalchemy import (
    Column, String, Integer, Date, DateTime, Time, Numeric, Boolean, Text,
    ForeignKey, Index, UniqueConstraint, CheckConstraint, func
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.dialects.sqlite import JSON
from app.database import Base
from app.utils.encryption import EncryptedString, EncryptedText, EncryptedJSON, EncryptedNumeric


# ---------------------------------------------------------------------------
# User Model (Multi-tenant authentication)
# ---------------------------------------------------------------------------

class User(Base):
    """
    User model for multi-tenant authentication.
    All financial data is scoped by user_id foreign key.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)

    # Authentication
    email: Mapped[str] = mapped_column(EncryptedString(255), nullable=False, index=True)
    email_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # Profile
    full_name: Mapped[Optional[str]] = mapped_column(EncryptedString(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Consent tracking
    terms_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    privacy_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    ai_consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Relationships (all user-owned data)
    financial_institutions: Mapped[List["FinancialInstitution"]] = relationship("FinancialInstitution", back_populates="user", cascade="all, delete-orphan")
    accounts: Mapped[List["Account"]] = relationship("Account", back_populates="user", cascade="all, delete-orphan")
    category_rules: Mapped[List["CategoryRule"]] = relationship("CategoryRule", back_populates="user", cascade="all, delete-orphan")
    ai_extractions: Mapped[List["AiExtraction"]] = relationship("AiExtraction", back_populates="user", cascade="all, delete-orphan")
    insights: Mapped[List["Insight"]] = relationship("Insight", back_populates="user", cascade="all, delete-orphan")
    budgets: Mapped[List["Budget"]] = relationship("Budget", back_populates="user", cascade="all, delete-orphan")
    advisor_reports: Mapped[List["AdvisorReport"]] = relationship("AdvisorReport", back_populates="user", cascade="all, delete-orphan")
    statements: Mapped[List["Statement"]] = relationship("Statement", back_populates="user", cascade="all, delete-orphan")
    transactions: Mapped[List["Transaction"]] = relationship("Transaction", back_populates="user", cascade="all, delete-orphan")
    fees: Mapped[List["Fee"]] = relationship("Fee", back_populates="user", cascade="all, delete-orphan")
    interest_charges: Mapped[List["InterestCharge"]] = relationship("InterestCharge", back_populates="user", cascade="all, delete-orphan")
    rewards_summaries: Mapped[List["RewardsSummary"]] = relationship("RewardsSummary", back_populates="user", cascade="all, delete-orphan")
    category_summaries: Mapped[List["CategorySummary"]] = relationship("CategorySummary", back_populates="user", cascade="all, delete-orphan")
    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    daily_expenses: Mapped[List["DailyExpense"]] = relationship("DailyExpense", back_populates="user", cascade="all, delete-orphan")
    daily_income: Mapped[List["DailyIncome"]] = relationship("DailyIncome", back_populates="user", cascade="all, delete-orphan")
    liability_templates: Mapped[List["LiabilityTemplate"]] = relationship("LiabilityTemplate", back_populates="user", foreign_keys="[LiabilityTemplate.user_id]", cascade="all, delete-orphan")
    monthly_records: Mapped[List["MonthlyRecord"]] = relationship("MonthlyRecord", back_populates="user", foreign_keys="[MonthlyRecord.user_id]", cascade="all, delete-orphan")
    monthly_liabilities: Mapped[List["MonthlyLiability"]] = relationship("MonthlyLiability", back_populates="user", foreign_keys="[MonthlyLiability.user_id]", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


# ---------------------------------------------------------------------------
# NEW: Financial Institution Registry
# ---------------------------------------------------------------------------

class FinancialInstitution(Base):
    """
    Registry of banks and financial institutions.
    Drives bank-specific parsing behaviour for Claude Vision.
    Seed this table on startup via init_db().
    """
    __tablename__ = "financial_institutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    short_name: Mapped[str] = mapped_column(String(20), nullable=False)
    country: Mapped[str] = mapped_column(String(2), default="BD")
    swift_code: Mapped[Optional[str]] = mapped_column(String(20))

    # Claude Vision parsing hints
    statement_format_hint: Mapped[str] = mapped_column(String(50), default="generic")
    # JSON list of lowercase keywords: ["city bank", "cbl", "american express"]
    detection_keywords: Mapped[Optional[dict]] = mapped_column(JSON)
    # Whether this bank's statement has a promotional sidebar to crop (e.g. BRAC Bank)
    has_sidebar: Mapped[bool] = mapped_column(Boolean, default=False)
    # Percentage of image width to crop from the right side (0 = no crop)
    sidebar_crop_right_pct: Mapped[int] = mapped_column(Integer, default=0)
    # "chronological" (Amex) or "sectioned" (BRAC: Payments / Fees / Transactions)
    page_structure: Mapped[str] = mapped_column(String(20), default="chronological")
    default_currency: Mapped[str] = mapped_column(String(3), default="BDT")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="financial_institutions")
    accounts: Mapped[List["Account"]] = relationship("Account", back_populates="institution")

    def __repr__(self):
        return f"<FinancialInstitution(id={self.id}, name={self.name})>"


# ---------------------------------------------------------------------------
# NEW: Account Registry (replaces loose account_number strings)
# ---------------------------------------------------------------------------

class Account(Base):
    """
    Represents a single financial account: credit card, debit card,
    savings account, MFS wallet, etc.
    Supplement cards are linked to their primary card via parent_account_id.
    """
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("financial_institutions.id", ondelete="SET NULL"), index=True
    )

    # Account identity
    account_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="credit_card"
    )  # credit_card | debit_card | savings | current | mfs
    account_number_masked: Mapped[str] = mapped_column(EncryptedString(30), nullable=False)
    # SHA-256 of the full account number — for dedup without storing full number
    account_number_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    # Last 4 digits in plaintext for account matching during import
    card_last_four: Mapped[Optional[str]] = mapped_column(String(4), index=True)
    cardholder_name: Mapped[Optional[str]] = mapped_column(String(200))
    account_nickname: Mapped[Optional[str]] = mapped_column(EncryptedString(100))

    # Card-specific (NULL for plain bank accounts)
    card_network: Mapped[Optional[str]] = mapped_column(String(20))  # AMEX | VISA | MASTERCARD
    card_tier: Mapped[Optional[str]] = mapped_column(String(20))     # primary | supplement
    parent_account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )

    # Financial limits (credit/debit cards)
    billing_currency: Mapped[str] = mapped_column(String(3), default="BDT")
    credit_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    cash_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))

    # Rewards program
    reward_program_name: Mapped[Optional[str]] = mapped_column(String(100))
    reward_type: Mapped[Optional[str]] = mapped_column(String(20))  # points | cashback | miles
    reward_expiry_months: Mapped[Optional[int]] = mapped_column(Integer)
    # BDT value per point, used to estimate total reward value
    points_value_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    color_hex: Mapped[Optional[str]] = mapped_column(String(7))  # "#1A73E8"

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="accounts")
    institution: Mapped[Optional["FinancialInstitution"]] = relationship(
        "FinancialInstitution", back_populates="accounts"
    )
    supplement_cards: Mapped[List["Account"]] = relationship(
        "Account", foreign_keys=[parent_account_id]
    )
    statements: Mapped[List["Statement"]] = relationship("Statement", back_populates="account")

    def __repr__(self):
        return f"<Account(id={self.id}, masked={self.account_number_masked}, holder={self.cardholder_name})>"


# ---------------------------------------------------------------------------
# NEW: Category Rule Memory (AI category learning)
# ---------------------------------------------------------------------------

class CategoryRule(Base):
    """
    Persistent category rules learned from user overrides and Claude AI.
    When a user overrides a category, a rule is stored here so future
    transactions from the same merchant are auto-categorized correctly.
    """
    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # The merchant name pattern (lowercase, stripped) to match against
    merchant_pattern: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    normalized_merchant: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String(100))

    # Where this rule came from
    source: Mapped[str] = mapped_column(String(20), default="builtin")
    # builtin | user_override | claude_ai

    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=Decimal("0.80"))
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    last_matched_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="category_rules")

    __table_args__ = (
        Index("idx_category_rule_merchant", "normalized_merchant"),
        UniqueConstraint("normalized_merchant", "source", "user_id", name="uq_rule_merchant_source_user"),
    )

    def __repr__(self):
        return f"<CategoryRule(merchant={self.merchant_pattern}, category={self.category}, source={self.source})>"


# ---------------------------------------------------------------------------
# NEW: AI Extraction Audit Trail
# ---------------------------------------------------------------------------

class AiExtraction(Base):
    """
    Audit trail for every Claude API call made during statement processing.
    Used for cost tracking and to cache results — re-processing never
    re-calls the API if a cached extraction exists.

    Cache key: file_hash (SHA-256 of the raw PDF bytes).
    raw_response stores the full JSON-safe parsed_data dict so subsequent
    uploads of the same PDF skip the Claude API call entirely.
    """
    __tablename__ = "ai_extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    statement_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("statements.id", ondelete="SET NULL"), index=True
    )

    # SHA-256 of the raw PDF bytes — used as the cache key
    file_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    model_used: Mapped[str] = mapped_column(String(100), default="claude-sonnet-4-5")
    pages_processed: Mapped[int] = mapped_column(Integer, default=0)
    pages_skipped: Mapped[int] = mapped_column(Integer, default=0)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))

    extraction_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 2))
    # Pages where Claude flagged uncertainty
    issues_flagged: Mapped[Optional[dict]] = mapped_column(JSON)
    # Full JSON-safe parsed_data dict — the cache payload
    raw_response: Mapped[Optional[dict]] = mapped_column(EncryptedJSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="ai_extractions")
    statement: Mapped[Optional["Statement"]] = relationship("Statement", back_populates="ai_extractions")

    def __repr__(self):
        return f"<AiExtraction(id={self.id}, tokens={self.input_tokens}+{self.output_tokens}, cost=${self.cost_usd})>"


# ---------------------------------------------------------------------------
# NEW: AI Advisor Insights
# ---------------------------------------------------------------------------

class Insight(Base):
    """
    AI-generated financial insights and advisor recommendations.
    Stored so they are never regenerated unless underlying data changes.
    Displayed in the advisor dashboard.
    """
    __tablename__ = "insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    insight_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # reward_expiry_alert | fx_cost_report | budget_breach | overspending |
    # duplicate_subscription | monthly_report | cross_card

    scope: Mapped[str] = mapped_column(String(20), default="monthly")
    # transaction | monthly | quarterly | annual

    period_from: Mapped[Optional[date]] = mapped_column(Date, index=True)
    period_to: Mapped[Optional[date]] = mapped_column(Date, index=True)

    # NULL = multi-account insight
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # Markdown
    # Snapshot of the data referenced in the insight (for charts)
    data_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)

    # 1 = critical, 2 = warning, 3 = tip/info
    priority: Mapped[int] = mapped_column(Integer, default=3)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="insights")
    account: Mapped[Optional["Account"]] = relationship("Account")

    def __repr__(self):
        return f"<Insight(id={self.id}, type={self.insight_type}, priority={self.priority})>"


# ---------------------------------------------------------------------------
# NEW: Budget Tracking
# ---------------------------------------------------------------------------

class Budget(Base):
    """
    Monthly spending budgets per category.
    Used by the AI advisor to detect breaches and project overspending.
    """
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String(100))

    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")

    # Alert when this percentage of the budget is consumed (default 80%)
    alert_at_pct: Mapped[int] = mapped_column(Integer, default=80)

    # NULL = applies to all accounts
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="budgets")
    account: Mapped[Optional["Account"]] = relationship("Account")

    __table_args__ = (
        UniqueConstraint("category", "account_id", name="uq_budget_category_account"),
    )

    def __repr__(self):
        return f"<Budget(category={self.category}, limit={self.monthly_limit} {self.currency})>"


# ---------------------------------------------------------------------------
# NEW: AI Advisor Report (monthly cached diagnosis)
# ---------------------------------------------------------------------------

class AdvisorReport(Base):
    """
    Monthly AI-generated financial diagnosis report.
    One report per month per account — cached to avoid re-generating.
    Generated by Claude Sonnet with structured JSON output.
    """
    __tablename__ = "advisor_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)

    # Which user/account/month this report covers
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    month: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )

    # Section 1: Diagnosis (bold opening statement)
    diagnosis: Mapped[Optional[str]] = mapped_column(Text)

    # Section 2: Health Score (0-100)
    score: Mapped[Optional[int]] = mapped_column(Integer)
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    # e.g. {"spending_control": 20, "savings_mindset": 18, "consistency": 22, "discipline": 19}
    score_prev: Mapped[Optional[int]] = mapped_column(Integer)
    score_delta: Mapped[Optional[int]] = mapped_column(Integer)

    # Section 3: Behavioral Insights
    insights: Mapped[Optional[dict]] = mapped_column(JSON)
    # e.g. [{"title": "...", "icon": "fa-bolt", "text": "..."}]

    # Section 4: Top 3 Mistakes
    mistakes: Mapped[Optional[dict]] = mapped_column(JSON)
    # e.g. [{"title": "...", "detail": "...", "cost_bdt": 5000}]

    # Section 5: Opportunities
    recommendations: Mapped[Optional[dict]] = mapped_column(JSON)
    # e.g. [{"title": "...", "detail": "...", "savings_bdt": 3000}]

    # Section 6: Risk Alerts
    risks: Mapped[Optional[dict]] = mapped_column(JSON)
    # e.g. [{"title": "...", "severity": "high", "detail": "..."}]

    # Section 7: Personality
    personality_type: Mapped[Optional[str]] = mapped_column(String(100))
    personality_detail: Mapped[Optional[str]] = mapped_column(Text)

    # Section 8: Top Recommendation
    top_recommendation: Mapped[Optional[str]] = mapped_column(Text)

    # Section 9: Projection (6-month forward)
    projection: Mapped[Optional[dict]] = mapped_column(JSON)
    # e.g. {"current_monthly": 50000, "projected_6m": [50000, 48000, ...], "trend": "decreasing"}

    # Section 10: Advisor Notes
    advisor_notes: Mapped[Optional[str]] = mapped_column(Text)

    # Income & Savings sections (holistic financial advice)
    income_insights: Mapped[Optional[dict]] = mapped_column(JSON)
    # e.g. [{"title": "...", "icon": "fa-coins", "text": "..."}]
    income_tips: Mapped[Optional[dict]] = mapped_column(JSON)
    # e.g. [{"title": "...", "detail": "...", "potential_bdt": 5000}]
    savings_analysis: Mapped[Optional[dict]] = mapped_column(JSON)
    # e.g. {"true_savings_rate_pct": 12.5, "target_savings_rate_pct": 20, "monthly_gap_bdt": 3000, "assessment": "..."}
    motivation: Mapped[Optional[str]] = mapped_column(Text)

    # Raw data
    signals: Mapped[Optional[dict]] = mapped_column(JSON)
    raw_ai_output: Mapped[Optional[str]] = mapped_column(Text)

    # AI cost tracking
    ai_model: Mapped[Optional[str]] = mapped_column(String(100))
    ai_input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    ai_output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    ai_cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="advisor_reports")
    account: Mapped[Optional["Account"]] = relationship("Account")

    __table_args__ = (
        UniqueConstraint("year", "month", "account_id", name="uq_advisor_report_month_account"),
    )

    def __repr__(self):
        return f"<AdvisorReport(year={self.year}, month={self.month}, score={self.score})>"


# ---------------------------------------------------------------------------
# EXISTING: Statement (modified)
# ---------------------------------------------------------------------------

class Statement(Base):
    """
    Credit/debit card statement or bank account statement.
    Stores statement-level metadata and financial summary.
    """
    __tablename__ = "statements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # File Information
    filename: Mapped[str] = mapped_column(EncryptedString(500), nullable=False)
    filename_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    pdf_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    password: Mapped[Optional[str]] = mapped_column(EncryptedString(200), nullable=True)

    # Bank & Account Information
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    account_number: Mapped[str] = mapped_column(EncryptedString(20), nullable=False)
    card_type: Mapped[Optional[str]] = mapped_column(String(50))
    cardholder_name: Mapped[Optional[str]] = mapped_column(EncryptedString(200))
    member_since: Mapped[Optional[int]] = mapped_column(Integer)

    # NEW: FK to accounts table (nullable during migration period)
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )
    # NEW: How this statement was extracted
    extraction_method: Mapped[Optional[str]] = mapped_column(String(30))
    # claude_vision | regex_fallback | manual
    ai_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 2))
    # NEW: Statement type for scalability
    statement_type: Mapped[str] = mapped_column(String(20), default="credit")
    # credit | debit | bank_account | mfs

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
    new_balance: Mapped[Optional[Decimal]] = mapped_column(EncryptedNumeric)

    # Payment Information
    total_amount_due: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    minimum_payment_due: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))

    # Credit Information
    credit_limit: Mapped[Optional[Decimal]] = mapped_column(EncryptedNumeric)
    available_credit: Mapped[Optional[Decimal]] = mapped_column(EncryptedNumeric)
    cash_advance_limit: Mapped[Optional[Decimal]] = mapped_column(EncryptedNumeric)
    credit_utilization_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    # Rewards/Points (summary on statement level)
    rewards_opening: Mapped[Optional[int]] = mapped_column(Integer)
    rewards_earned: Mapped[Optional[int]] = mapped_column(Integer)
    rewards_redeemed: Mapped[Optional[int]] = mapped_column(Integer)
    rewards_closing: Mapped[Optional[int]] = mapped_column(Integer)
    # Renamed from rewards_value_inr but kept as-is for backward compat
    rewards_value_inr: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))

    # Currency (fixed default from INR → BDT)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")

    # Timestamps
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="statements")
    account: Mapped[Optional["Account"]] = relationship("Account", back_populates="statements")
    transactions: Mapped[List["Transaction"]] = relationship(
        "Transaction", back_populates="statement", cascade="all, delete-orphan"
    )
    fees: Mapped[List["Fee"]] = relationship(
        "Fee", back_populates="statement", cascade="all, delete-orphan"
    )
    interest_charges: Mapped[List["InterestCharge"]] = relationship(
        "InterestCharge", back_populates="statement", cascade="all, delete-orphan"
    )
    rewards_summary: Mapped[Optional["RewardsSummary"]] = relationship(
        "RewardsSummary", back_populates="statement", cascade="all, delete-orphan", uselist=False
    )
    category_summaries: Mapped[List["CategorySummary"]] = relationship(
        "CategorySummary", back_populates="statement", cascade="all, delete-orphan"
    )
    payments: Mapped[List["Payment"]] = relationship(
        "Payment", back_populates="statement", cascade="all, delete-orphan"
    )
    ai_extractions: Mapped[List["AiExtraction"]] = relationship(
        "AiExtraction", back_populates="statement"
    )

    __table_args__ = ()

    def __repr__(self):
        return f"<Statement(id={self.id}, bank={self.bank_name}, date={self.statement_date})>"


# ---------------------------------------------------------------------------
# EXISTING: Transaction (modified — backward compatible)
# ---------------------------------------------------------------------------

class Transaction(Base):
    """
    Individual transaction. Stores both the original source currency amounts
    and the billing currency amounts for accurate multi-currency tracking.

    Backward-compat note: `amount` and `currency` are kept alongside the new
    `billing_amount` / `billing_currency` fields. Alembic migration 004 will
    backfill billing_amount = amount for existing rows.
    """
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Foreign Keys
    statement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # NEW: direct link to account (which card was used)
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )

    # Account Reference (kept for backward compat)
    account_number: Mapped[str] = mapped_column(EncryptedString(20), nullable=False)

    # Transaction Dates
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    posting_date: Mapped[Optional[date]] = mapped_column(Date)

    # Description
    description_raw: Mapped[str] = mapped_column(EncryptedText, nullable=False)
    description_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    description_cleaned: Mapped[Optional[str]] = mapped_column(EncryptedText)

    # Merchant Information
    merchant_name: Mapped[Optional[str]] = mapped_column(EncryptedString(200))
    merchant_category: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    merchant_city: Mapped[Optional[str]] = mapped_column(String(100))
    merchant_state: Mapped[Optional[str]] = mapped_column(String(100))
    merchant_country: Mapped[str] = mapped_column(String(2), default="BD")

    # -----------------------------------------------------------------------
    # Amount fields — original (backward compat) + new semantic names
    # -----------------------------------------------------------------------
    # Original fields kept for backward compatibility with existing data:
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")

    # NEW: Cleaner semantic naming (billing = what appears on your statement)
    billing_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    billing_currency: Mapped[Optional[str]] = mapped_column(String(3))

    # NEW: Source (original transaction currency before FX conversion)
    original_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    original_currency: Mapped[Optional[str]] = mapped_column(String(3))
    # Calculated: billing_amount / original_amount
    fx_rate_applied: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    # Previously: foreign_amount / foreign_currency / exchange_rate (kept for compat)
    foreign_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    foreign_currency: Mapped[Optional[str]] = mapped_column(String(3))
    exchange_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    # Transaction Type
    transaction_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    debit_credit: Mapped[str] = mapped_column(String(1))  # D or C

    # Reference Information
    reference_number: Mapped[Optional[str]] = mapped_column(EncryptedString(100))
    authorization_code: Mapped[Optional[str]] = mapped_column(EncryptedString(50))
    card_last_four: Mapped[Optional[str]] = mapped_column(EncryptedString(4))

    # Transaction Flags
    is_international: Mapped[bool] = mapped_column(Boolean, default=False)
    is_emi: Mapped[bool] = mapped_column(Boolean, default=False)
    emi_tenure: Mapped[Optional[int]] = mapped_column(Integer)
    emi_month: Mapped[Optional[int]] = mapped_column(Integer)

    # Rewards
    rewards_earned: Mapped[int] = mapped_column(Integer, default=0)
    rewards_multiplier: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 1))

    # -----------------------------------------------------------------------
    # Category — AI + user override fields
    # -----------------------------------------------------------------------
    # Original field (kept for backward compat)
    category_manual: Mapped[Optional[str]] = mapped_column(String(100))
    # NEW: AI-generated category from Claude
    category_ai: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    subcategory_ai: Mapped[Optional[str]] = mapped_column(String(100))
    category_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 2))
    # rule | claude_ai | user_override
    category_source: Mapped[Optional[str]] = mapped_column(String(20))
    # FK to the rule that matched (for audit trail)
    category_rule_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("category_rules.id", ondelete="SET NULL")
    )

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
    user: Mapped["User"] = relationship("User", back_populates="transactions")
    statement: Mapped["Statement"] = relationship("Statement", back_populates="transactions")
    account: Mapped[Optional["Account"]] = relationship("Account")
    category_rule: Mapped[Optional["CategoryRule"]] = relationship("CategoryRule")

    __table_args__ = (
        UniqueConstraint(
            "statement_id", "transaction_date", "description_hash", "amount",
            name="uq_transaction_duplicate"
        ),
        CheckConstraint("debit_credit IN ('D', 'C')", name="ck_transaction_debit_credit"),
        Index("idx_transaction_date_amount", "transaction_date", "amount"),
        Index("idx_transaction_category_ai", "category_ai"),
    )

    def __repr__(self):
        return f"<Transaction(id={self.id}, date={self.transaction_date}, amount={self.amount})>"


# ---------------------------------------------------------------------------
# EXISTING: Fee (unchanged)
# ---------------------------------------------------------------------------

class Fee(Base):
    """Fee and charges model. Tracks all fees with GST/VAT breakdown."""
    __tablename__ = "fees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    statement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_number: Mapped[str] = mapped_column(EncryptedString(20), nullable=False)

    fee_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    fee_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    fee_description: Mapped[Optional[str]] = mapped_column(Text)

    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")

    gst_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    gst_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))

    waived: Mapped[bool] = mapped_column(Boolean, default=False)
    waiver_reason: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="fees")
    statement: Mapped["Statement"] = relationship("Statement", back_populates="fees")

    def __repr__(self):
        return f"<Fee(id={self.id}, type={self.fee_type}, amount={self.amount})>"


# ---------------------------------------------------------------------------
# EXISTING: InterestCharge (unchanged)
# ---------------------------------------------------------------------------

class InterestCharge(Base):
    """Interest charges model. Stores interest calculation details."""
    __tablename__ = "interest_charges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    statement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_number: Mapped[str] = mapped_column(EncryptedString(20), nullable=False)

    interest_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    balance_subject_to_interest: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    apr: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 3))
    daily_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))
    days_in_cycle: Mapped[Optional[int]] = mapped_column(Integer)

    interest_charged: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")

    calculation_method: Mapped[Optional[str]] = mapped_column(String(50))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="interest_charges")
    statement: Mapped["Statement"] = relationship("Statement", back_populates="interest_charges")

    def __repr__(self):
        return f"<InterestCharge(id={self.id}, type={self.interest_type}, amount={self.interest_charged})>"


# ---------------------------------------------------------------------------
# EXISTING: RewardsSummary (modified)
# ---------------------------------------------------------------------------

class RewardsSummary(Base):
    """
    Rewards summary per statement. Tracks MR Points, Reward Points,
    cashback, or miles depending on the card's reward program.
    """
    __tablename__ = "rewards_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    statement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("statements.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )

    account_number: Mapped[str] = mapped_column(EncryptedString(20), nullable=False)
    statement_date: Mapped[date] = mapped_column(Date, nullable=False)

    # NEW: Reward program metadata
    reward_program_name: Mapped[Optional[str]] = mapped_column(String(100))
    # "MR Points" | "Reward Points" | "CashBack BDT"

    opening_balance: Mapped[int] = mapped_column(Integer, default=0)

    earned_purchases: Mapped[int] = mapped_column(Integer, default=0)
    earned_bonus: Mapped[int] = mapped_column(Integer, default=0)
    earned_welcome: Mapped[int] = mapped_column(Integer, default=0)
    # NEW: accelerated tier breakdown (e.g. {"5x": 0, "10x": 0})
    accelerated_tiers: Mapped[Optional[dict]] = mapped_column(JSON)

    redeemed_travel: Mapped[int] = mapped_column(Integer, default=0)
    redeemed_cashback: Mapped[int] = mapped_column(Integer, default=0)
    redeemed_vouchers: Mapped[int] = mapped_column(Integer, default=0)
    redeemed_other: Mapped[int] = mapped_column(Integer, default=0)

    expired: Mapped[int] = mapped_column(Integer, default=0)
    # NEW: points that expired specifically in this statement period (BRAC shows this)
    expired_this_period: Mapped[int] = mapped_column(Integer, default=0)
    adjusted: Mapped[int] = mapped_column(Integer, default=0)

    closing_balance: Mapped[int] = mapped_column(Integer, default=0)

    points_value_inr: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    # NEW: estimated value in BDT based on account's points_value_rate
    estimated_value_bdt: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))

    points_expiring_next_month: Mapped[int] = mapped_column(Integer, default=0)

    lifetime_earned: Mapped[Optional[int]] = mapped_column(Integer)
    lifetime_redeemed: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="rewards_summaries")
    statement: Mapped["Statement"] = relationship("Statement", back_populates="rewards_summary")

    def __repr__(self):
        return f"<RewardsSummary(id={self.id}, closing_balance={self.closing_balance})>"


# ---------------------------------------------------------------------------
# EXISTING: CategorySummary (currency default fixed INR → BDT)
# ---------------------------------------------------------------------------

class CategorySummary(Base):
    """Category-level spending summary per statement."""
    __tablename__ = "category_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    statement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_number: Mapped[str] = mapped_column(EncryptedString(20), nullable=False)

    category_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    subcategory_name: Mapped[Optional[str]] = mapped_column(String(100))

    transaction_count: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")  # Fixed: was "INR"
    percentage_of_spending: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    avg_transaction_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    rewards_earned: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="category_summaries")
    statement: Mapped["Statement"] = relationship("Statement", back_populates="category_summaries")

    __table_args__ = (
        UniqueConstraint("statement_id", "category_name", name="uq_category_per_statement"),
        Index("idx_category_name", "category_name"),
    )

    def __repr__(self):
        return f"<CategorySummary(id={self.id}, category={self.category_name}, amount={self.total_amount})>"


# ---------------------------------------------------------------------------
# EXISTING: Payment (currency default fixed INR → BDT)
# ---------------------------------------------------------------------------

class Payment(Base):
    """Payment history tracking."""
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    statement_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("statements.id", ondelete="SET NULL"), index=True
    )
    account_number: Mapped[str] = mapped_column(EncryptedString(20), nullable=False)

    payment_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")  # Fixed: was "INR"

    payment_method: Mapped[Optional[str]] = mapped_column(String(50))
    reference_number: Mapped[Optional[str]] = mapped_column(String(100), index=True)

    payment_status: Mapped[Optional[str]] = mapped_column(String(20))
    processing_date: Mapped[Optional[date]] = mapped_column(Date)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="payments")
    statement: Mapped[Optional["Statement"]] = relationship("Statement", back_populates="payments")

    def __repr__(self):
        return f"<Payment(id={self.id}, date={self.payment_date}, amount={self.payment_amount})>"


# ---------------------------------------------------------------------------
# NEW: Daily Expense (manual logging with batch AI categorization)
# ---------------------------------------------------------------------------

class DailyExpense(Base):
    """
    User-entered daily cash expenses for quick manual logging.
    Supports batch AI categorization workflow: draft → pending → processed.
    Separate from statement-imported transactions (different data source).
    """
    __tablename__ = "daily_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Amount
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")

    # Description
    description_raw: Mapped[str] = mapped_column(EncryptedString(500), nullable=False)
    description_normalized: Mapped[Optional[str]] = mapped_column(EncryptedString(500))

    # AI-enhanced categorization
    category: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String(100))
    tags: Mapped[Optional[dict]] = mapped_column(JSON)

    # Payment method
    payment_method: Mapped[str] = mapped_column(String(20), default="cash", index=True)
    # cash | bkash | nagad | rocket | card_estimate

    # Transaction timing
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    transaction_time: Mapped[Optional[time]] = mapped_column(Time)

    # AI workflow status
    ai_status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft | pending | processed
    confidence_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 2))
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), index=True)
    enriched_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="daily_expenses")

    def __repr__(self):
        return f"<DailyExpense(id={self.id}, amount={self.amount}, status={self.ai_status})>"


# ---------------------------------------------------------------------------
# NEW: Daily Income (manual logging)
# ---------------------------------------------------------------------------

class DailyIncome(Base):
    """
    User-entered daily income transactions for tracking cash inflows.
    Simpler workflow than expenses (fewer categories, minimal AI processing).
    """
    __tablename__ = "daily_income"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)

    # Amount
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")

    # Description
    description_raw: Mapped[str] = mapped_column(EncryptedString(500), nullable=False)
    description_normalized: Mapped[Optional[str]] = mapped_column(EncryptedString(500))

    # Income source type
    source_type: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    # freelance | salary | business | gift | investment | side_income | other
    tags: Mapped[Optional[dict]] = mapped_column(JSON)

    # Transaction date
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # AI workflow status (optional for income)
    ai_status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft | processed

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), index=True)
    enriched_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="daily_income")

    def __repr__(self):
        return f"<DailyIncome(id={self.id}, amount={self.amount}, source={self.source_type}, date={self.transaction_date})>"

# ---------------------------------------------------------------------------
# NEW: Monthly Liabilities (Standalone Tracker)
# ---------------------------------------------------------------------------
from .liabilities import LiabilityTemplate, MonthlyRecord, MonthlyLiability

# ---------------------------------------------------------------------------
# Password Reset Tokens
# ---------------------------------------------------------------------------
from .password_reset import PasswordResetToken

