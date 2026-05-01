"""
Subscription Detector — identifies recurring software/tool expenses.

Only counts transactions in the "Software & Tools" category.
Uses subcategory (AI Services, Dev Tools, Cloud Services, etc.)
for multi-colored bar visualization.
"""
import calendar
import logging
import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transaction

logger = logging.getLogger(__name__)

# Only this category counts as a subscription
SUBSCRIPTION_CATEGORY = "Software & Tools"

# Subcategory color map for charts (used by frontend)
SUBCATEGORY_COLORS: Dict[str, str] = {
    "AI Services": "#8b5cf6",        # violet
    "Dev Tools": "#6366f1",          # indigo
    "Cloud Services": "#06b6d4",     # cyan
    "Cloud Storage": "#0ea5e9",      # sky
    "Design Tools": "#ec4899",       # pink
    "Productivity": "#10b981",       # emerald
    "Security": "#f59e0b",           # amber
    "Domain / Hosting": "#f97316",   # orange
    "App Store": "#14b8a6",          # teal
    "Other": "#9ca3af",              # gray
}

# Known subscription merchants for confidence boost
KNOWN_SOFTWARE_MERCHANTS = {
    "cursor.ai", "cursor", "github", "openai", "claude.ai",
    "anthropic", "canva", "figma", "notion", "1password",
    "adobe", "microsoft 365", "office 365", "dropbox",
    "icloud", "google one", "google storage", "google workspace",
    "google cloud", "amazon web services", "aws", "digitalocean",
    "namecheap", "godaddy", "heroku", "vercel", "netlify",
    "cloudflare", "linode", "google play",
}

# Thresholds
AMOUNT_TOLERANCE = 0.15          # 15% variation allowed
MIN_MONTHS_FOR_PATTERN = 2       # At least 2 appearances
SUBSCRIPTION_AMOUNT_MAX = 50000  # 50k BDT cap


