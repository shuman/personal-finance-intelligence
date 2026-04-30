"""
Daily Income Service - Manual income tracking.

Simpler than expenses: fewer categories, minimal AI processing needed.
Users log income sources with optional AI enhancement.
"""
import logging
import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Dict, Any

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyIncome

logger = logging.getLogger(__name__)


class DailyIncomeService:
    """Service for managing daily income transactions."""

    # Income source types
    SOURCE_TYPES = [
        "salary",
        "freelance",
        "business",
        "investment",
        "gift",
        "side_income",
        "refund",
        "bonus",
        "other",
    ]

    def __init__(self, db: AsyncSession):
        self.db = db

    # -----------------------------------------------------------------------
    # CRUD Operations
    # -----------------------------------------------------------------------

    async def save_income(
        self,
        user_id: int,
        amount: Decimal,
        description: str,
        transaction_date: Optional[date] = None,
        source_type: Optional[str] = None,
        currency: str = "BDT",
    ) -> DailyIncome:
        """
        Save a new income entry.
        Returns the created income object.
        """
        if transaction_date is None:
            transaction_date = date.today()

        if source_type and source_type not in self.SOURCE_TYPES:
            source_type = "other"

        income = DailyIncome(
            uuid=str(uuid.uuid4()),
            user_id=user_id,
            amount=amount,
            currency=currency,
            description_raw=description.strip(),
            source_type=source_type,
            transaction_date=transaction_date,
            ai_status="processed",
            created_at=datetime.utcnow(),
        )

        self.db.add(income)
        await self.db.commit()
        await self.db.refresh(income)

        logger.info(f"Saved income: {income.id} - {amount} {currency} - {description[:30]}")
        return income

    async def get_income_entries(
        self,
        user_id: int,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        source_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[DailyIncome]:
        """
        Get income entries with optional filters.

        Args:
            user_id: Filter by user ownership
            date_from: Filter by transaction_date >= date_from
            date_to: Filter by transaction_date <= date_to
            source_type: Filter by source_type
            limit: Maximum number of results
            offset: Offset for pagination
        """
        query = select(DailyIncome).where(DailyIncome.user_id == user_id)

        # Build filters
        filters = []
        if date_from:
            filters.append(DailyIncome.transaction_date >= date_from)
        if date_to:
            filters.append(DailyIncome.transaction_date <= date_to)
        if source_type:
            filters.append(DailyIncome.source_type == source_type)

        if filters:
            query = query.where(and_(*filters))

        # Order by most recent first
        query = query.order_by(desc(DailyIncome.created_at))
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_income_by_id(self, income_id: int, user_id: Optional[int] = None) -> Optional[DailyIncome]:
        """Get a single income entry by ID (optionally scoped to user)."""
        query = select(DailyIncome).where(DailyIncome.id == income_id)
        if user_id is not None:
            query = query.where(DailyIncome.user_id == user_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def update_income(
        self,
        income_id: int,
        user_id: int,
        amount: Optional[Decimal] = None,
        description: Optional[str] = None,
        source_type: Optional[str] = None,
        transaction_date: Optional[date] = None,
    ) -> Optional[DailyIncome]:
        """Update an income entry."""
        income = await self.get_income_by_id(income_id, user_id=user_id)
        if not income:
            return None

        if amount is not None:
            income.amount = amount
        if description is not None:
            income.description_raw = description.strip()
        if source_type is not None:
            if source_type in self.SOURCE_TYPES:
                income.source_type = source_type
        if transaction_date is not None:
            income.transaction_date = transaction_date

        income.ai_status = "processed"
        income.enriched_at = datetime.utcnow()

        await self.db.commit()
        await self.db.refresh(income)

        logger.info(f"Updated income: {income_id}")
        return income

    async def delete_income(self, income_id: int, user_id: int) -> bool:
        """Delete an income entry."""
        income = await self.get_income_by_id(income_id, user_id=user_id)
        if not income:
            return False

        await self.db.delete(income)
        await self.db.commit()
        logger.info(f"Deleted income: {income_id}")
        return True

    # -----------------------------------------------------------------------
    # Statistics
    # -----------------------------------------------------------------------

    async def get_statistics(
        self,
        user_id: int,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        Get income statistics for a date range.
        """
        query = select(DailyIncome).where(DailyIncome.user_id == user_id)

        filters = []
        if date_from:
            filters.append(DailyIncome.transaction_date >= date_from)
        if date_to:
            filters.append(DailyIncome.transaction_date <= date_to)

        if filters:
            query = query.where(and_(*filters))

        result = await self.db.execute(query)
        income_entries = list(result.scalars().all())

        total_amount = sum(e.amount for e in income_entries)
        source_type_breakdown = {}

        for income in income_entries:
            # Source type breakdown
            source = income.source_type or "Unspecified"
            if source not in source_type_breakdown:
                source_type_breakdown[source] = Decimal("0")
            source_type_breakdown[source] += income.amount

        return {
            "total_count": len(income_entries),
            "total_amount": float(total_amount),
            "source_breakdown": {k: float(v) for k, v in source_type_breakdown.items()},
            "average_income": float(total_amount / len(income_entries)) if income_entries else 0,
        }

    async def get_monthly_summary(self, user_id: int, year: int, month: int) -> Dict[str, Any]:
        """
        Get income summary for a specific month.
        """
        from calendar import monthrange

        # Get first and last day of month
        first_day = date(year, month, 1)
        last_day = date(year, month, monthrange(year, month)[1])

        stats = await self.get_statistics(user_id=user_id, date_from=first_day, date_to=last_day)

        return {
            "year": year,
            "month": month,
            "month_name": first_day.strftime("%B"),
            **stats,
        }
