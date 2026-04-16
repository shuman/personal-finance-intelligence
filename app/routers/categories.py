"""
Categories router — manages category rules and transaction category overrides.
Replaces the ML router with a persistent rule-memory system.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from datetime import datetime

from app.database import get_db
from app.utils.auth import get_current_user
from app.models import User
from app.models import CategoryRule, Transaction
from app.services.category_engine import CategoryEngine, seed_category_rules

router = APIRouter(prefix="/api/categories", tags=["categories"])


class CategoryRuleResponse(BaseModel):
    id: int
    merchant_pattern: str
    normalized_merchant: str
    category: str
    subcategory: Optional[str]
    source: str
    confidence: float
    match_count: int
    last_matched_at: Optional[datetime]
    is_active: bool

    class Config:
        from_attributes = True


class CategoryOverrideRequest(BaseModel):
    category: str
    subcategory: Optional[str] = None


class CategoryRuleUpdateRequest(BaseModel):
    merchant_pattern: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    source: Optional[str] = None
    confidence: Optional[float] = None


@router.get("/rules", response_model=List[CategoryRuleResponse])
async def list_category_rules(
    source: Optional[str] = Query(None, description="Filter by source: user_override, claude_ai, builtin"),
    category: Optional[str] = Query(None, description="Filter by category"),
    active_only: bool = Query(True),
    limit: int = Query(200, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all learned category rules."""
    query = select(CategoryRule)
    if active_only:
        query = query.where(CategoryRule.is_active == True)
    if source:
        query = query.where(CategoryRule.source == source)
    if category:
        query = query.where(CategoryRule.category == category)
    query = query.order_by(CategoryRule.source.desc(), CategoryRule.match_count.desc()).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/rules/seed")
async def seed_rules(db: AsyncSession = Depends(get_db)):
    """Seed the category_rules table with Bangladesh-relevant built-in rules."""
    await seed_category_rules(db)
    result = await db.execute(select(CategoryRule))
    count = len(result.scalars().all())
    return {"success": True, "message": f"Rules seeded. Total rules: {count}"}


@router.delete("/rules/{rule_id}")
async def deactivate_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    """Deactivate a category rule (soft delete)."""
    result = await db.execute(select(CategoryRule).where(CategoryRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.is_active = False
    await db.commit()
    return {"success": True, "message": f"Rule {rule_id} deactivated"}


@router.put("/rules/{rule_id}", response_model=CategoryRuleResponse)
async def update_rule(
    rule_id: int,
    body: CategoryRuleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update fields on an existing category rule."""
    result = await db.execute(select(CategoryRule).where(CategoryRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    if body.merchant_pattern is not None:
        rule.merchant_pattern = body.merchant_pattern
        rule.normalized_merchant = body.merchant_pattern.lower().strip()
    if body.category is not None:
        rule.category = body.category
    if body.subcategory is not None:
        rule.subcategory = body.subcategory
    if body.source is not None:
        rule.source = body.source
    if body.confidence is not None:
        rule.confidence = body.confidence

    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/transactions/{transaction_id}")
async def override_transaction_category(
    transaction_id: int,
    body: CategoryOverrideRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Override a transaction's category.
    Persists as a user_override rule so future transactions from the
    same merchant are automatically categorized correctly.
    """
    engine = CategoryEngine(db)
    success = await engine.override_category(
        transaction_id=transaction_id,
        new_category=body.category,
        new_subcategory=body.subcategory,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return {
        "success": True,
        "transaction_id": transaction_id,
        "category": body.category,
        "subcategory": body.subcategory,
        "message": "Category updated. Future similar transactions will auto-match this rule.",
    }


@router.post("/predict")
async def predict_category(
    merchant_name: Optional[str] = Query(None),
    description: str = Query(...),
    country: str = Query("BD"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Predict the category for a given merchant/description."""
    engine = CategoryEngine(db)
    category, subcategory, source, confidence = await engine.categorize(
        merchant_name=merchant_name,
        description_raw=description,
        country=country,
    )
    return {
        "category": category,
        "subcategory": subcategory,
        "source": source,
        "confidence": f"{confidence * 100:.0f}%",
    }


@router.get("/list")
async def list_categories():
    """List all standard spending categories."""
    from app.services.category_engine import CategoryEngine
    return {
        "categories": CategoryEngine.CATEGORIES,
        "count": len(CategoryEngine.CATEGORIES),
    }
