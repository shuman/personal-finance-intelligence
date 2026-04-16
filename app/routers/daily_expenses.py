"""
Daily Expenses Router - API endpoints for manual expense logging.
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
from app.services.daily_expense_service import DailyExpenseService

router = APIRouter(prefix="/api/daily-expenses", tags=["daily-expenses"])


# -----------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------

class ExpenseCreate(BaseModel):
    amount: float
    description: str
    transaction_date: Optional[date] = None
    payment_method: str = "cash"
    currency: str = "BDT"


class ExpenseUpdate(BaseModel):
    category: Optional[str] = None
    subcategory: Optional[str] = None
    description_normalized: Optional[str] = None


class BatchProcessRequest(BaseModel):
    expense_ids: List[int]


class ExpenseResponse(BaseModel):
    id: int
    amount: Decimal
    currency: str
    description_raw: str
    description_normalized: Optional[str]
    category: Optional[str]
    subcategory: Optional[str]
    tags: Optional[List[str]]
    payment_method: str
    transaction_date: date
    ai_status: str
    confidence_score: Optional[Decimal]
    needs_review: bool
    created_at: str
    enriched_at: Optional[str]

    class Config:
        from_attributes = True

    @classmethod
    def from_orm(cls, expense):
        """Custom from_orm to handle datetime serialization."""
        return cls(
            id=expense.id,
            amount=expense.amount,
            currency=expense.currency,
            description_raw=expense.description_raw,
            description_normalized=expense.description_normalized,
            category=expense.category,
            subcategory=expense.subcategory,
            tags=expense.tags,
            payment_method=expense.payment_method,
            transaction_date=expense.transaction_date,
            ai_status=expense.ai_status,
            confidence_score=expense.confidence_score,
            needs_review=expense.needs_review,
            created_at=expense.created_at.isoformat() if expense.created_at else None,
            enriched_at=expense.enriched_at.isoformat() if expense.enriched_at else None,
        )


# -----------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------

@router.post("", response_model=ExpenseResponse)
async def create_expense(
    body: ExpenseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Save a new draft expense (no AI processing).
    Fast save for quick input.
    """
    service = DailyExpenseService(db)
    expense = await service.save_draft_expense(
        amount=Decimal(str(body.amount)),
        description=body.description,
        transaction_date=body.transaction_date,
        payment_method=body.payment_method,
        currency=body.currency,
    )
    return ExpenseResponse.from_orm(expense)


@router.get("", response_model=List[ExpenseResponse])
async def list_expenses(
    status: Optional[str] = Query(None, description="Filter by ai_status (draft, pending, processed)"),
    date_from: Optional[date] = Query(None, description="Filter by date >= date_from"),
    date_to: Optional[date] = Query(None, description="Filter by date <= date_to"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List expenses with optional filters.
    """
    service = DailyExpenseService(db)
    expenses = await service.get_expenses(
        status=status,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return [ExpenseResponse.from_orm(e) for e in expenses]


@router.get("/drafts", response_model=List[ExpenseResponse])
async def get_draft_expenses(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all draft expenses (for batch processing selection UI).
    """
    service = DailyExpenseService(db)
    expenses = await service.get_expenses(status="draft", limit=limit)
    return [ExpenseResponse.from_orm(e) for e in expenses]


@router.get("/processed", response_model=List[ExpenseResponse])
async def get_processed_expenses(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get processed expenses awaiting user review.
    Only returns expenses with needs_review=True.
    """
    service = DailyExpenseService(db)
    expenses = await service.get_expenses(status="processed", needs_review=True, limit=limit)
    return [ExpenseResponse.from_orm(e) for e in expenses]


@router.post("/batch-process")
async def batch_process_expenses(
    body: BatchProcessRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Batch process selected expenses with Claude AI.

    Steps:
    1. Mark expenses as 'pending'
    2. Send all to Claude in single API call
    3. Update with AI categorization results
    4. Return processing summary
    """
    if not body.expense_ids:
        raise HTTPException(status_code=400, detail="No expense IDs provided")

    service = DailyExpenseService(db)

    # Mark for processing
    marked_count = await service.mark_for_processing(body.expense_ids)

    if marked_count == 0:
        raise HTTPException(
            status_code=400,
            detail="No draft expenses found with provided IDs"
        )

    # Batch categorize with AI
    result = await service.batch_categorize_expenses(body.expense_ids)

    return {
        "success": True,
        "marked_count": marked_count,
        "processed_count": result["success_count"],
        "failed_count": result["failed_count"],
        "cost_usd": result["total_cost_usd"],
        "expenses_processed": result["expenses_processed"],
    }


@router.get("/{expense_id}", response_model=ExpenseResponse)
async def get_expense(
    expense_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single expense by ID."""
    service = DailyExpenseService(db)
    expense = await service.get_expense_by_id(expense_id)
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    return ExpenseResponse.from_orm(expense)


@router.patch("/{expense_id}", response_model=ExpenseResponse)
async def update_expense(
    expense_id: int,
    body: ExpenseUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Apply user override/corrections to an expense.
    Stores corrections as high-priority rules for future matching.
    """
    service = DailyExpenseService(db)

    expense = await service.apply_user_override(
        expense_id=expense_id,
        category=body.category,
        subcategory=body.subcategory,
        description_normalized=body.description_normalized,
    )

    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    return ExpenseResponse.from_orm(expense)


@router.delete("/{expense_id}")
async def delete_expense(
    expense_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete an expense."""
    service = DailyExpenseService(db)
    success = await service.delete_expense(expense_id)

    if not success:
        raise HTTPException(status_code=404, detail="Expense not found")

    return {"success": True, "message": f"Expense {expense_id} deleted"}


@router.get("/stats/summary")
async def get_expense_statistics(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get expense statistics for a date range.
    Includes category breakdown, payment method breakdown, totals.
    """
    service = DailyExpenseService(db)
    stats = await service.get_statistics(date_from=date_from, date_to=date_to)
    return stats


@router.get("/options/categories")
async def get_categories():
    """
    Get available expense categories (same as statement transactions).
    """
    return {
        "categories": DailyExpenseService.CATEGORIES,
        "payment_methods": DailyExpenseService.PAYMENT_METHODS
    }
