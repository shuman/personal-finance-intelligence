"""
Pydantic schemas for Claude Vision extraction output.
These define the exact JSON structure Claude is expected to return.
"""
import re
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, field_validator


class ExtractedTransaction(BaseModel):
    """A single transaction row extracted from a statement page."""
    date: str                           # "YYYY-MM-DD" or "DD MMM, YYYY" — normalizer handles both
    description_raw: str

    merchant_name: Optional[str] = None
    merchant_city: Optional[str] = None
    merchant_country: Optional[str] = None

    # Source column (original transaction currency)
    original_currency: str = "BDT"
    original_amount: float = 0.0

    # Settlement column (billing currency on the statement)
    billing_currency: str = "BDT"
    billing_amount: float = 0.0

    # purchase | return | fee | payment | interest | cash_advance | adjustment
    transaction_type: str = "purchase"
    is_credit: bool = False             # True for returns, payments, credits

    reference_number: Optional[str] = None

    @field_validator("transaction_type")
    @classmethod
    def normalize_transaction_type(cls, v: str) -> str:
        v = v.lower().strip()
        mapping = {
            "merchandize return": "return",
            "merchandise return": "return",
            "refund": "return",
            "purchase": "purchase",
            "payment": "payment",
            "fee": "fee",
            "interest": "interest",
            "cash advance": "cash_advance",
            "cash_advance": "cash_advance",
            "adjustment": "adjustment",
            "vat": "fee",
            "transfer": "transfer",
            "fund transfer": "transfer",
            "deposit": "deposit",
            "salary": "deposit",
            "atm withdrawal": "cash_advance",
            "atm": "cash_advance",
            "pos purchase": "purchase",
            "pos": "purchase",
            "online transfer": "transfer",
            "npsb": "transfer",
            "beftn": "transfer",
            "rtgs": "transfer",
        }
        return mapping.get(v, v)


class ExtractedCardSection(BaseModel):
    """
    Transactions grouped by physical card.
    A single statement PDF may contain sections for the primary card
    and one or more supplement cards.
    """
    card_number_masked: str             # e.g. "376948*****9844"
    cardholder_name: str
    transactions: List[ExtractedTransaction] = []


class ExtractedAccountSummary(BaseModel):
    """Financial summary from the statement header."""
    previous_balance: Optional[float] = None
    payment_received: Optional[float] = None
    new_balance: Optional[float] = None
    credit_limit: Optional[float] = None
    available_credit: Optional[float] = None
    cash_limit: Optional[float] = None
    current_balance: Optional[float] = None
    minimum_amount_due: Optional[float] = None
    total_outstanding: Optional[float] = None
    reward_points: Optional[float] = None

    @field_validator(
        "previous_balance", "payment_received", "new_balance",
        "credit_limit", "available_credit", "cash_limit",
        "current_balance", "minimum_amount_due", "total_outstanding",
        "reward_points",
        mode="before",
    )
    @classmethod
    def coerce_money_string(cls, v):
        """Handle Claude returning currency-prefixed strings like 'BDT 3,548.90'."""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            # Strip 3-letter currency prefix (BDT, USD, INR, etc.)
            cleaned = re.sub(r'^[A-Za-z]{3}\s*', '', v.strip())
            # Remove comma separators
            cleaned = cleaned.replace(',', '')
            try:
                return float(cleaned)
            except ValueError:
                return None
        return v


class ExtractedRewardsData(BaseModel):
    """Rewards/points section from the statement."""
    program_name: Optional[str] = None  # "MR Points" | "Reward Points"
    opening_balance: Optional[int] = None
    earned_purchases: Optional[int] = None
    earned_bonus: Optional[int] = None
    earned_accelerated: Optional[int] = None
    adjustment: Optional[int] = None
    redeemed: Optional[int] = None
    expired: Optional[int] = None
    expired_this_period: Optional[int] = None
    closing_balance: Optional[int] = None
    # Claude sometimes returns a list of tier objects instead of a dict — accept both
    accelerated_tiers: Optional[Any] = None
    points_expiring_next_month: Optional[int] = None

    @field_validator("accelerated_tiers", mode="before")
    @classmethod
    def coerce_accelerated_tiers(cls, v: Any) -> Optional[Dict[str, Any]]:
        """Convert list-of-tier-dicts to a plain dict keyed by tier_name."""
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, list):
            result: Dict[str, Any] = {}
            for item in v:
                if isinstance(item, dict):
                    tier_key = item.get("tier_name") or item.get("tier") or str(item)
                    points = item.get("points") or item.get("value") or 0
                    result[tier_key] = points
            return result or None
        return None


class ExtractedStatementHeader(BaseModel):
    """Header metadata extracted from page 1."""
    bank_name: Optional[str] = None
    cardholder_name: Optional[str] = None
    client_id: Optional[str] = None
    card_number_masked: Optional[str] = None
    statement_date: Optional[str] = None
    payment_due_date: Optional[str] = None
    statement_period_from: Optional[str] = None
    statement_period_to: Optional[str] = None


class ExtractedPage(BaseModel):
    """
    Result for a single statement page.
    Claude returns skip=True for ToS / promotional pages.
    """
    page_number: int = 0
    # transaction | summary | rewards | header | skip
    page_type: str = "transaction"
    skip: bool = False
    skip_reason: Optional[str] = None

    header: Optional[ExtractedStatementHeader] = None
    account_summary: Optional[ExtractedAccountSummary] = None
    card_sections: Optional[List[ExtractedCardSection]] = None
    rewards_data: Optional[ExtractedRewardsData] = None

    # Fees extracted as a separate section (BRAC Bank style)
    fees_section: Optional[List[ExtractedTransaction]] = None
    payments_section: Optional[List[ExtractedTransaction]] = None


class ExtractionResult(BaseModel):
    """
    Full extraction result for an entire PDF statement.
    Aggregates data from all non-skipped pages.
    """
    pages: List[ExtractedPage] = []
    pages_skipped: int = 0
    model_used: str = "claude-sonnet-4-5"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    confidence: float = 1.0
    issues: List[Dict[str, Any]] = []

    # Aggregated convenience properties
    @property
    def all_card_sections(self) -> List[ExtractedCardSection]:
        """All card sections across all pages."""
        sections: Dict[str, ExtractedCardSection] = {}
        for page in self.pages:
            if page.card_sections:
                for section in page.card_sections:
                    key = section.card_number_masked
                    if key not in sections:
                        sections[key] = ExtractedCardSection(
                            card_number_masked=section.card_number_masked,
                            cardholder_name=section.cardholder_name,
                        )
                    sections[key].transactions.extend(section.transactions)
        return list(sections.values())

    @property
    def header(self) -> Optional[ExtractedStatementHeader]:
        """First header found across pages."""
        for page in self.pages:
            if page.header:
                return page.header
        return None

    @property
    def account_summary(self) -> Optional[ExtractedAccountSummary]:
        """First account summary found across pages."""
        for page in self.pages:
            if page.account_summary:
                return page.account_summary
        return None

    @property
    def rewards_data(self) -> Optional[ExtractedRewardsData]:
        """First rewards data found across pages."""
        for page in self.pages:
            if page.rewards_data:
                return page.rewards_data
        return None
