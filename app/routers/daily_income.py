"""
Daily Income Router - API endpoints for income tracking.
"""
from typing import List, Optional
from decimal import Decimal
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.utils.auth import get_current_user
from app.models import User
from app.services.daily_income_service import DailyIncomeService

router = APIRouter(prefix="/api/daily-income", tags=["daily-income"])


# -----------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------

class IncomeCreate(BaseModel):
    amount: float
    description: str
    transaction_date: Optional[date] = None
    source_type: Optional[str] = None
    currency: str = "BDT"


class IncomeUpdate(BaseModel):
    amount: Optional[float] = None
    description: Optional[str] = None
    source_type: Optional[str] = None
    transaction_date: Optional[date] = None


class IncomeResponse(BaseModel):
    id: int
    amount: Decimal
    currency: str
    description_raw: str
    description_normalized: Optional[str]
    source_type: Optional[str]
    tags: Optional[List[str]]
    transaction_date: date
    ai_status: str
    created_at: str
    enriched_at: Optional[str]

    class Config:
        from_attributes = True

    @classmethod
    def from_orm(cls, income):
        """Custom from_orm to handle datetime serialization."""
        return cls(
            id=income.id,
            amount=income.amount,
            currency=income.currency,
            description_raw=income.description_raw,
            description_normalized=income.description_normalized,
            source_type=income.source_type,
            tags=income.tags,
            transaction_date=income.transaction_date,
            ai_status=income.ai_status,
            created_at=income.created_at.isoformat() if income.created_at else None,
            enriched_at=income.enriched_at.isoformat() if income.enriched_at else None,
        )


# -----------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------

@router.post("", response_model=IncomeResponse)
async def create_income(
    body: IncomeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Save a new income entry.
    """
    service = DailyIncomeService(db)
    income = await service.save_income(
        amount=Decimal(str(body.amount)),
        description=body.description,
        transaction_date=body.transaction_date,
        source_type=body.source_type,
        currency=body.currency,
    )
    return IncomeResponse.from_orm(income)


@router.get("", response_model=List[IncomeResponse])
async def list_income(
    date_from: Optional[date] = Query(None, description="Filter by date >= date_from"),
    date_to: Optional[date] = Query(None, description="Filter by date <= date_to"),
    source_type: Optional[str] = Query(None, description="Filter by source type"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List income entries with optional filters.
    """
    service = DailyIncomeService(db)
    income_entries = await service.get_income_entries(
        date_from=date_from,
        date_to=date_to,
        source_type=source_type,
        limit=limit,
        offset=offset,
    )
    return [IncomeResponse.from_orm(i) for i in income_entries]


@router.get("/{income_id}", response_model=IncomeResponse)
async def get_income(
    income_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single income entry by ID."""
    service = DailyIncomeService(db)
    income = await service.get_income_by_id(income_id)
    if not income:
        raise HTTPException(status_code=404, detail="Income entry not found")
    return IncomeResponse.from_orm(income)


@router.put("/{income_id}", response_model=IncomeResponse)
async def update_income(
    income_id: int,
    body: IncomeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update an income entry."""
    service = DailyIncomeService(db)

    update_data = {}
    if body.amount is not None:
        update_data["amount"] = Decimal(str(body.amount))
    if body.description is not None:
        update_data["description"] = body.description
    if body.source_type is not None:
        update_data["source_type"] = body.source_type
    if body.transaction_date is not None:
        update_data["transaction_date"] = body.transaction_date

    income = await service.update_income(income_id, **update_data)

    if not income:
        raise HTTPException(status_code=404, detail="Income entry not found")

    return IncomeResponse.from_orm(income)


@router.delete("/{income_id}")
async def delete_income(
    income_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete an income entry."""
    service = DailyIncomeService(db)
    success = await service.delete_income(income_id)

    if not success:
        raise HTTPException(status_code=404, detail="Income entry not found")

    return {"success": True, "message": f"Income entry {income_id} deleted"}


@router.get("/stats/summary")
async def get_income_statistics(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get income statistics for a date range.
    Includes source type breakdown, totals.
    """
    service = DailyIncomeService(db)
    stats = await service.get_statistics(date_from=date_from, date_to=date_to)
    return stats


@router.get("/stats/monthly/{year}/{month}")
async def get_monthly_income_summary(
    year: int,
    month: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get income summary for a specific month.
    """
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be between 1 and 12")

    service = DailyIncomeService(db)
    summary = await service.get_monthly_summary(year, month)
    return summary
