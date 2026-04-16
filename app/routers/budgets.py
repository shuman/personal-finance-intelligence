"""
Budgets router — manage monthly spending budgets per category.
"""
from typing import List, Optional
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel
from datetime import date
from collections import defaultdict

from app.database import get_db
from app.utils.auth import get_current_user
from app.models import User
from app.models import Budget, Transaction
from app.config import settings

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


class BudgetCreate(BaseModel):
    category: str
    subcategory: Optional[str] = None
    monthly_limit: float
    currency: str = "BDT"
    alert_at_pct: int = 80
    account_id: Optional[int] = None


class BudgetUpdate(BaseModel):
    monthly_limit: Optional[float] = None
    alert_at_pct: Optional[int] = None
    is_active: Optional[bool] = None


class BudgetResponse(BaseModel):
    id: int
    category: str
    subcategory: Optional[str]
    monthly_limit: Decimal
    currency: str
    alert_at_pct: int
    account_id: Optional[int]
    is_active: bool

    class Config:
        from_attributes = True


@router.get("", response_model=List[BudgetResponse])
async def list_budgets(db: AsyncSession = Depends(get_db)):
    """List all active budgets."""
    result = await db.execute(
        select(Budget).where(Budget.is_active == True).order_by(Budget.category)
    )
    return result.scalars().all()


@router.post("", response_model=BudgetResponse)
async def create_budget(body: BudgetCreate, db: AsyncSession = Depends(get_db)):
    """Create a new monthly budget for a category."""
    # Check for existing budget for same category + account
    existing = await db.execute(
        select(Budget).where(
            Budget.category == body.category,
            Budget.account_id == body.account_id,
            Budget.is_active == True,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"A budget for '{body.category}' already exists. Use PUT to update it."
        )

    budget = Budget(
        category=body.category,
        subcategory=body.subcategory,
        monthly_limit=Decimal(str(body.monthly_limit)),
        currency=body.currency,
        alert_at_pct=body.alert_at_pct,
        account_id=body.account_id,
        is_active=True,
    )
    db.add(budget)
    await db.commit()
    await db.refresh(budget)
    return budget


@router.put("/{budget_id}", response_model=BudgetResponse)
async def update_budget(
    budget_id: int, body: BudgetUpdate, db: AsyncSession = Depends(get_db)
):
    """Update a budget."""
    result = await db.execute(select(Budget).where(Budget.id == budget_id))
    budget = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")

    for field, value in body.model_dump(exclude_none=True).items():
        if field == "monthly_limit":
            value = Decimal(str(value))
        setattr(budget, field, value)

    await db.commit()
    await db.refresh(budget)
    return budget


@router.delete("/{budget_id}")
async def delete_budget(budget_id: int, db: AsyncSession = Depends(get_db)):
    """Deactivate a budget."""
    result = await db.execute(select(Budget).where(Budget.id == budget_id))
    budget = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")
    budget.is_active = False
    await db.commit()
    return {"success": True, "message": f"Budget {budget_id} deactivated"}


@router.get("/status")
async def get_budget_status(
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get current month's spending vs budget for each category.
    Defaults to current month.
    """
    today = date.today()
    year = year or today.year
    month = month or today.month

    period_from = date(year, month, 1)
    if month == 12:
        period_to = date(year + 1, 1, 1)
    else:
        period_to = date(year, month + 1, 1)

    # Get all active budgets
    result = await db.execute(select(Budget).where(Budget.is_active == True))
    budgets = result.scalars().all()

    # Get current spending by category
    txn_result = await db.execute(
        select(Transaction).where(
            Transaction.transaction_date >= period_from,
            Transaction.transaction_date < period_to,
            Transaction.debit_credit == "D",
        )
    )
    txns = txn_result.scalars().all()

    spending_by_cat: dict = defaultdict(float)
    for t in txns:
        cat = t.category_ai or t.merchant_category or "Other"
        spending_by_cat[cat] += float(t.billing_amount or t.amount or 0)

    status_list = []
    for budget in budgets:
        spent = spending_by_cat.get(budget.category, 0)
        limit = float(budget.monthly_limit)
        pct_used = (spent / limit * 100) if limit > 0 else 0

        status_list.append({
            "id": budget.id,
            "category": budget.category,
            "monthly_limit": limit,
            "spent": round(spent, 2),
            "remaining": round(max(0, limit - spent), 2),
            "pct_used": round(pct_used, 1),
            "status": (
                "exceeded" if pct_used >= 100
                else "warning" if pct_used >= budget.alert_at_pct
                else "ok"
            ),
            "currency": budget.currency,
        })

    return {
        "period": f"{year}-{month:02d}",
        "budgets": sorted(status_list, key=lambda x: x["pct_used"], reverse=True),
        "total_budgeted": sum(b["monthly_limit"] for b in status_list),
        "total_spent": sum(b["spent"] for b in status_list),
    }
