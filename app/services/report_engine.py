"""
Report Engine — generates data for the 6 core dashboard reports.
All queries run on existing Transaction, Statement, CategorySummary, and Budget tables.
"""
import calendar
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transaction, Statement, Budget, DailyExpense, DailyIncome
from app.services.subscription_detector import SubscriptionDetector

logger = logging.getLogger(__name__)


class ReportEngine:
    """Generates data dicts for each dashboard report card."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _period_bounds(self, year: int, month: int):
        """Return (period_from, period_to) for a given year/month."""
        period_from = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        period_to = date(year, month, last_day)
        return period_from, period_to

    async def _get_debit_transactions(
        self,
        period_from: date,
        period_to: date,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> List[Transaction]:
        """Fetch all debit transactions for a period."""
        query = select(Transaction).where(
            Transaction.transaction_date.between(period_from, period_to),
            Transaction.debit_credit == "D",
        )
        if user_id is not None:
            query = query.where(Transaction.user_id == user_id)
        if account_id:
            query = query.where(Transaction.account_id == account_id)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def _category_distribution(
        self,
        txns: List[Transaction],
    ) -> Dict[str, float]:
        """Return {category: total_amount} from a list of transactions."""
        dist: Dict[str, float] = {}
        for t in txns:
            cat = t.category_ai or t.merchant_category or "Other"
            amount = float(t.billing_amount or t.amount or 0)
            dist[cat] = dist.get(cat, 0) + amount
        return dist

    async def _monthly_totals(
        self,
        months_back: int,
        account_id: Optional[int] = None,
        end_year: Optional[int] = None,
        end_month: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return [{month: 'YYYY-MM', total: N}, ...] for the last N months."""
        today = date.today()
        end_year = end_year or today.year
        end_month = end_month or today.month

        results: List[Dict[str, Any]] = []
        for i in range(months_back - 1, -1, -1):
            # Walk backwards from end_month
            m = end_month - i
            y = end_year
            while m <= 0:
                m += 12
                y -= 1
            period_from, period_to = self._period_bounds(y, m)
            query = select(
                func.coalesce(func.sum(Transaction.billing_amount), 0),
            ).where(
                Transaction.transaction_date.between(period_from, period_to),
                Transaction.debit_credit == "D",
            )
            if user_id is not None:
                query = query.where(Transaction.user_id == user_id)
            if account_id:
                query = query.where(Transaction.account_id == account_id)
            result = await self.db.execute(query)
            total = float(result.scalar() or 0)
            results.append({"month": f"{y}-{m:02d}", "total": round(total, 2)})
        return results

    # ------------------------------------------------------------------
    # Daily Expense Helpers
    # ------------------------------------------------------------------

    async def _get_daily_expenses(
        self,
        period_from: date,
        period_to: date,
        user_id: Optional[int] = None,
    ) -> List[DailyExpense]:
        """Fetch processed DailyExpense rows for a period (excludes drafts)."""
        query = select(DailyExpense).where(
            DailyExpense.transaction_date.between(period_from, period_to),
            DailyExpense.ai_status == "processed",
        )
        if user_id is not None:
            query = query.where(DailyExpense.user_id == user_id)
        result = await self.db.execute(query)
        return result.scalars().all()

    def _daily_expense_category_distribution(
        self,
        expenses: List[DailyExpense],
    ) -> Dict[str, float]:
        """Return {category: total_amount} from daily expenses."""
        dist: Dict[str, float] = {}
        for e in expenses:
            cat = e.category or "Other"
            amount = float(e.amount or 0)
            dist[cat] = dist.get(cat, 0) + amount
        return dist

    async def _daily_expense_monthly_totals(
        self,
        months_back: int,
        end_year: Optional[int] = None,
        end_month: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return [{month: 'YYYY-MM', total: N}, ...] for the last N months."""
        today = date.today()
        end_year = end_year or today.year
        end_month = end_month or today.month

        results: List[Dict[str, Any]] = []
        for i in range(months_back - 1, -1, -1):
            m = end_month - i
            y = end_year
            while m <= 0:
                m += 12
                y -= 1
            period_from, period_to = self._period_bounds(y, m)
            query = select(
                func.coalesce(func.sum(DailyExpense.amount), 0),
            ).where(
                DailyExpense.transaction_date.between(period_from, period_to),
                DailyExpense.ai_status == "processed",
            )
            if user_id is not None:
                query = query.where(DailyExpense.user_id == user_id)
            result = await self.db.execute(query)
            total = float(result.scalar() or 0)
            results.append({"month": f"{y}-{m:02d}", "total": round(total, 2)})
        return results

    # ------------------------------------------------------------------
    # Daily Income Helpers
    # ------------------------------------------------------------------

    async def _get_daily_income(
        self,
        period_from: date,
        period_to: date,
        user_id: Optional[int] = None,
    ) -> List[DailyIncome]:
        """Fetch DailyIncome rows for a period."""
        query = select(DailyIncome).where(
            DailyIncome.transaction_date.between(period_from, period_to),
        )
        if user_id is not None:
            query = query.where(DailyIncome.user_id == user_id)
        result = await self.db.execute(query)
        return result.scalars().all()

    def _income_source_distribution(
        self,
        entries: List[DailyIncome],
    ) -> Dict[str, float]:
        """Return {source_type: total_amount} from daily income entries."""
        dist: Dict[str, float] = {}
        for e in entries:
            src = e.source_type or "other"
            amount = float(e.amount or 0)
            dist[src] = dist.get(src, 0) + amount
        return dist

    async def _income_monthly_totals(
        self,
        months_back: int,
        end_year: Optional[int] = None,
        end_month: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return [{month: 'YYYY-MM', total: N}, ...] for the last N months."""
        today = date.today()
        end_year = end_year or today.year
        end_month = end_month or today.month

        results: List[Dict[str, Any]] = []
        for i in range(months_back - 1, -1, -1):
            m = end_month - i
            y = end_year
            while m <= 0:
                m += 12
                y -= 1
            period_from, period_to = self._period_bounds(y, m)
            query = select(
                func.coalesce(func.sum(DailyIncome.amount), 0),
            ).where(
                DailyIncome.transaction_date.between(period_from, period_to),
            )
            if user_id is not None:
                query = query.where(DailyIncome.user_id == user_id)
            result = await self.db.execute(query)
            total = float(result.scalar() or 0)
            results.append({"month": f"{y}-{m:02d}", "total": round(total, 2)})
        return results

    # ------------------------------------------------------------------
    # Report #1: Monthly Spending Breakdown
    # ------------------------------------------------------------------

    async def monthly_spending_breakdown(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
        payment_source: str = "all",
    ) -> Dict[str, Any]:
        period_from, period_to = self._period_bounds(year, month)

        # --- Current month ---
        by_cat: Dict[str, float] = {}
        if payment_source in ("all", "card"):
            txns = await self._get_debit_transactions(period_from, period_to, account_id, user_id=user_id)
            cc_cat = await self._category_distribution(txns)
            for cat, amt in cc_cat.items():
                by_cat[cat] = by_cat.get(cat, 0) + amt
        if payment_source in ("all", "cash"):
            expenses = await self._get_daily_expenses(period_from, period_to, user_id=user_id)
            cash_cat = self._daily_expense_category_distribution(expenses)
            for cat, amt in cash_cat.items():
                by_cat[cat] = by_cat.get(cat, 0) + amt

        total = sum(by_cat.values())

        # --- Previous month for trend arrows ---
        prev_month_date = period_from - timedelta(days=1)
        prev_from, prev_to = self._period_bounds(
            prev_month_date.year, prev_month_date.month
        )
        prev_by_cat: Dict[str, float] = {}
        if payment_source in ("all", "card"):
            prev_txns = await self._get_debit_transactions(prev_from, prev_to, account_id, user_id=user_id)
            prev_cc = await self._category_distribution(prev_txns)
            for cat, amt in prev_cc.items():
                prev_by_cat[cat] = prev_by_cat.get(cat, 0) + amt
        if payment_source in ("all", "cash"):
            prev_exp = await self._get_daily_expenses(prev_from, prev_to, user_id=user_id)
            prev_cash = self._daily_expense_category_distribution(prev_exp)
            for cat, amt in prev_cash.items():
                prev_by_cat[cat] = prev_by_cat.get(cat, 0) + amt
        prev_total = sum(prev_by_cat.values())

        categories = []
        for cat, amount in sorted(by_cat.items(), key=lambda x: x[1], reverse=True):
            pct = round(amount / total * 100, 1) if total else 0
            prev_amount = prev_by_cat.get(cat, 0)
            change_pct = (
                round((amount - prev_amount) / prev_amount * 100, 1)
                if prev_amount > 0
                else 0
            )
            categories.append({
                "name": cat,
                "amount": round(amount, 2),
                "pct": pct,
                "change_pct": change_pct,
            })

        top_cat = categories[0]["name"] if categories else "N/A"

        return {
            "categories": categories,
            "total": round(total, 2),
            "prev_total": round(prev_total, 2),
            "top_category": top_cat,
        }

    # ------------------------------------------------------------------
    # Report #2: Merchant Concentration
    # ------------------------------------------------------------------

    async def merchant_concentration(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
        payment_source: str = "all",
    ) -> Dict[str, Any]:
        period_from, period_to = self._period_bounds(year, month)

        merchant_data: Dict[str, Dict[str, Any]] = {}
        total = 0.0
        if payment_source in ("all", "card"):
            txns = await self._get_debit_transactions(period_from, period_to, account_id, user_id=user_id)
            for t in txns:
                name = t.merchant_name or (t.description_raw or "")[:30]
                amount = float(t.billing_amount or t.amount or 0)
                total += amount
                if name not in merchant_data:
                    merchant_data[name] = {"amount": 0.0, "txns": 0}
                merchant_data[name]["amount"] += amount
                merchant_data[name]["txns"] += 1
        if payment_source in ("all", "cash"):
            expenses = await self._get_daily_expenses(period_from, period_to, user_id=user_id)
            for e in expenses:
                name = (e.description_raw or "Cash expense")[:30]
                amount = float(e.amount or 0)
                total += amount
                if name not in merchant_data:
                    merchant_data[name] = {"amount": 0.0, "txns": 0}
                merchant_data[name]["amount"] += amount
                merchant_data[name]["txns"] += 1

        # Sort by amount descending, take top 10
        sorted_merchants = sorted(
            merchant_data.items(), key=lambda x: x[1]["amount"], reverse=True
        )[:10]

        merchants = []
        for name, data in sorted_merchants:
            pct = round(data["amount"] / total * 100, 1) if total else 0
            merchants.append({
                "name": name,
                "amount": round(data["amount"], 2),
                "pct": pct,
                "txns": data["txns"],
            })

        top3_amount = sum(m["amount"] for m in merchants[:3])
        top3_pct = round(top3_amount / total * 100, 1) if total else 0
        total_merchants = len(merchant_data)

        return {
            "merchants": merchants,
            "top3_pct": top3_pct,
            "total_merchants": total_merchants,
            "insight": (
                f"Top 3 merchants = {top3_pct}% of total spending"
                if total_merchants > 3
                else "Spending concentrated in few merchants"
            ),
        }

    # ------------------------------------------------------------------
    # Report #7: Subscription Waste
    # ------------------------------------------------------------------

    async def subscription_waste(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
        payment_source: str = "all",
    ) -> Dict[str, Any]:
        """Use SubscriptionDetector for multi-layer subscription detection."""
        detector = SubscriptionDetector(self.db)
        return await detector.detect_subscriptions(year, month, account_id)

    # ------------------------------------------------------------------
    # Report #8: Lifestyle Creep Tracker
    # ------------------------------------------------------------------

    async def lifestyle_creep(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        cc_months = await self._monthly_totals(6, account_id, end_year=year, end_month=month, user_id=user_id)
        cash_months = await self._daily_expense_monthly_totals(6, end_year=year, end_month=month, user_id=user_id)
        cash_map = {m["month"]: m["total"] for m in cash_months}

        # Combined totals (credit card + cash) for trend
        months = [
            {
                "month": m["month"],
                "total": round(m["total"] + cash_map.get(m["month"], 0), 2),
                "credit_card": m["total"],
                "cash": cash_map.get(m["month"], 0),
            }
            for m in cc_months
        ]

        if len(months) < 2:
            trend_pct = 0
            trend_direction = "stable"
        else:
            first = months[0]["total"]
            last = months[-1]["total"]
            if first > 0:
                trend_pct = round((last - first) / first * 100, 1)
            else:
                trend_pct = 0
            if trend_pct > 5:
                trend_direction = "increasing"
            elif trend_pct < -5:
                trend_direction = "decreasing"
            else:
                trend_direction = "stable"

        # Find category with biggest increase (credit card only, has richer categories)
        period_from, period_to = self._period_bounds(year, month)
        prev_month_date = period_from - timedelta(days=1)
        prev_from, prev_to = self._period_bounds(
            prev_month_date.year, prev_month_date.month
        )
        curr_txns = await self._get_debit_transactions(period_from, period_to, account_id, user_id=user_id)
        prev_txns = await self._get_debit_transactions(prev_from, prev_to, account_id, user_id=user_id)

        curr_cats = await self._category_distribution(curr_txns)
        prev_cats = await self._category_distribution(prev_txns)

        biggest_increase_cat = "N/A"
        biggest_increase_val = 0.0
        for cat, amount in curr_cats.items():
            diff = amount - prev_cats.get(cat, 0)
            if diff > biggest_increase_val:
                biggest_increase_val = diff
                biggest_increase_cat = cat

        return {
            "months": months,
            "trend_pct": trend_pct,
            "trend_direction": trend_direction,
            "biggest_increase_category": biggest_increase_cat,
        }

    # ------------------------------------------------------------------
    # Report #12: Financial Health Score
    # ------------------------------------------------------------------

    async def financial_health_score(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        period_from, period_to = self._period_bounds(year, month)

        # 1. Credit Utilization (0-20)
        utilization_score = 10  # default
        stmt_query = select(Statement).where(
            Statement.statement_date.between(period_from, period_to),
        )
        if user_id is not None:
            stmt_query = stmt_query.where(Statement.user_id == user_id)
        if account_id:
            stmt_query = stmt_query.where(Statement.account_id == account_id)
        stmt_result = await self.db.execute(stmt_query.order_by(Statement.statement_date.desc()))
        stmt = stmt_result.scalars().first()

        if stmt and stmt.credit_utilization_pct is not None:
            util_pct = float(stmt.credit_utilization_pct)
            if util_pct < 30:
                utilization_score = 20
            elif util_pct < 50:
                utilization_score = 15
            elif util_pct < 75:
                utilization_score = 10
            else:
                utilization_score = 5

        # 2. Fee Burden (0-20)
        fee_score = 20
        if stmt and stmt.fees_charged:
            fees = float(stmt.fees_charged)
            purchases = float(stmt.purchases or 0)
            if purchases > 0:
                fee_ratio = fees / purchases * 100
                if fee_ratio > 5:
                    fee_score = 5
                elif fee_ratio > 3:
                    fee_score = 10
                elif fee_ratio > 1:
                    fee_score = 15

        # 3. Budget Adherence (0-20)
        budget_score = 15
        budget_query = select(Budget).where(Budget.is_active == True)
        if user_id is not None:
            budget_query = budget_query.where(Budget.user_id == user_id)
        budget_result = await self.db.execute(budget_query)
        budgets = budget_result.scalars().all()
        if budgets:
            txns = await self._get_debit_transactions(period_from, period_to, account_id, user_id=user_id)
            spending = await self._category_distribution(txns)
            breached = 0
            for b in budgets:
                spent = spending.get(b.category, 0)
                limit = float(b.monthly_limit)
                if limit > 0 and spent > limit:
                    breached += 1
            breach_ratio = breached / len(budgets) if budgets else 0
            if breach_ratio == 0:
                budget_score = 20
            elif breach_ratio < 0.25:
                budget_score = 15
            elif breach_ratio < 0.5:
                budget_score = 10
            else:
                budget_score = 5

        # 4. Recurring Ratio (0-20)
        recurring_score = 15
        txns = await self._get_debit_transactions(period_from, period_to, account_id, user_id=user_id)
        total_spend = sum(float(t.billing_amount or t.amount or 0) for t in txns)
        recurring_spend = sum(
            float(t.billing_amount or t.amount or 0)
            for t in txns
            if t.is_recurring
        )
        if total_spend > 0:
            recurring_pct = recurring_spend / total_spend * 100
            if recurring_pct < 15:
                recurring_score = 20
            elif recurring_pct < 30:
                recurring_score = 15
            elif recurring_pct < 50:
                recurring_score = 10
            else:
                recurring_score = 5

        # 5. Spending Trend (0-20)
        trend_score = 15
        prev_month_date = period_from - timedelta(days=1)
        prev_from, prev_to = self._period_bounds(
            prev_month_date.year, prev_month_date.month
        )
        prev_txns = await self._get_debit_transactions(prev_from, prev_to, account_id, user_id=user_id)
        prev_total = sum(float(t.billing_amount or t.amount or 0) for t in prev_txns)
        if prev_total > 0 and total_spend > 0:
            change_pct = (total_spend - prev_total) / prev_total * 100
            if change_pct < -10:
                trend_score = 20
            elif change_pct < 0:
                trend_score = 18
            elif change_pct < 10:
                trend_score = 15
            elif change_pct < 25:
                trend_score = 10
            else:
                trend_score = 5

        factors = [
            {"name": "Credit Utilization", "score": utilization_score, "max": 20},
            {"name": "Fee Burden", "score": fee_score, "max": 20},
            {"name": "Budget Adherence", "score": budget_score, "max": 20},
            {"name": "Recurring Ratio", "score": recurring_score, "max": 20},
            {"name": "Spending Trend", "score": trend_score, "max": 20},
        ]

        # 6th factor: Savings Mindset (income vs total outflow) — 0-20 pts
        # Only computed if income data exists; neutral 10 otherwise
        income_entries = await self._get_daily_income(period_from, period_to, user_id=user_id)
        income_total = sum(float(e.amount or 0) for e in income_entries)
        savings_mindset_score = 10  # neutral default when no income data
        if income_total > 0:
            cash_expenses = await self._get_daily_expenses(period_from, period_to, user_id=user_id)
            cash_total = sum(float(e.amount or 0) for e in cash_expenses)
            savings_rate = (income_total - total_spend - cash_total) / income_total
            if savings_rate >= 0.20:
                savings_mindset_score = 20
            elif savings_rate >= 0.10:
                savings_mindset_score = 15
            elif savings_rate >= 0:
                savings_mindset_score = 10
            else:
                savings_mindset_score = 5
            factors.append({"name": "Savings Mindset", "score": savings_mindset_score, "max": 20})

        # Normalize to 100 regardless of whether savings factor is present
        raw_score = sum(f["score"] for f in factors)
        raw_max = sum(f["max"] for f in factors)
        score = round(raw_score / raw_max * 100) if raw_max > 0 else 0

        if score >= 80:
            grade = "Excellent"
        elif score >= 60:
            grade = "Good"
        elif score >= 40:
            grade = "Fair"
        else:
            grade = "Needs Work"

        return {
            "score": score,
            "max": 100,
            "grade": grade,
            "factors": factors,
        }

    # ------------------------------------------------------------------
    # Report #18: No-Spend Day Tracker
    # ------------------------------------------------------------------

    async def no_spend_day_tracker(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        period_from, period_to = self._period_bounds(year, month)
        total_days = (period_to - period_from).days + 1

        # Get distinct days with spending (credit card)
        query = select(Transaction.transaction_date).where(
            Transaction.transaction_date.between(period_from, period_to),
            Transaction.debit_credit == "D",
        )
        if user_id is not None:
            query = query.where(Transaction.user_id == user_id)
        if account_id:
            query = query.where(Transaction.account_id == account_id)
        result = await self.db.execute(query.distinct())
        spend_dates = {row[0] for row in result.all()}

        # Also mark days with daily cash expenses as spent
        cash_query = select(DailyExpense.transaction_date).where(
            DailyExpense.transaction_date.between(period_from, period_to),
            DailyExpense.ai_status == "processed",
        )
        if user_id is not None:
            cash_query = cash_query.where(DailyExpense.user_id == user_id)
        cash_result = await self.db.execute(cash_query.distinct())
        spend_dates |= {row[0] for row in cash_result.all()}

        # Build calendar
        calendar_data = []
        no_spend_days = 0
        current_streak = 0
        best_streak = 0
        streak = 0
        today = date.today()

        for day_num in range(1, total_days + 1):
            day_date = date(year, month, day_num)
            spent = day_date in spend_dates
            if not spent and day_date <= today:
                no_spend_days += 1
                streak += 1
                best_streak = max(best_streak, streak)
            else:
                streak = 0
            calendar_data.append({"day": day_num, "spent": spent})

        # Current streak (from today backwards)
        current_streak = 0
        check_date = min(today, period_to)
        while check_date >= period_from:
            if check_date not in spend_dates:
                current_streak += 1
                check_date -= timedelta(days=1)
            else:
                break

        goal = max(10, total_days // 3)  # Roughly 1/3 of the month

        return {
            "no_spend_days": no_spend_days,
            "total_days": total_days,
            "goal": goal,
            "current_streak": current_streak,
            "best_streak": best_streak,
            "calendar": calendar_data,
        }

    # ------------------------------------------------------------------
    # Report: Cash Expense Breakdown (monthly)
    # ------------------------------------------------------------------

    async def cash_expense_breakdown(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Category + payment method breakdown for daily cash expenses."""
        period_from, period_to = self._period_bounds(year, month)
        expenses = await self._get_daily_expenses(period_from, period_to, user_id=user_id)

        by_cat = self._daily_expense_category_distribution(expenses)
        total = sum(by_cat.values())

        categories = []
        for cat, amount in sorted(by_cat.items(), key=lambda x: x[1], reverse=True):
            pct = round(amount / total * 100, 1) if total else 0
            categories.append({"name": cat, "amount": round(amount, 2), "pct": pct})

        # Payment method breakdown
        pm_dist: Dict[str, float] = {}
        for e in expenses:
            pm = e.payment_method or "cash"
            pm_dist[pm] = pm_dist.get(pm, 0) + float(e.amount or 0)

        payment_methods = []
        for pm, amount in sorted(pm_dist.items(), key=lambda x: x[1], reverse=True):
            pct = round(amount / total * 100, 1) if total else 0
            payment_methods.append({"method": pm, "amount": round(amount, 2), "pct": pct})

        return {
            "categories": categories,
            "payment_methods": payment_methods,
            "total": round(total, 2),
            "count": len(expenses),
        }

    # ------------------------------------------------------------------
    # Report: Income Summary (monthly)
    # ------------------------------------------------------------------

    async def income_summary(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Income by source type for a month."""
        period_from, period_to = self._period_bounds(year, month)
        entries = await self._get_daily_income(period_from, period_to, user_id=user_id)

        by_src = self._income_source_distribution(entries)
        total = sum(by_src.values())
        count = len(entries)

        sources = []
        for src, amount in sorted(by_src.items(), key=lambda x: x[1], reverse=True):
            pct = round(amount / total * 100, 1) if total else 0
            sources.append({"name": src, "amount": round(amount, 2), "pct": pct})

        return {
            "sources": sources,
            "total": round(total, 2),
            "count": count,
            "avg_per_entry": round(total / count, 2) if count else 0,
        }

    # ------------------------------------------------------------------
    # Report: Income vs Expense (monthly)
    # ------------------------------------------------------------------

    async def income_vs_expense(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Compare income, credit card spend, and cash spend for the month."""
        period_from, period_to = self._period_bounds(year, month)

        # Credit card spend
        cc_txns = await self._get_debit_transactions(period_from, period_to, account_id, user_id=user_id)
        cc_total = sum(float(t.billing_amount or t.amount or 0) for t in cc_txns)

        # Cash expenses
        expenses = await self._get_daily_expenses(period_from, period_to, user_id=user_id)
        cash_total = sum(float(e.amount or 0) for e in expenses)

        # Income
        income_entries = await self._get_daily_income(period_from, period_to, user_id=user_id)
        income_total = sum(float(e.amount or 0) for e in income_entries)

        combined_expense = round(cc_total + cash_total, 2)
        net_flow = round(income_total - combined_expense, 2)
        savings_rate_pct = round(net_flow / income_total * 100, 1) if income_total > 0 else 0

        # 6-month trend for the chart
        months_data = []
        for i in range(5, -1, -1):
            m = month - i
            y = year
            while m <= 0:
                m += 12
                y -= 1
            pf, pt = self._period_bounds(y, m)

            cc_query = select(func.coalesce(func.sum(Transaction.billing_amount), 0)).where(
                Transaction.transaction_date.between(pf, pt),
                Transaction.debit_credit == "D",
            )
            if user_id is not None:
                cc_query = cc_query.where(Transaction.user_id == user_id)
            if account_id:
                cc_query = cc_query.where(Transaction.account_id == account_id)
            cc_q = await self.db.execute(cc_query)
            inc_query = select(func.coalesce(func.sum(DailyIncome.amount), 0)).where(
                DailyIncome.transaction_date.between(pf, pt),
            )
            if user_id is not None:
                inc_query = inc_query.where(DailyIncome.user_id == user_id)
            inc_q = await self.db.execute(inc_query)
            cash_query = select(func.coalesce(func.sum(DailyExpense.amount), 0)).where(
                DailyExpense.transaction_date.between(pf, pt),
                DailyExpense.ai_status == "processed",
            )
            if user_id is not None:
                cash_query = cash_query.where(DailyExpense.user_id == user_id)
            cash_q = await self.db.execute(cash_query)
            m_cc = round(float(cc_q.scalar() or 0), 2)
            m_inc = round(float(inc_q.scalar() or 0), 2)
            m_cash = round(float(cash_q.scalar() or 0), 2)
            months_data.append({
                "month": f"{y}-{m:02d}",
                "income": m_inc,
                "credit_card": m_cc,
                "cash": m_cash,
                "net": round(m_inc - m_cc - m_cash, 2),
            })

        return {
            "income_total": round(income_total, 2),
            "credit_card_total": round(cc_total, 2),
            "cash_total": round(cash_total, 2),
            "combined_expense_total": combined_expense,
            "net_flow": net_flow,
            "savings_rate_pct": savings_rate_pct,
            "months": months_data,
        }

    # ------------------------------------------------------------------
    # Report: Payment Method Distribution (monthly)
    # ------------------------------------------------------------------

    async def payment_method_distribution(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """All spending grouped by payment method (credit card + cash methods)."""
        period_from, period_to = self._period_bounds(year, month)

        # Credit card counts as one method
        cc_txns = await self._get_debit_transactions(period_from, period_to, account_id, user_id=user_id)
        cc_total = sum(float(t.billing_amount or t.amount or 0) for t in cc_txns)

        # Daily expenses by payment method
        expenses = await self._get_daily_expenses(period_from, period_to, user_id=user_id)
        pm_dist: Dict[str, Dict[str, Any]] = {}
        for e in expenses:
            pm = e.payment_method or "cash"
            amount = float(e.amount or 0)
            if pm not in pm_dist:
                pm_dist[pm] = {"amount": 0.0, "txns": 0}
            pm_dist[pm]["amount"] += amount
            pm_dist[pm]["txns"] += 1

        # Add credit card as a method
        if cc_total > 0:
            pm_dist["credit_card"] = {"amount": cc_total, "txns": len(cc_txns)}

        total = sum(v["amount"] for v in pm_dist.values())
        methods = []
        for pm, data in sorted(pm_dist.items(), key=lambda x: x[1]["amount"], reverse=True):
            pct = round(data["amount"] / total * 100, 1) if total else 0
            methods.append({
                "method": pm,
                "amount": round(data["amount"], 2),
                "pct": pct,
                "txns": data["txns"],
            })

        return {"methods": methods, "total": round(total, 2)}

    # ------------------------------------------------------------------
    # Report: Budget Burn-Down (monthly)
    # ------------------------------------------------------------------

    async def budget_burndown(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Daily cumulative spend vs linear budget pace."""
        period_from, period_to = self._period_bounds(year, month)
        total_days = (period_to - period_from).days + 1

        # Total budget
        budget_query = select(func.coalesce(func.sum(Budget.monthly_limit), 0)).where(
            Budget.is_active == True,
        )
        if user_id is not None:
            budget_query = budget_query.where(Budget.user_id == user_id)
        if account_id:
            budget_query = budget_query.where(Budget.account_id == account_id)
        budget_result = await self.db.execute(budget_query)
        total_budget = float(budget_result.scalar() or 0)

        # Daily spend (credit card)
        cc_txns = await self._get_debit_transactions(period_from, period_to, account_id, user_id=user_id)
        daily_cc: Dict[int, float] = {}
        for t in cc_txns:
            day = t.transaction_date.day
            daily_cc[day] = daily_cc.get(day, 0) + float(t.billing_amount or t.amount or 0)

        # Daily spend (cash expenses)
        expenses = await self._get_daily_expenses(period_from, period_to, user_id=user_id)
        daily_cash: Dict[int, float] = {}
        for e in expenses:
            day = e.transaction_date.day
            daily_cash[day] = daily_cash.get(day, 0) + float(e.amount or 0)

        today = date.today()
        days = []
        cumulative = 0.0
        for day_num in range(1, total_days + 1):
            day_date = date(year, month, day_num)
            is_future = day_date > today
            daily_spend = daily_cc.get(day_num, 0) + daily_cash.get(day_num, 0)
            cumulative += daily_spend
            pace = round(total_budget / total_days * day_num, 2) if total_budget > 0 else 0
            days.append({
                "day": day_num,
                "spend": round(daily_spend, 2),
                "cumulative": round(cumulative, 2),
                "pace": pace,
                "is_future": is_future,
            })

        # Project end-of-month if we have data so far
        elapsed = (min(today, period_to) - period_from).days + 1
        total_spent_so_far = cumulative if today >= period_from else 0
        projected_end = round(total_spent_so_far / elapsed * total_days, 2) if elapsed > 0 and total_spent_so_far > 0 else 0

        return {
            "days": days,
            "total_budget": round(total_budget, 2),
            "total_spent": round(total_spent_so_far, 2),
            "projected_end": projected_end,
            "has_budget": total_budget > 0,
        }

    # ------------------------------------------------------------------
    # Yearly Dashboard methods (12-month aggregated views)
    # ------------------------------------------------------------------

    async def yearly_monthly_totals(
        self,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
        payment_source: str = "all",
    ) -> List[Dict[str, Any]]:
        """12-month bar chart data: [{month: 'YYYY-MM', total: N}, ...]."""
        cc_totals = await self._monthly_totals(12, account_id, user_id=user_id)
        if payment_source == "card":
            return cc_totals

        cash_totals = await self._daily_expense_monthly_totals(12, user_id=user_id)
        if payment_source == "cash":
            return cash_totals

        # Merge both by month
        cash_map = {m["month"]: m["total"] for m in cash_totals}
        return [
            {
                "month": m["month"],
                "total": round(m["total"] + cash_map.get(m["month"], 0), 2),
            }
            for m in cc_totals
        ]

    async def yearly_category_breakdown(
        self,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
        payment_source: str = "all",
    ) -> Dict[str, Any]:
        """Aggregated category distribution across the last 12 months."""
        today = date.today()
        start = today - timedelta(days=365)

        by_cat: Dict[str, float] = {}
        if payment_source in ("all", "card"):
            txns = await self._get_debit_transactions(start, today, account_id, user_id=user_id)
            cc_cat = await self._category_distribution(txns)
            for cat, amt in cc_cat.items():
                by_cat[cat] = by_cat.get(cat, 0) + amt
        if payment_source in ("all", "cash"):
            expenses = await self._get_daily_expenses(start, today, user_id=user_id)
            cash_cat = self._daily_expense_category_distribution(expenses)
            for cat, amt in cash_cat.items():
                by_cat[cat] = by_cat.get(cat, 0) + amt

        total = sum(by_cat.values())

        categories = []
        for cat, amount in sorted(by_cat.items(), key=lambda x: x[1], reverse=True):
            pct = round(amount / total * 100, 1) if total else 0
            categories.append({
                "name": cat,
                "amount": round(amount, 2),
                "pct": pct,
            })

        return {
            "categories": categories,
            "total": round(total, 2),
        }

    async def yearly_top_merchants(
        self,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
        payment_source: str = "all",
    ) -> Dict[str, Any]:
        """Top 10 merchants across the last 12 months."""
        today = date.today()
        start = today - timedelta(days=365)

        merchant_data: Dict[str, Dict[str, Any]] = {}
        total = 0.0
        if payment_source in ("all", "card"):
            txns = await self._get_debit_transactions(start, today, account_id, user_id=user_id)
            for t in txns:
                name = t.merchant_name or (t.description_raw or "")[:30]
                amount = float(t.billing_amount or t.amount or 0)
                total += amount
                if name not in merchant_data:
                    merchant_data[name] = {"amount": 0.0, "txns": 0}
                merchant_data[name]["amount"] += amount
                merchant_data[name]["txns"] += 1
        if payment_source in ("all", "cash"):
            expenses = await self._get_daily_expenses(start, today, user_id=user_id)
            for e in expenses:
                name = (e.description_raw or "Cash expense")[:30]
                amount = float(e.amount or 0)
                total += amount
                if name not in merchant_data:
                    merchant_data[name] = {"amount": 0.0, "txns": 0}
                merchant_data[name]["amount"] += amount
                merchant_data[name]["txns"] += 1

        sorted_merchants = sorted(
            merchant_data.items(), key=lambda x: x[1]["amount"], reverse=True
        )[:10]

        merchants = []
        for name, data in sorted_merchants:
            pct = round(data["amount"] / total * 100, 1) if total else 0
            merchants.append({
                "name": name,
                "amount": round(data["amount"], 2),
                "pct": pct,
                "txns": data["txns"],
            })

        return {
            "merchants": merchants,
            "total_merchants": len(merchant_data),
        }

    async def yearly_subscription_summary(
        self,
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Use SubscriptionDetector for yearly subscription analysis with frequency detection."""
        detector = SubscriptionDetector(self.db)
        return await detector.detect_yearly_subscriptions(account_id)

    async def yearly_account_comparison(
        self,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Spending per account across months (for multi-card users)."""
        from app.models import Account

        # Get all active accounts
        acct_query = select(Account).where(Account.is_active == True).order_by(Account.id)
        if user_id is not None:
            acct_query = acct_query.where(Account.user_id == user_id)
        acct_result = await self.db.execute(acct_query)
        accounts = acct_result.scalars().all()

        if account_id:
            accounts = [a for a in accounts if a.id == account_id]

        today = date.today()
        monthly_data = await self._monthly_totals(12, account_id, user_id=user_id)
        months = [m["month"] for m in monthly_data]

        account_series = []
        for acct in accounts:
            label = acct.account_nickname or f"{acct.account_type} {acct.account_number_masked}"
            series: List[float] = []
            for m_info in monthly_data:
                year, month = m_info["month"].split("-")
                period_from, period_to = self._period_bounds(int(year), int(month))
                query = select(
                    func.coalesce(func.sum(Transaction.billing_amount), 0)
                ).where(
                    Transaction.transaction_date.between(period_from, period_to),
                    Transaction.debit_credit == "D",
                    Transaction.account_id == acct.id,
                )
                if user_id is not None:
                    query = query.where(Transaction.user_id == user_id)
                result = await self.db.execute(query)
                series.append(round(float(result.scalar() or 0), 2))
            account_series.append({
                "account_id": acct.id,
                "label": label,
                "data": series,
            })

        return {
            "months": months,
            "accounts": account_series,
            "has_multiple": len(account_series) > 1,
        }

    async def yearly_summary_stats(
        self,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
        payment_source: str = "all",
    ) -> Dict[str, Any]:
        """KPI cards: total spent, avg monthly, highest/lowest month, total txns, unique merchants."""
        today = date.today()
        start = today - timedelta(days=365)

        total_spent = 0.0
        total_txns = 0
        unique_names: set = set()

        if payment_source in ("all", "card"):
            txns = await self._get_debit_transactions(start, today, account_id, user_id=user_id)
            total_spent += sum(float(t.billing_amount or t.amount or 0) for t in txns)
            total_txns += len(txns)
            unique_names.update(
                t.merchant_name or (t.description_raw or "")[:30] for t in txns
            )
        if payment_source in ("all", "cash"):
            expenses = await self._get_daily_expenses(start, today, user_id=user_id)
            total_spent += sum(float(e.amount or 0) for e in expenses)
            total_txns += len(expenses)
            unique_names.update((e.description_raw or "Cash expense")[:30] for e in expenses)

        # Monthly breakdown for avg/highest/lowest
        monthly = await self.yearly_monthly_totals(account_id, user_id=user_id, payment_source=payment_source)
        totals = [m["total"] for m in monthly if m["total"] > 0]
        avg_monthly = round(sum(totals) / len(totals), 2) if totals else 0

        highest_month = max(monthly, key=lambda m: m["total"]) if monthly else {"month": "—", "total": 0}
        lowest_month = min([m for m in monthly if m["total"] > 0], key=lambda m: m["total"]) if any(m["total"] > 0 for m in monthly) else {"month": "—", "total": 0}

        return {
            "total_spent": round(total_spent, 2),
            "avg_monthly": avg_monthly,
            "highest_month": highest_month,
            "lowest_month": lowest_month,
            "total_transactions": total_txns,
            "unique_merchants": len(unique_names),
        }

    # ------------------------------------------------------------------
    # Yearly: Cash Expense Reports
    # ------------------------------------------------------------------

    async def yearly_cash_expense_totals(
        self,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """12-month daily expense totals [{month, total}, ...]."""
        return await self._daily_expense_monthly_totals(12, user_id=user_id)

    async def yearly_cash_expense_categories(
        self,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Aggregated category breakdown across last 12 months of daily expenses."""
        today = date.today()
        start = today - timedelta(days=365)
        expenses = await self._get_daily_expenses(start, today, user_id=user_id)
        by_cat = self._daily_expense_category_distribution(expenses)
        total = sum(by_cat.values())

        categories = []
        for cat, amount in sorted(by_cat.items(), key=lambda x: x[1], reverse=True):
            pct = round(amount / total * 100, 1) if total else 0
            categories.append({"name": cat, "amount": round(amount, 2), "pct": pct})

        return {"categories": categories, "total": round(total, 2)}

    # ------------------------------------------------------------------
    # Yearly: Income Reports
    # ------------------------------------------------------------------

    async def yearly_income_totals(
        self,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """12-month income totals [{month, total}, ...]."""
        return await self._income_monthly_totals(12, user_id=user_id)

    async def yearly_income_sources(
        self,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Aggregated income by source_type across last 12 months."""
        today = date.today()
        start = today - timedelta(days=365)
        entries = await self._get_daily_income(start, today, user_id=user_id)
        by_src = self._income_source_distribution(entries)
        total = sum(by_src.values())
        count = len(entries)

        sources = []
        for src, amount in sorted(by_src.items(), key=lambda x: x[1], reverse=True):
            pct = round(amount / total * 100, 1) if total else 0
            sources.append({"name": src, "amount": round(amount, 2), "pct": pct})

        return {
            "sources": sources,
            "total": round(total, 2),
            "count": count,
        }

    # ------------------------------------------------------------------
    # Yearly: Income vs Expense (12-month comparison)
    # ------------------------------------------------------------------

    async def yearly_income_vs_expense(
        self,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """12-month comparison: income vs credit card vs cash, with net flow."""
        cc_totals = await self._monthly_totals(12, account_id, user_id=user_id)
        cash_totals = await self._daily_expense_monthly_totals(12, user_id=user_id)
        income_totals = await self._income_monthly_totals(12, user_id=user_id)

        # Align all three series by month key
        cc_map = {m["month"]: m["total"] for m in cc_totals}
        cash_map = {m["month"]: m["total"] for m in cash_totals}
        inc_map = {m["month"]: m["total"] for m in income_totals}

        months = [m["month"] for m in cc_totals]
        months_data = []
        total_income = 0.0
        total_cc = 0.0
        total_cash = 0.0
        for month in months:
            inc = inc_map.get(month, 0)
            cc = cc_map.get(month, 0)
            cash = cash_map.get(month, 0)
            net = round(inc - cc - cash, 2)
            months_data.append({
                "month": month,
                "income": inc,
                "credit_card": cc,
                "cash": cash,
                "net": net,
            })
            total_income += inc
            total_cc += cc
            total_cash += cash

        total_expenses = total_cc + total_cash
        net_total = total_income - total_expenses
        savings_rate_pct = round(net_total / total_income * 100, 1) if total_income > 0 else 0

        return {
            "months": months_data,
            "summary": {
                "total_income": round(total_income, 2),
                "total_credit_card": round(total_cc, 2),
                "total_cash": round(total_cash, 2),
                "total_expenses": round(total_expenses, 2),
                "net": round(net_total, 2),
                "savings_rate_pct": savings_rate_pct,
            },
        }

    # ------------------------------------------------------------------
    # Yearly: Payment Method Mix
    # ------------------------------------------------------------------

    async def yearly_payment_method_mix(
        self,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """All spending by payment method over 12 months."""
        today = date.today()
        start = today - timedelta(days=365)

        # Credit card total
        cc_query = select(func.coalesce(func.sum(Transaction.billing_amount), 0)).where(
            Transaction.transaction_date.between(start, today),
            Transaction.debit_credit == "D",
        )
        if user_id is not None:
            cc_query = cc_query.where(Transaction.user_id == user_id)
        if account_id:
            cc_query = cc_query.where(Transaction.account_id == account_id)
        cc_result = await self.db.execute(cc_query)
        cc_total = float(cc_result.scalar() or 0)

        # Daily expenses by payment method
        expenses = await self._get_daily_expenses(start, today, user_id=user_id)
        pm_dist: Dict[str, float] = {}
        for e in expenses:
            pm = e.payment_method or "cash"
            pm_dist[pm] = pm_dist.get(pm, 0) + float(e.amount or 0)

        if cc_total > 0:
            pm_dist["credit_card"] = cc_total

        total = sum(pm_dist.values())
        methods = []
        for pm, amount in sorted(pm_dist.items(), key=lambda x: x[1], reverse=True):
            pct = round(amount / total * 100, 1) if total else 0
            methods.append({"method": pm, "amount": round(amount, 2), "pct": pct})

        return {"methods": methods, "total": round(total, 2)}

    # ------------------------------------------------------------------
    # Yearly: Unified KPIs (income + all expenses)
    # ------------------------------------------------------------------

    async def yearly_unified_kpis(
        self,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Total Income, Total Cash Expenses, Net Cash Flow, Savings Rate."""
        today = date.today()
        start = today - timedelta(days=365)

        # Credit card total
        cc_query = select(func.coalesce(func.sum(Transaction.billing_amount), 0)).where(
            Transaction.transaction_date.between(start, today),
            Transaction.debit_credit == "D",
        )
        if user_id is not None:
            cc_query = cc_query.where(Transaction.user_id == user_id)
        if account_id:
            cc_query = cc_query.where(Transaction.account_id == account_id)
        cc_result = await self.db.execute(cc_query)
        cc_total = float(cc_result.scalar() or 0)

        # Cash expenses
        expenses = await self._get_daily_expenses(start, today, user_id=user_id)
        cash_total = sum(float(e.amount or 0) for e in expenses)

        # Income
        income_entries = await self._get_daily_income(start, today, user_id=user_id)
        income_total = sum(float(e.amount or 0) for e in income_entries)

        total_expenses = cc_total + cash_total
        net_flow = income_total - total_expenses
        savings_rate_pct = round(net_flow / income_total * 100, 1) if income_total > 0 else 0

        return {
            "total_income": round(income_total, 2),
            "total_cash_expenses": round(cash_total, 2),
            "total_credit_card": round(cc_total, 2),
            "net_cash_flow": round(net_flow, 2),
            "savings_rate_pct": savings_rate_pct,
        }

    async def generate_yearly_dashboard(
        self,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
        payment_source: str = "all",
    ) -> Dict[str, Any]:
        """Run all yearly dashboard reports and return as a single dict."""
        return {
            "monthly_totals": await self.yearly_monthly_totals(account_id, user_id=user_id, payment_source=payment_source),
            "category_breakdown": await self.yearly_category_breakdown(account_id, user_id=user_id, payment_source=payment_source),
            "top_merchants": await self.yearly_top_merchants(account_id, user_id=user_id, payment_source=payment_source),
            "subscription_summary": await self.yearly_subscription_summary(account_id),
            "account_comparison": await self.yearly_account_comparison(account_id, user_id=user_id),
            "summary_stats": await self.yearly_summary_stats(account_id, user_id=user_id, payment_source=payment_source),
            # Cash + income additions
            "cash_expense_totals": await self.yearly_cash_expense_totals(user_id=user_id),
            "cash_expense_categories": await self.yearly_cash_expense_categories(user_id=user_id),
            "income_totals": await self.yearly_income_totals(user_id=user_id),
            "income_sources": await self.yearly_income_sources(user_id=user_id),
            "income_vs_expense": await self.yearly_income_vs_expense(account_id, user_id=user_id),
            "payment_method_mix": await self.yearly_payment_method_mix(account_id, user_id=user_id),
            "unified_kpis": await self.yearly_unified_kpis(account_id, user_id=user_id),
        }

    # ------------------------------------------------------------------
    # Convenience: generate all 6 reports at once
    # ------------------------------------------------------------------

    async def generate_all(
        self,
        year: int,
        month: int,
        account_id: Optional[int] = None,
        user_id: Optional[int] = None,
        payment_source: str = "all",
    ) -> Dict[str, Any]:
        """Run all 6 reports and return as a single dict."""
        return {
            "monthly_spending": await self.monthly_spending_breakdown(year, month, account_id, user_id=user_id, payment_source=payment_source),
            "merchant_concentration": await self.merchant_concentration(year, month, account_id, user_id=user_id, payment_source=payment_source),
            "subscription_waste": await self.subscription_waste(year, month, account_id, user_id=user_id, payment_source=payment_source),
            "lifestyle_creep": await self.lifestyle_creep(year, month, account_id, user_id=user_id),
            "health_score": await self.financial_health_score(year, month, account_id, user_id=user_id),
            "no_spend_tracker": await self.no_spend_day_tracker(year, month, account_id, user_id=user_id),
            # Cash + income additions
            "cash_expense_breakdown": await self.cash_expense_breakdown(year, month, user_id=user_id),
            "income_summary": await self.income_summary(year, month, user_id=user_id),
            "income_vs_expense": await self.income_vs_expense(year, month, account_id, user_id=user_id),
            "payment_method_distribution": await self.payment_method_distribution(year, month, account_id, user_id=user_id),
            "budget_burndown": await self.budget_burndown(year, month, account_id, user_id=user_id),
            "period": {"year": year, "month": month},
        }
