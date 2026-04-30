"""
Reports router — dashboard and individual report endpoints.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Account
from app.models import User
from app.routers.auth import get_current_user
from app.services.report_engine import ReportEngine

router = APIRouter(prefix="/api/reports", tags=["reports"])

REPORT_METHODS = {
    "monthly_spending": "monthly_spending_breakdown",
    "merchant_concentration": "merchant_concentration",
    "subscription_waste": "subscription_waste",
    "lifestyle_creep": "lifestyle_creep",
    "health_score": "financial_health_score",
    "no_spend_tracker": "no_spend_day_tracker",
    "cash_expense_breakdown": "cash_expense_breakdown",
    "income_summary": "income_summary",
    "income_vs_expense": "income_vs_expense",
    "payment_method_distribution": "payment_method_distribution",
    "budget_burndown": "budget_burndown",
}


@router.get("/dashboard")
async def dashboard(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    account_id: Optional[int] = Query(None),
    payment_source: Optional[str] = Query("all"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all 6 reports for the dashboard."""
    from datetime import date as _date
    today = _date.today()
    year = year or today.year
    month = month or today.month

    if payment_source not in ("all", "card", "cash"):
        payment_source = "all"

    engine = ReportEngine(db)
    data = await engine.generate_all(year, month, account_id, user_id=current_user.id, payment_source=payment_source)

    # Attach list of accounts for the filter dropdown
    acct_result = await db.execute(
        select(Account).where(Account.is_active == True, Account.user_id == current_user.id).order_by(Account.id)
    )
    data["accounts"] = [
        {
            "id": a.id,
            "label": a.account_nickname
            or f"{a.account_type} {a.account_number_masked}",
            "masked": a.account_number_masked,
        }
        for a in acct_result.scalars().all()
    ]

    return data


@router.get("/yearly-dashboard")
async def yearly_dashboard(
    account_id: Optional[int] = Query(None),
    payment_source: Optional[str] = Query("all"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all yearly dashboard data (12-month aggregated)."""
    if payment_source not in ("all", "card", "cash"):
        payment_source = "all"

    engine = ReportEngine(db)
    data = await engine.generate_yearly_dashboard(account_id, user_id=current_user.id, payment_source=payment_source)

    # Attach list of accounts for the filter dropdown
    acct_result = await db.execute(
        select(Account).where(Account.is_active == True, Account.user_id == current_user.id).order_by(Account.id)
    )
    data["accounts"] = [
        {
            "id": a.id,
            "label": a.account_nickname
            or f"{a.account_type} {a.account_number_masked}",
            "masked": a.account_number_masked,
        }
        for a in acct_result.scalars().all()
    ]

    return data


@router.get("/{report_id}")
async def individual_report(
    report_id: str,
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    account_id: Optional[int] = Query(None),
    payment_source: Optional[str] = Query("all"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a single report by ID for drill-down view."""
    from datetime import date as _date
    today = _date.today()
    year = year or today.year
    month = month or today.month

    if payment_source not in ("all", "card", "cash"):
        payment_source = "all"

    method_name = REPORT_METHODS.get(report_id)
    if not method_name:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown report: {report_id}. Available: {list(REPORT_METHODS.keys())}",
        )

    engine = ReportEngine(db)
    method = getattr(engine, method_name)
    kwargs = {"account_id": account_id, "user_id": current_user.id}
    # Only pass payment_source to methods that accept it
    import inspect
    sig = inspect.signature(method)
    if "payment_source" in sig.parameters:
        kwargs["payment_source"] = payment_source
    return await method(year, month, **kwargs)
