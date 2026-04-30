"""
MoneyEvent — unified query layer for all financial events.

Normalises Transaction, DailyExpense, DailyIncome, and MonthlyLiability
into a single MoneyEvent stream with consistent direction, dedup, and
CC-bill-payment tagging.  All future reports and signals should consume
this layer instead of querying the raw tables directly.
"""

import asyncio
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, DailyExpense, DailyIncome, Transaction
from app.models.liabilities import MonthlyLiability
from app.services.categories import normalize_category

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Direction(str, Enum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"
    TRANSFER = "transfer"  # CC bill payments — excluded from spending totals


class EventSource(str, Enum):
    STATEMENT_TXN = "statement_txn"
    DAILY_EXPENSE = "daily_expense"
    DAILY_INCOME = "daily_income"
    LIABILITY_PAID = "liability_paid"


class PaymentMethod(str, Enum):
    CASH = "cash"
    BKASH = "bkash"
    NAGAD = "nagad"
    ROCKET = "rocket"
    CARD = "card"
    BANK = "bank"
    CARD_ESTIMATE = "card_estimate"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# MoneyEvent dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MoneyEvent:
    user_id: int
    raw_id: int
    source: EventSource
    event_date: date
    direction: Direction
    amount_bdt: Decimal  # always positive
    category: str = "Other"
    subcategory: Optional[str] = None
    merchant: Optional[str] = None
    description: Optional[str] = None
    payment_method: PaymentMethod = PaymentMethod.UNKNOWN
    account_id: Optional[int] = None
    is_recurring: bool = False
    is_deduped: bool = False
    dedup_reason: Optional[str] = None
    original_currency: Optional[str] = None
    original_amount: Optional[Decimal] = None


# ---------------------------------------------------------------------------
# Regex patterns for CC bill-payment detection
# ---------------------------------------------------------------------------

_CC_PAYMENT_PATTERNS = re.compile(
    r"credit\s*card|bill\s*payment|card\s*payment|"
    r"CARD\s*\*\d{4}|\bCC\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# MoneyEventQuery — public API
# ---------------------------------------------------------------------------


class MoneyEventQuery:
    """Unified query helper that normalises all financial events."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def fetch(
        self,
        *,
        user_id: int,
        date_from: date,
        date_to: date,
        account_id: Optional[int] = None,
        payment_source: Literal["all", "card", "cash", "liability"] = "all",
        include_transfers: bool = False,
        include_deduped: bool = False,
    ) -> list[MoneyEvent]:
        """
        Fetch normalised MoneyEvents across all data sources.

        Parameters
        ----------
        user_id : int
            Scoped user.
        date_from / date_to : date
            Inclusive date range.
        account_id : int, optional
            Restrict to a single account (statement transactions only).
        payment_source : str
            ``"all"`` | ``"card"`` | ``"cash"`` | ``"liability"``.
        include_transfers : bool
            Whether to include CC bill-payment TRANSFER events.
        include_deduped : bool
            Whether to include events flagged as duplicates.
        """
        # 1. Account type map (single query)
        account_types = await self._get_account_types(user_id)

        # 2. Fetch streams in parallel
        coros: list = []
        if payment_source in ("all", "card"):
            coros.append(self._fetch_statement_txn_events(user_id, date_from, date_to, account_id, account_types))
        if payment_source in ("all", "cash"):
            coros.append(self._fetch_daily_expense_events(user_id, date_from, date_to))
            coros.append(self._fetch_daily_income_events(user_id, date_from, date_to))
        if payment_source in ("all", "liability"):
            coros.append(self._fetch_liability_paid_events(user_id, date_from, date_to))

        results = await asyncio.gather(*coros)

        # Flatten
        events: list[MoneyEvent] = []
        for batch in results:
            events.extend(batch)

        # 3. CC bill payment detection (Phase 2 cross-account)
        events = self._detect_cc_bill_payments_phase2(events, account_types)

        # 4. Dedup
        events = self._dedup_card_estimates(events)
        events = self._dedup_liability_payments(events)

        # 5. Apply filters
        if not include_transfers:
            events = [e for e in events if e.direction != Direction.TRANSFER]
        if not include_deduped:
            events = [e for e in events if not e.is_deduped]

        # 6. Sort by event_date
        events.sort(key=lambda e: e.event_date)

        return events

    # ------------------------------------------------------------------
    # Account type helper
    # ------------------------------------------------------------------

    async def _get_account_types(self, user_id: int) -> dict[int, str]:
        """Return ``{Account.id: account_type}`` for the user."""
        result = await self.db.execute(
            select(Account.id, Account.account_type).where(Account.user_id == user_id)
        )
        return {row[0]: row[1] for row in result.all()}

    # ------------------------------------------------------------------
    # Stream fetchers
    # ------------------------------------------------------------------

    async def _fetch_statement_txn_events(
        self,
        user_id: int,
        date_from: date,
        date_to: date,
        account_id: Optional[int],
        account_types: dict[int, str],
    ) -> list[MoneyEvent]:
        """Fetch Transaction rows → MoneyEvent list with Phase 1 CC detection."""
        query = select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.transaction_date.between(date_from, date_to),
        )
        if account_id is not None:
            query = query.where(Transaction.account_id == account_id)

        result = await self.db.execute(query)
        txns = result.scalars().all()

        events: list[MoneyEvent] = []
        for t in txns:
            direction = self._txn_direction(t, account_types)
            amount_bdt = abs(t.billing_amount or t.amount or Decimal("0"))
            category = normalize_category(t.category_ai or t.merchant_category or "Other")

            events.append(MoneyEvent(
                user_id=user_id,
                raw_id=t.id,
                source=EventSource.STATEMENT_TXN,
                event_date=t.transaction_date,
                direction=direction,
                amount_bdt=amount_bdt,
                category=category,
                subcategory=t.subcategory_ai,
                merchant=t.merchant_name,
                description=t.description_raw,
                payment_method=PaymentMethod.CARD,
                account_id=t.account_id,
                is_recurring=t.is_recurring,
                original_currency=t.original_currency or t.currency,
                original_amount=t.original_amount or t.amount,
            ))
        return events

    async def _fetch_daily_expense_events(
        self,
        user_id: int,
        date_from: date,
        date_to: date,
    ) -> list[MoneyEvent]:
        """Fetch DailyExpense rows (processed only) → MoneyEvent list."""
        query = select(DailyExpense).where(
            DailyExpense.user_id == user_id,
            DailyExpense.transaction_date.between(date_from, date_to),
            DailyExpense.ai_status == "processed",
        )
        result = await self.db.execute(query)
        rows = result.scalars().all()

        events: list[MoneyEvent] = []
        for r in rows:
            pm = self._map_payment_method(r.payment_method)
            category = normalize_category(r.category or "Other")

            events.append(MoneyEvent(
                user_id=user_id,
                raw_id=r.id,
                source=EventSource.DAILY_EXPENSE,
                event_date=r.transaction_date,
                direction=Direction.OUTFLOW,
                amount_bdt=abs(r.amount),
                category=category,
                subcategory=r.subcategory,
                description=r.description_raw,
                payment_method=pm,
            ))
        return events

    async def _fetch_daily_income_events(
        self,
        user_id: int,
        date_from: date,
        date_to: date,
    ) -> list[MoneyEvent]:
        """Fetch DailyIncome rows (processed only) → MoneyEvent list."""
        query = select(DailyIncome).where(
            DailyIncome.user_id == user_id,
            DailyIncome.transaction_date.between(date_from, date_to),
            DailyIncome.ai_status == "processed",
        )
        result = await self.db.execute(query)
        rows = result.scalars().all()

        events: list[MoneyEvent] = []
        for r in rows:
            events.append(MoneyEvent(
                user_id=user_id,
                raw_id=r.id,
                source=EventSource.DAILY_INCOME,
                event_date=r.transaction_date,
                direction=Direction.INFLOW,
                amount_bdt=abs(r.amount),
                category=normalize_category("Freelancing") if r.source_type == "freelance" else "Other",
                description=r.description_raw,
                payment_method=PaymentMethod.CASH,
            ))
        return events

    async def _fetch_liability_paid_events(
        self,
        user_id: int,
        date_from: date,
        date_to: date,
    ) -> list[MoneyEvent]:
        """Fetch paid MonthlyLiability rows → MoneyEvent list."""
        # status is EncryptedString — cannot filter via SQL .in_() because
        # Fernet encryption produces unique ciphertext each time.
        # Filter in Python after SQLAlchemy decrypts the column.
        query = select(MonthlyLiability).where(
            MonthlyLiability.user_id == user_id,
            MonthlyLiability.paid_date.between(date_from, date_to),
            MonthlyLiability.paid_date.isnot(None),
        )
        result = await self.db.execute(query)
        rows = result.scalars().all()

        # Python-side status filter (EncryptedString prevents SQL-level filtering)
        paid_statuses = {"Paid", "Partially Paid"}
        rows = [r for r in rows if r.status in paid_statuses]

        events: list[MoneyEvent] = []
        for r in rows:
            paid_amt = r.paid_amount if r.paid_amount else r.amount
            events.append(MoneyEvent(
                user_id=user_id,
                raw_id=r.id,
                source=EventSource.LIABILITY_PAID,
                event_date=r.paid_date,
                direction=Direction.OUTFLOW,
                amount_bdt=abs(paid_amt),
                category="Bills & EMI",
                description=r.name,
                payment_method=PaymentMethod.BANK,
            ))
        return events

    # ------------------------------------------------------------------
    # Direction / payment-method helpers
    # ------------------------------------------------------------------

    def _txn_direction(self, txn: Transaction, account_types: dict[int, str]) -> Direction:
        """
        Phase 1 CC bill-payment detection.
        Returns the Direction for a statement Transaction.
        """
        acct_type = account_types.get(txn.account_id) if txn.account_id else None

        # Credit on a credit-card account → transfer (CC bill payment received)
        if txn.debit_credit == "C" and acct_type == "credit_card":
            return Direction.TRANSFER

        # Debit on bank/debit/savings that looks like a CC payment
        if txn.debit_credit == "D" and acct_type in ("savings", "current", "debit_card"):
            if txn.transaction_type and txn.transaction_type.lower() == "payment":
                return Direction.TRANSFER
            desc = txn.description_raw or ""
            if _CC_PAYMENT_PATTERNS.search(desc):
                return Direction.TRANSFER

        # Standard direction
        if txn.debit_credit == "C":
            return Direction.INFLOW
        return Direction.OUTFLOW

    @staticmethod
    def _map_payment_method(pm_str: str) -> PaymentMethod:
        """Map a DailyExpense payment_method string to the PaymentMethod enum."""
        mapping = {
            "cash": PaymentMethod.CASH,
            "bkash": PaymentMethod.BKASH,
            "nagad": PaymentMethod.NAGAD,
            "rocket": PaymentMethod.ROCKET,
            "card_estimate": PaymentMethod.CARD_ESTIMATE,
        }
        return mapping.get(pm_str, PaymentMethod.UNKNOWN)

    # ------------------------------------------------------------------
    # CC bill payment detection — Phase 2 (cross-account matching)
    # ------------------------------------------------------------------

    def _detect_cc_bill_payments_phase2(
        self,
        events: list[MoneyEvent],
        account_types: dict[int, str],
    ) -> list[MoneyEvent]:
        """
        Confirm TRANSFER candidates from bank debits by matching to CC credits.

        For each TRANSFER candidate (bank-side debit), look for a matching
        CC credit event within ±3 days and ±5% amount.  If no match is
        found, downgrade back to OUTFLOW (it was a real purchase).
        """
        # Collect CC credits and bank-side TRANSFER candidates
        cc_credits: list[MoneyEvent] = []
        transfer_candidates: list[tuple[int, MoneyEvent]] = []  # (index, event)

        for i, e in enumerate(events):
            if e.direction != Direction.TRANSFER:
                continue
            acct_type = account_types.get(e.account_id) if e.account_id else None
            if acct_type == "credit_card":
                cc_credits.append(e)
            else:
                transfer_candidates.append((i, e))

        if not transfer_candidates or not cc_credits:
            # No cross-account matching possible — downgrade all candidates
            new_events = list(events)
            for idx, _ in transfer_candidates:
                old = new_events[idx]
                new_events[idx] = MoneyEvent(
                    **{**_dataclass_as_dict(old), "direction": Direction.OUTFLOW}
                )
            return new_events

        # Try to match each candidate to a CC credit
        matched_cc_indices: set[int] = set()
        new_events = list(events)

        for idx, candidate in transfer_candidates:
            match_found = False
            for j, cc_event in enumerate(cc_credits):
                if j in matched_cc_indices:
                    continue
                if self._amounts_match(candidate.amount_bdt, cc_event.amount_bdt, 0.05) and \
                   self._dates_within(candidate.event_date, cc_event.event_date, 3):
                    matched_cc_indices.add(j)
                    match_found = True
                    break
            if not match_found:
                # Downgrade to OUTFLOW
                old = new_events[idx]
                new_events[idx] = MoneyEvent(
                    **{**_dataclass_as_dict(old), "direction": Direction.OUTFLOW}
                )

        return new_events

    # ------------------------------------------------------------------
    # Dedup
    # ------------------------------------------------------------------

    def _dedup_card_estimates(self, events: list[MoneyEvent]) -> list[MoneyEvent]:
        """
        Flag DailyExpense card_estimate events that overlap with Transaction events.

        A card_estimate is considered a duplicate if there's a statement
        transaction from the same user within ±3 days and ±5% amount.
        """
        txn_events = [e for e in events if e.source == EventSource.STATEMENT_TXN and e.direction == Direction.OUTFLOW]
        card_est_events = [e for e in events if e.source == EventSource.DAILY_EXPENSE and e.payment_method == PaymentMethod.CARD_ESTIMATE]

        if not card_est_events or not txn_events:
            return events

        flagged_ids: set[int] = set()
        for ce in card_est_events:
            for te in txn_events:
                if self._amounts_match(ce.amount_bdt, te.amount_bdt, 0.05) and \
                   self._dates_within(ce.event_date, te.event_date, 3):
                    flagged_ids.add(id(ce))
                    break

        if not flagged_ids:
            return events

        new_events: list[MoneyEvent] = []
        for e in events:
            if id(e) in flagged_ids:
                new_events.append(MoneyEvent(
                    **{**_dataclass_as_dict(e), "is_deduped": True, "dedup_reason": "card_estimate_matched_to_statement_txn"}
                ))
            else:
                new_events.append(e)
        return new_events

    def _dedup_liability_payments(self, events: list[MoneyEvent]) -> list[MoneyEvent]:
        """
        Flag MonthlyLiability paid events that overlap with Transaction or DailyExpense outflows.

        A liability payment is considered a duplicate if there's an outflow
        from the same user within ±5 days and ±10% amount, with a matching
        category or name in the description.
        """
        liab_events = [e for e in events if e.source == EventSource.LIABILITY_PAID]
        outflow_events = [
            e for e in events
            if e.source in (EventSource.STATEMENT_TXN, EventSource.DAILY_EXPENSE)
            and e.direction == Direction.OUTFLOW
            and not e.is_deduped
        ]

        if not liab_events or not outflow_events:
            return events

        flagged_ids: set[int] = set()
        for le in liab_events:
            for oe in outflow_events:
                if not self._amounts_match(le.amount_bdt, oe.amount_bdt, 0.10):
                    continue
                if not self._dates_within(le.event_date, oe.event_date, 5):
                    continue
                # Category or name match
                if oe.category == "Bills & EMI" or (le.description and le.description in (oe.description or "")):
                    flagged_ids.add(id(le))
                    break

        if not flagged_ids:
            return events

        new_events: list[MoneyEvent] = []
        for e in events:
            if id(e) in flagged_ids:
                new_events.append(MoneyEvent(
                    **{**_dataclass_as_dict(e), "is_deduped": True, "dedup_reason": "liability_matched_to_outflow"}
                ))
            else:
                new_events.append(e)
        return new_events

    # ------------------------------------------------------------------
    # Numeric / date comparison helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _amounts_match(a: Decimal, b: Decimal, tolerance: float) -> bool:
        """True if |a - b| / max(a,b) <= tolerance."""
        if a == 0 and b == 0:
            return True
        if a == 0 or b == 0:
            return False
        diff = abs(a - b)
        denom = max(abs(a), abs(b))
        return (diff / denom) <= Decimal(str(tolerance))

    @staticmethod
    def _dates_within(d1: date, d2: date, days: int) -> bool:
        """True if the two dates are within ``days`` of each other."""
        return abs((d1 - d2).days) <= days

    # ------------------------------------------------------------------
    # Convenience aggregation methods
    # ------------------------------------------------------------------

    @staticmethod
    def total_outflow(events: list[MoneyEvent]) -> Decimal:
        """Sum of all OUTFLOW amounts (excludes TRANSFER and INFLOW)."""
        return sum(
            (e.amount_bdt for e in events if e.direction == Direction.OUTFLOW),
            Decimal("0"),
        )

    @staticmethod
    def total_inflow(events: list[MoneyEvent]) -> Decimal:
        """Sum of all INFLOW amounts."""
        return sum(
            (e.amount_bdt for e in events if e.direction == Direction.INFLOW),
            Decimal("0"),
        )

    @staticmethod
    def net_cash_flow(events: list[MoneyEvent]) -> Decimal:
        """INFLOW minus OUTFLOW."""
        return MoneyEventQuery.total_inflow(events) - MoneyEventQuery.total_outflow(events)

    @staticmethod
    def savings_rate(events: list[MoneyEvent]) -> float:
        """Savings rate as a percentage (0–100).  Returns 0 if no inflow."""
        inflow = MoneyEventQuery.total_inflow(events)
        if inflow == 0:
            return 0.0
        outflow = MoneyEventQuery.total_outflow(events)
        net = inflow - outflow
        return float((net / inflow) * 100)

    @staticmethod
    def by_category(events: list[MoneyEvent]) -> dict[str, Decimal]:
        """Return ``{category: total_amount}`` for OUTFLOW events."""
        result: dict[str, Decimal] = defaultdict(Decimal)
        for e in events:
            if e.direction == Direction.OUTFLOW:
                result[e.category] += e.amount_bdt
        return dict(result)

    @staticmethod
    def by_payment_method(events: list[MoneyEvent]) -> dict[str, Decimal]:
        """Return ``{payment_method: total_amount}`` for OUTFLOW events."""
        result: dict[str, Decimal] = defaultdict(Decimal)
        for e in events:
            if e.direction == Direction.OUTFLOW:
                result[e.payment_method.value] += e.amount_bdt
        return dict(result)

    @staticmethod
    def by_merchant(events: list[MoneyEvent], top_n: int = 10) -> list[tuple[str, Decimal]]:
        """Top-N merchants by OUTFLOW amount."""
        totals: dict[str, Decimal] = defaultdict(Decimal)
        for e in events:
            if e.direction == Direction.OUTFLOW and e.merchant:
                totals[e.merchant] += e.amount_bdt
        sorted_merchants = sorted(totals.items(), key=lambda x: x[1], reverse=True)
        return sorted_merchants[:top_n]

    @staticmethod
    def by_month(events: list[MoneyEvent]) -> dict[str, list[MoneyEvent]]:
        """Group events by ``YYYY-MM`` string."""
        result: dict[str, list[MoneyEvent]] = defaultdict(list)
        for e in events:
            key = f"{e.event_date.year:04d}-{e.event_date.month:02d}"
            result[key].append(e)
        return dict(result)

    @staticmethod
    def by_day(events: list[MoneyEvent]) -> dict[date, list[MoneyEvent]]:
        """Group events by date."""
        result: dict[date, list[MoneyEvent]] = defaultdict(list)
        for e in events:
            result[e.event_date].append(e)
        return dict(result)


# ---------------------------------------------------------------------------
# Utility — dataclass → dict (avoid dataclasses.asdict for frozen dataclasses)
# ---------------------------------------------------------------------------

def _dataclass_as_dict(obj: MoneyEvent) -> dict:
    """Convert a frozen MoneyEvent to a plain dict for re-construction."""
    return {
        "user_id": obj.user_id,
        "raw_id": obj.raw_id,
        "source": obj.source,
        "event_date": obj.event_date,
        "direction": obj.direction,
        "amount_bdt": obj.amount_bdt,
        "category": obj.category,
        "subcategory": obj.subcategory,
        "merchant": obj.merchant,
        "description": obj.description,
        "payment_method": obj.payment_method,
        "account_id": obj.account_id,
        "is_recurring": obj.is_recurring,
        "is_deduped": obj.is_deduped,
        "dedup_reason": obj.dedup_reason,
        "original_currency": obj.original_currency,
        "original_amount": obj.original_amount,
    }