class SubscriptionDetector:
    """Detect and analyze Software & Tools subscription expenses."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def detect_subscriptions(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Detect Software & Tools subscriptions for a given month.

        Returns:
            {
                subscriptions: [{merchant, monthly, annual, frequency,
                                 confidence, source, subcategory}],
                total_monthly, total_annual,
                duplicate_services,
                by_subcategory: [{name, color, total_monthly, count}],
                new_detected: int
            }
        """
        period_from, period_to = self._period_bounds(year, month)

        # Get all Software & Tools debit transactions for this month
        txns = await self._get_software_transactions(period_from, period_to, account_id)

        # Group by merchant
        merchant_txns: Dict[str, List[Transaction]] = defaultdict(list)
        for t in txns:
            merchant = self._normalize_merchant(t)
            merchant_txns[merchant].append(t)

        # Run multi-month pattern analysis to detect recurring ones
        pattern_data = await self._pattern_analysis(
            year, month, account_id, merchant_txns
        )

        # Build subscription list
        total_monthly = 0.0
        subs_list = []
        subcategory_totals: Dict[str, float] = defaultdict(float)
        subcategory_counts: Dict[str, int] = defaultdict(int)
        all_account_ids: Dict[str, set] = {}
        new_detected = 0

        for merchant, t_list in merchant_txns.items():
            # Get representative transaction for metadata
            rep = t_list[0]
            subcategory = rep.subcategory_ai or self._infer_subcategory(merchant)
            amount = sum(float(t.billing_amount or t.amount or 0) for t in t_list)

            # Check pattern data for confidence / frequency
            pat = pattern_data.get(merchant)
            if pat:
                confidence = pat["confidence"]
                frequency = pat["frequency"]
                source = pat["source"]
            else:
                # Single-month: lower confidence, assume monthly
                confidence = 0.50
                frequency = "monthly"
                source = "category"

            is_flagged = any(t.is_recurring for t in t_list)
            if is_flagged:
                confidence = min(confidence + 0.20, 1.0)
                source = "flagged"

            # Track accounts for duplicate detection
            acct_ids = set()
            for t in t_list:
                if t.account_id:
                    acct_ids.add(t.account_id)
            all_account_ids[merchant] = acct_ids

            monthly = round(amount, 2)
            annual = round(monthly * self._frequency_multiplier(frequency), 2)

            total_monthly += monthly
            subcategory_totals[subcategory] += monthly
            subcategory_counts[subcategory] += 1

            if source == "pattern":
                new_detected += 1

            subs_list.append({
                "merchant": merchant,
                "monthly": monthly,
                "annual": annual,
                "frequency": frequency,
                "confidence": round(confidence, 2),
                "source": source,
                "subcategory": subcategory,
                "account_ids": list(acct_ids),
            })

        # Sort by monthly cost descending
        subs_list.sort(key=lambda s: s["monthly"], reverse=True)

        total_annual = round(total_monthly * 12, 2)

        # Detect duplicates
        duplicates = [
            m for m, accts in all_account_ids.items() if len(accts) > 1
        ]

        # Build by_subcategory summary for chart legend
        by_subcategory = []
        for subcat, total in sorted(subcategory_totals.items(), key=lambda x: x[1], reverse=True):
            by_subcategory.append({
                "name": subcat,
                "color": SUBCATEGORY_COLORS.get(subcat, "#9ca3af"),
                "total_monthly": round(total, 2),
                "count": subcategory_counts[subcat],
            })

        return {
            "subscriptions": subs_list,
            "total_monthly": round(total_monthly, 2),
            "total_annual": total_annual,
            "duplicate_services": duplicates,
            "new_detected": new_detected,
            "by_subcategory": by_subcategory,
        }

    async def detect_yearly_subscriptions(
        self,
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Detect Software & Tools subscriptions across the last 12 months.

        Monthly cost is based on the most recent month the merchant appeared,
        so upgrades/downgrades are reflected immediately. Merchants with no
        transaction in the latest month are excluded (unsubscribed).
        """
        today = date.today()
        start = today - timedelta(days=365)

        query = select(Transaction).where(
            Transaction.transaction_date.between(start, today),
            Transaction.debit_credit == "D",
            self._software_category_filter(),
        )
        if account_id:
            query = query.where(Transaction.account_id == account_id)
        result = await self.db.execute(query)
        all_txns = result.scalars().all()

        # Group by merchant
        merchant_txns: Dict[str, List[Transaction]] = defaultdict(list)
        for t in all_txns:
            merchant = self._normalize_merchant(t)
            merchant_txns[merchant].append(t)

        # Determine the most recent month across all transactions
        all_dates = [t.transaction_date for t in all_txns if t.transaction_date]
        if not all_dates:
            return {
                "subscriptions": [],
                "total_annual": 0,
                "total_monthly_avg": 0,
                "by_subcategory": [],
            }
        latest_month = max(all_dates)
        latest_year, latest_mon = latest_month.year, latest_month.month

        subs_list = []
        total_monthly = 0.0
        subcategory_totals: Dict[str, float] = defaultdict(float)

        for merchant, txns in merchant_txns.items():
            # Find transactions in the most recent month only
            latest_txns = [
                t for t in txns
                if t.transaction_date
                and t.transaction_date.year == latest_year
                and t.transaction_date.month == latest_mon
            ]

            # Skip merchants with no activity in the latest month (unsubscribed)
            if not latest_txns:
                continue

            rep = txns[0]
            subcategory = rep.subcategory_ai or self._infer_subcategory(merchant)

            # Use last month's actual spending as the monthly cost
            monthly_cost = sum(
                float(t.billing_amount or t.amount or 0) for t in latest_txns
            )

            # Frequency analysis (from full history)
            months_seen = set()
            account_ids = set()
            for t in txns:
                if t.transaction_date:
                    months_seen.add((t.transaction_date.year, t.transaction_date.month))
                if t.account_id:
                    account_ids.add(t.account_id)

            num_months = len(months_seen)
            avg_per_month = len(txns) / num_months if num_months > 0 else 0
            if avg_per_month >= 0.8:
                frequency = "monthly"
            elif num_months <= 2 and len(txns) <= 2:
                frequency = "annual"
            else:
                frequency = "monthly"

            # Confidence
            is_flagged = any(t.is_recurring for t in txns)
            merchant_lower = merchant.lower()
            is_known = any(ksm in merchant_lower for ksm in KNOWN_SOFTWARE_MERCHANTS)
            confidence = 0.50
            if is_flagged:
                confidence += 0.20
            if is_known:
                confidence += 0.15
            if num_months >= 3:
                confidence += 0.10
            confidence = min(confidence, 1.0)

            annual = round(monthly_cost * self._frequency_multiplier(frequency), 2)
            total_monthly += monthly_cost
            subcategory_totals[subcategory] += monthly_cost

            subs_list.append({
                "merchant": merchant,
                "monthly_avg": round(monthly_cost, 2),
                "annual": annual,
                "frequency": frequency,
                "count": len(txns),
                "confidence": round(confidence, 2),
                "subcategory": subcategory,
            })

        subs_list.sort(key=lambda s: s["monthly_avg"], reverse=True)

        total_annual = round(total_monthly * 12, 2)

        # Build by_subcategory summary
        by_subcategory = []
        for subcat, total in sorted(subcategory_totals.items(), key=lambda x: x[1], reverse=True):
            by_subcategory.append({
                "name": subcat,
                "color": SUBCATEGORY_COLORS.get(subcat, "#9ca3af"),
                "total_annual": round(total * 12, 2),
            })

        return {
            "subscriptions": subs_list,
            "total_annual": total_annual,
            "total_monthly_avg": round(total_monthly, 2),
            "by_subcategory": by_subcategory,
        }

    # ------------------------------------------------------------------
    # Category filter
    # ------------------------------------------------------------------

    def _software_category_filter(self):
        """SQLAlchemy filter for Software & Tools category."""
        return or_(
            Transaction.category_ai == SUBSCRIPTION_CATEGORY,
            Transaction.merchant_category == SUBSCRIPTION_CATEGORY,
        )

    async def _get_software_transactions(
        self, period_from: date, period_to: date, account_id: Optional[int]
    ) -> List[Transaction]:
        """Get all Software & Tools debit transactions for a period."""
        query = select(Transaction).where(
            Transaction.transaction_date.between(period_from, period_to),
            Transaction.debit_credit == "D",
            self._software_category_filter(),
        )
        if account_id:
            query = query.where(Transaction.account_id == account_id)
        result = await self.db.execute(query)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Pattern Analysis (multi-month)
    # ------------------------------------------------------------------

    async def _pattern_analysis(
        self,
        year: int,
        month: int,
        account_id: Optional[int],
        current_merchant_txns: Dict[str, List[Transaction]],
    ) -> Dict[str, Dict[str, Any]]:
        """
        Look at Software & Tools transactions across 6 months to find
        merchants with recurring patterns.
        """
        monthly_data: Dict[str, Dict[str, List[Transaction]]] = {}

        for i in range(6):
            m = month - i
            y = year
            while m <= 0:
                m += 12
                y -= 1
            period_from, period_to = self._period_bounds(y, m)
            query = select(Transaction).where(
                Transaction.transaction_date.between(period_from, period_to),
                Transaction.debit_credit == "D",
                self._software_category_filter(),
            )
            if account_id:
                query = query.where(Transaction.account_id == account_id)
            result = await self.db.execute(query)
            txns = result.scalars().all()

            for t in txns:
                merchant = self._normalize_merchant(t)
                if merchant not in monthly_data:
                    monthly_data[merchant] = {}
                key = f"{y}-{m:02d}"
                if key not in monthly_data[merchant]:
                    monthly_data[merchant][key] = []
                monthly_data[merchant][key].append(t)

        results = {}
        for merchant, months in monthly_data.items():
            pattern = self._analyze_pattern(merchant, months)
            if pattern:
                results[merchant] = pattern

        return results

    def _analyze_pattern(
        self,
        merchant: str,
        months: Dict[str, List[Transaction]],
    ) -> Optional[Dict[str, Any]]:
        """Score a merchant's recurrence confidence from multi-month data."""
        num_months = len(months)
        total_txns = sum(len(txs) for txs in months.values())

        if num_months < MIN_MONTHS_FOR_PATTERN:
            return None

        all_amounts: List[float] = []
        account_ids: set = set()

        for txs in months.values():
            for t in txs:
                all_amounts.append(float(t.billing_amount or t.amount or 0))
                if t.account_id:
                    account_ids.add(t.account_id)

        avg_amount = sum(all_amounts) / len(all_amounts) if all_amounts else 0
        if avg_amount > SUBSCRIPTION_AMOUNT_MAX:
            return None

        # Confidence signals
        confidence = 0.40  # baseline: it's already Software & Tools
        signals = []

        merchant_lower = merchant.lower()
        if any(ksm in merchant_lower for ksm in KNOWN_SOFTWARE_MERCHANTS):
            confidence += 0.20
            signals.append("known_merchant")

        if num_months >= 4 and total_txns >= 3:
            confidence += 0.15
            signals.append("frequent")

        amount_cv = self._coefficient_of_variation(all_amounts)
        if amount_cv < 0.10:
            confidence += 0.15
            signals.append("consistent_amount")
        elif amount_cv < 0.25:
            confidence += 0.05
            signals.append("somewhat_consistent")

        regularity = num_months / 6
        if regularity >= 0.5:
            confidence += 0.10
            signals.append("regular")

        confidence = min(confidence, 1.0)

        # Frequency
        avg_per_month = total_txns / num_months
        if avg_per_month >= 0.8:
            frequency = "monthly"
        elif num_months <= 2 and total_txns <= 2:
            frequency = "annual"
        else:
            frequency = "monthly"

        # Source
        if any(t.is_recurring for txs in months.values() for t in txs):
            source = "flagged"
        elif "known_merchant" in signals:
            source = "known_merchant"
        else:
            source = "pattern"

        return {
            "confidence": round(confidence, 2),
            "frequency": frequency,
            "source": source,
            "count": total_txns,
            "account_ids": account_ids,
            "signals": signals,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _infer_subcategory(self, merchant: str) -> str:
        """Infer subcategory from merchant name when subcategory_ai is missing."""
        m = merchant.lower()
        if any(k in m for k in ["openai", "claude", "anthropic", "ai"]):
            return "AI Services"
        if any(k in m for k in ["cursor", "github", "dev"]):
            return "Dev Tools"
        if any(k in m for k in ["cloud", "aws", "digitalocean", "heroku", "vercel", "netlify", "cloudflare", "linode"]):
            return "Cloud Services"
        if any(k in m for k in ["storage", "google one", "icloud", "dropbox"]):
            return "Cloud Storage"
        if any(k in m for k in ["canva", "figma", "adobe"]):
            return "Design Tools"
        if any(k in m for k in ["notion", "productivity"]):
            return "Productivity"
        if any(k in m for k in ["1password", "security"]):
            return "Security"
        if any(k in m for k in ["namecheap", "godaddy", "domain", "hosting"]):
            return "Domain / Hosting"
        if any(k in m for k in ["google play", "app store"]):
            return "App Store"
        return "Other"

    def _normalize_merchant(self, t: Transaction) -> str:
        """Normalize merchant name for grouping."""
        name = t.merchant_name or (t.description_raw or "")[:40]
        name = re.sub(r"^purchase,\s*", "", name, flags=re.IGNORECASE)
        name = re.sub(r"^merchandize return,\s*", "", name, flags=re.IGNORECASE)
        name = name.split(",")[0].strip()
        return name if name else "Unknown"

    def _coefficient_of_variation(self, values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        if mean == 0:
            return 0.0
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return (variance ** 0.5) / mean

    def _frequency_multiplier(self, frequency: str) -> int:
        return {"monthly": 12, "quarterly": 4, "annual": 1, "weekly": 52}.get(frequency, 12)

    def _period_bounds(self, year: int, month: int) -> Tuple[date, date]:
        period_from = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        period_to = date(year, month, last_day)
        return period_from, period_to
