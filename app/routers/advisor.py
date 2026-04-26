"""
AI Advisor router — generate and retrieve financial insights.
"""
from typing import List, Optional
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel

from app.database import get_db
from app.models import Insight, AdvisorReport
from app.models import User
from app.routers.auth import get_current_user
from app.services.advisor import AdvisorService

router = APIRouter(prefix="/api/advisor", tags=["advisor"])


class InsightResponse(BaseModel):
    id: int
    insight_type: str
    scope: str
    period_from: Optional[date]
    period_to: Optional[date]
    account_id: Optional[int]
    title: str
    content: str
    priority: int
    is_read: bool
    created_at: date

    class Config:
        from_attributes = True


@router.post("/analyze")
async def analyze_period(
    period_from: Optional[date] = Query(None, description="Start date (defaults to first of last month)"),
    period_to: Optional[date] = Query(None, description="End date (defaults to last of last month)"),
    account_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Trigger AI analysis for a date range.
    Generates all 6 insight types and stores them in the database.
    """
    # Default to last calendar month
    today = date.today()
    if period_from is None:
        first_of_this_month = today.replace(day=1)
        period_to = first_of_this_month - timedelta(days=1)
        period_from = period_to.replace(day=1)

    if period_to is None:
        period_to = today

    advisor = AdvisorService(db)
    insights = await advisor.analyze_period(
        period_from=period_from,
        period_to=period_to,
        account_id=account_id,
        user_id=current_user.id,
    )

    return {
        "success": True,
        "period": f"{period_from} to {period_to}",
        "insights_generated": len(insights),
        "insight_ids": [i.id for i in insights],
    }


@router.get("/insights", response_model=List[InsightResponse])
async def get_insights(
    unread_only: bool = Query(False),
    insight_type: Optional[str] = Query(None),
    priority: Optional[int] = Query(None, ge=1, le=3),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List AI-generated insights. Filter by type, priority, or read status.
    """
    query = select(Insight).where(Insight.user_id == current_user.id).order_by(Insight.priority.asc(), desc(Insight.created_at))

    if unread_only:
        query = query.where(Insight.is_read == False)
    if insight_type:
        query = query.where(Insight.insight_type == insight_type)
    if priority:
        query = query.where(Insight.priority == priority)

    query = query.limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.put("/insights/{insight_id}/read")
async def mark_insight_read(
    insight_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark an insight as read."""
    result = await db.execute(select(Insight).where(Insight.id == insight_id, Insight.user_id == current_user.id))
    insight = result.scalar_one_or_none()
    if not insight:
        raise HTTPException(status_code=404, detail="Insight not found")
    insight.is_read = True
    await db.commit()
    return {"success": True}


@router.get("/monthly-report/{year}/{month}")
async def get_monthly_report(
    year: int,
    month: int,
    account_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get the monthly report for a specific year/month.
    Triggers generation if it doesn't exist yet.
    """
    period_from = date(year, month, 1)
    # Last day of month
    if month == 12:
        period_to = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        period_to = date(year, month + 1, 1) - timedelta(days=1)

    # Check if report already exists
    query = select(Insight).where(
        Insight.insight_type == "monthly_report",
        Insight.period_from == period_from,
        Insight.user_id == current_user.id,
    )
    if account_id:
        query = query.where(Insight.account_id == account_id)

    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    if existing:
        return {
            "insight_id": existing.id,
            "title": existing.title,
            "content": existing.content,
            "period": f"{period_from} to {period_to}",
            "generated_at": existing.created_at,
            "cached": True,
        }

    advisor = AdvisorService(db)
    report = await advisor._generate_monthly_report(current_user.id, period_from, period_to, account_id)

    if not report:
        raise HTTPException(
            status_code=404,
            detail="No transaction data found for this period."
        )

    return {
        "insight_id": report.id,
        "title": report.title,
        "content": report.content,
        "period": f"{period_from} to {period_to}",
        "generated_at": report.created_at,
        "cached": False,
    }


@router.get("/unread-count")
async def get_unread_count(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get count of unread insights (for badge in navigation)."""
    from sqlalchemy import func
    result = await db.execute(
        select(func.count(Insight.id)).where(Insight.is_read == False, Insight.user_id == current_user.id)
    )
    count = result.scalar() or 0
    critical = await db.execute(
        select(func.count(Insight.id)).where(
            Insight.is_read == False, Insight.priority == 1, Insight.user_id == current_user.id
        )
    )
    return {
        "total_unread": count,
        "critical_unread": critical.scalar() or 0,
    }


# ---------------------------------------------------------------------------
# NEW: AI Advisor Report endpoints (monthly cached diagnosis)
# ---------------------------------------------------------------------------

@router.get("/report/{year}/{month}")
async def get_advisor_report(
    year: int,
    month: int,
    account_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return a cached advisor report for the given month.
    Returns 404 if no report has been generated yet — does NOT auto-generate.
    Use POST /generate/{year}/{month} to trigger generation.
    """
    query = select(AdvisorReport).where(
        AdvisorReport.year == year,
        AdvisorReport.month == month,
        AdvisorReport.user_id == current_user.id,
    )
    if account_id:
        query = query.where(AdvisorReport.account_id == account_id)
    else:
        query = query.where(AdvisorReport.account_id.is_(None))
    result = await db.execute(query)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(
            status_code=404,
            detail="No report generated for this period yet."
        )

    return _serialize_report(report)


@router.get("/latest")
async def get_latest_report(
    account_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the most recent cached advisor report."""
    query = select(AdvisorReport).where(AdvisorReport.user_id == current_user.id).order_by(
        desc(AdvisorReport.year), desc(AdvisorReport.month)
    )
    if account_id:
        query = query.where(AdvisorReport.account_id == account_id)
    result = await db.execute(query.limit(1))
    report = result.scalar_one_or_none()

    if not report:
        return {"has_report": False}

    return {"has_report": True, **_serialize_report(report)}


@router.get("/reports")
async def list_reports(
    limit: int = Query(12, le=24),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all cached advisor reports (history)."""
    result = await db.execute(
        select(AdvisorReport)
        .where(AdvisorReport.user_id == current_user.id)
        .order_by(desc(AdvisorReport.year), desc(AdvisorReport.month))
        .limit(limit)
    )
    reports = result.scalars().all()
    return {
        "reports": [_serialize_report(r) for r in reports],
        "total": len(reports),
    }


@router.post("/generate/{year}/{month}")
async def force_generate_report(
    year: int,
    month: int,
    account_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate (or re-generate) an advisor report for the given month."""
    from app.config import settings
    from app.services.signal_engine import SignalEngine

    # Check API key first so we give a clear error before doing any DB work
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=500,
            detail="Anthropic API key is not configured. Add ANTHROPIC_API_KEY to your .env file and restart the server."
        )

    # Check that transaction data exists for the period
    month_names = ["January","February","March","April","May","June",
                   "July","August","September","October","November","December"]

    # Generation is allowed only for past months.
    # Current/future months are blocked entirely.
    advisor = AdvisorService(db)
    existing = await advisor._get_cached_report(current_user.id, year, month, account_id)
    today = date.today()
    is_past_month = (year, month) < (today.year, today.month)

    if not is_past_month:
        raise HTTPException(
            status_code=400,
            detail="Advisor report generation is only available for past months."
        )

    sig_engine = SignalEngine(db)
    signals = await sig_engine.compute_all_signals(current_user.id, year, month, account_id)
    if not signals.get("has_data"):
        raise HTTPException(
            status_code=404,
            detail=(
                f"No credit card transaction data found for {month_names[month-1]} {year}. "
                f"Upload a statement for this month first, or switch to a month that has data."
            )
        )

    report = await advisor.generate_advisor_report(
        current_user.id,
        year,
        month,
        account_id,
        force_regenerate=bool(existing and is_past_month),
    )

    if not report:
        raise HTTPException(
            status_code=500,
            detail="Report generation failed. The AI returned an unexpected response. Check server logs for details."
        )

    return _serialize_report(report)


def _serialize_report(report: AdvisorReport) -> dict:
    """Serialize an AdvisorReport to a JSON-friendly dict."""
    return {
        "id": report.id,
        "year": report.year,
        "month": report.month,
        "account_id": report.account_id,
        "diagnosis": report.diagnosis,
        "score": report.score,
        "score_breakdown": report.score_breakdown,
        "score_prev": report.score_prev,
        "score_delta": report.score_delta,
        "insights": report.insights,
        "mistakes": report.mistakes,
        "recommendations": report.recommendations,
        "risks": report.risks,
        "personality_type": report.personality_type,
        "personality_detail": report.personality_detail,
        "top_recommendation": report.top_recommendation,
        "projection": report.projection,
        "advisor_notes": report.advisor_notes,
        # Holistic income & savings fields
        "income_insights": report.income_insights,
        "income_tips": report.income_tips,
        "savings_analysis": report.savings_analysis,
        "motivation": report.motivation,
        "ai_cost_usd": float(report.ai_cost_usd) if report.ai_cost_usd else None,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }
