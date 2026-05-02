"""
Statements router - view and query statements and transactions.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import csv
import io
import os
import shutil

from app.database import get_db
from app.services import StatementService
from app.services.money_events import MoneyEventQuery, EventSource, Direction
from app.routers.auth import get_current_user
from app.models import User
from pydantic import BaseModel
from datetime import date, timedelta
from decimal import Decimal


router = APIRouter(prefix="/api", tags=["statements"])


# Pydantic models for responses
class StatementSummary(BaseModel):
    id: int
    filename: str
    bank_name: str
    card_type: Optional[str] = None
    account_number: str
    statement_date: Optional[date]
    statement_period_from: Optional[date] = None
    statement_period_to: Optional[date] = None
    payment_due_date: Optional[date] = None
    total_amount_due: Optional[Decimal]
    new_balance: Optional[Decimal]
    credit_utilization_pct: Optional[Decimal]
    transaction_count: int

    class Config:
        from_attributes = True


class TransactionDetail(BaseModel):
    id: int
    transaction_date: date
    description_raw: str
    merchant_name: Optional[str]
    merchant_category: Optional[str]
    category_ai: Optional[str] = None
    category_manual: Optional[str] = None
    category_source: Optional[str] = None
    amount: Decimal
    transaction_type: str
    debit_credit: Optional[str] = None
    account_id: Optional[int] = None
    account_number: Optional[str] = None
    card_last_four: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("/statements", response_model=List[StatementSummary])
async def list_statements(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get list of all uploaded statements for the current user.

    - **limit**: Maximum number of statements to return (default: 100, max: 500)
    - **offset**: Number of statements to skip (for pagination)

    Returns list of statement summaries.
    """
    from sqlalchemy import select, func
    from app.models import Statement, Transaction

    service = StatementService(db)
    statements = await service.get_all_statements(limit=limit, offset=offset, user_id=current_user.id)

    # Add transaction count to each statement
    result = []
    for stmt in statements:
        # Count transactions separately
        count_result = await db.execute(
            select(func.count(Transaction.id)).where(Transaction.statement_id == stmt.id)
        )
        txn_count = count_result.scalar()

        stmt_dict = {
            "id": stmt.id,
            "filename": stmt.filename,
            "bank_name": stmt.bank_name,
            "card_type": stmt.card_type,
            "account_number": stmt.account_number,
            "statement_date": stmt.statement_date,
            "statement_period_from": stmt.statement_period_from,
            "statement_period_to": stmt.statement_period_to,
            "payment_due_date": stmt.payment_due_date,
            "total_amount_due": stmt.total_amount_due,
            "new_balance": stmt.new_balance,
            "credit_utilization_pct": stmt.credit_utilization_pct,
            "transaction_count": txn_count or 0
        }
        result.append(stmt_dict)

    return result


@router.get("/statements/{statement_id}")
async def get_statement(
    statement_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get detailed information about a specific statement.

    - **statement_id**: ID of the statement

    Returns complete statement details including financial summary.
    """
    from sqlalchemy import select, func
    from app.models import Transaction

    service = StatementService(db)
    statement = await service.get_statement(statement_id, user_id=current_user.id)

    if not statement:
        raise HTTPException(status_code=404, detail="Statement not found")

    # Count transactions separately
    count_result = await db.execute(
        select(func.count(Transaction.id)).where(Transaction.statement_id == statement.id)
    )
    txn_count = count_result.scalar()

    return {
        "id": statement.id,
        "filename": statement.filename,
        "bank_name": statement.bank_name,
        "account_number": statement.account_number,
        "card_type": statement.card_type,
        "statement_date": statement.statement_date,
        "statement_period_from": statement.statement_period_from,
        "statement_period_to": statement.statement_period_to,
        "payment_due_date": statement.payment_due_date,
        "previous_balance": statement.previous_balance,
        "payments_credits": statement.payments_credits,
        "purchases": statement.purchases,
        "fees_charged": statement.fees_charged,
        "interest_charged": statement.interest_charged,
        "new_balance": statement.new_balance,
        "total_amount_due": statement.total_amount_due,
        "minimum_payment_due": statement.minimum_payment_due,
        "credit_limit": statement.credit_limit,
        "available_credit": statement.available_credit,
        "credit_utilization_pct": statement.credit_utilization_pct,
        "rewards_opening": statement.rewards_opening,
        "rewards_earned": statement.rewards_earned,
        "rewards_closing": statement.rewards_closing,
        "transaction_count": txn_count or 0,
    }


@router.get("/statements/{statement_id}/transactions", response_model=List[TransactionDetail])
async def get_transactions(
    statement_id: int,
    category: Optional[str] = Query(None, description="Filter by category"),
    merchant: Optional[str] = Query(None, description="Filter by merchant name"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get transactions for a statement with optional filters.

    - **statement_id**: ID of the statement
    - **category**: Optional filter by merchant category
    - **merchant**: Optional filter by merchant name (partial match)
    - **limit**: Maximum number of transactions (default: 100, max: 1000)
    - **offset**: Number of transactions to skip (for pagination)

    Returns list of transactions.
    """
    service = StatementService(db)

    # Check if statement exists
    statement = await service.get_statement(statement_id, user_id=current_user.id)
    if not statement:
        raise HTTPException(status_code=404, detail="Statement not found")

    transactions, _ = await service.get_transactions(
        statement_id=statement_id,
        user_id=current_user.id,
        category=category,
        merchant=merchant,
        limit=limit,
        offset=offset
    )

    return transactions


@router.get("/statements/{statement_id}/analytics")
async def get_statement_analytics(
    statement_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get analytics for a statement.

    - **statement_id**: ID of the statement

    Returns category breakdown, spending trends, and summary statistics.
    """
    service = StatementService(db)
    analytics = await service.get_analytics(statement_id, user_id=current_user.id)

    if not analytics:
        raise HTTPException(status_code=404, detail="Statement not found")

    statement = analytics["statement"]
    categories = analytics["categories"]

    return {
        "statement_id": statement_id,
        "statement_date": statement.statement_date,
        "total_spending": analytics["total_spending"],
        "transaction_count": analytics["transaction_count"],
        "categories": [
            {
                "category_name": cat.category_name,
                "transaction_count": cat.transaction_count,
                "total_amount": cat.total_amount,
                "percentage": cat.percentage_of_spending,
                "avg_transaction": cat.avg_transaction_amount,
                "rewards_earned": cat.rewards_earned
            }
            for cat in categories
        ],
        "top_category": categories[0].category_name if categories else None,
        "credit_utilization": statement.credit_utilization_pct,
        "rewards_earned": statement.rewards_earned,
    }


@router.get("/statements/{statement_id}/export/csv")
async def export_transactions_csv(
    statement_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Export statement transactions to CSV file.

    - **statement_id**: ID of the statement

    Returns CSV file download.
    """
    service = StatementService(db)

    # Get statement
    statement = await service.get_statement(statement_id, user_id=current_user.id)
    if not statement:
        raise HTTPException(status_code=404, detail="Statement not found")

    # Get all transactions
    transactions, _ = await service.get_transactions(statement_id, user_id=current_user.id, limit=10000)

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Write headers
    writer.writerow([
        "Date",
        "Description",
        "Merchant",
        "Category",
        "Amount",
        "Type",
        "Debit/Credit",
        "City",
        "International",
        "Recurring"
    ])

    # Write data
    for txn in transactions:
        writer.writerow([
            txn.transaction_date.strftime('%Y-%m-%d') if txn.transaction_date else '',
            txn.description_raw,
            txn.merchant_name or '',
            txn.merchant_category or '',
            str(txn.amount),
            txn.transaction_type,
            txn.debit_credit,
            txn.merchant_city or '',
            'Yes' if txn.is_international else 'No',
            'Yes' if txn.is_recurring else 'No'
        ])

    # Prepare file for download
    output.seek(0)

    filename = f"transactions_{statement.bank_name}_{statement.statement_date}.csv"

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@router.delete("/statements/{statement_id}")
async def delete_statement(
    statement_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a specific statement and all its related data.

    - **statement_id**: ID of the statement to delete

    This will cascade delete all transactions, fees, interest charges,
    category summaries, and rewards associated with this statement.
    """
    from sqlalchemy import select, delete
    from app.models import Statement, Transaction, Fee, InterestCharge, CategorySummary, RewardsSummary
    import os

    service = StatementService(db)
    statement = await service.get_statement(statement_id, user_id=current_user.id)

    if not statement:
        raise HTTPException(status_code=404, detail="Statement not found")

    # Delete the PDF file
    if statement.file_path and os.path.exists(statement.file_path):
        try:
            os.remove(statement.file_path)
        except Exception as e:
            print(f"Warning: Could not delete file {statement.file_path}: {e}")

    # Delete from database (cascade will handle related records)
    await db.execute(delete(Statement).where(Statement.id == statement_id))
    await db.commit()

    return {
        "success": True,
        "message": f"Statement {statement_id} deleted successfully",
        "filename": statement.filename
    }


@router.post("/database/reset")
async def reset_database(
    confirm: str = Query(..., description="Type 'RESET' to confirm"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    **DANGER**: Reset entire database - deletes ALL statements and data.

    - **confirm**: Must be exactly 'RESET' to proceed

    This will:
    - Delete all statements, transactions, fees, interest charges, etc.
    - Remove all uploaded PDF files
    - Cannot be undone!
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required for database reset")

    if confirm != "RESET":
        raise HTTPException(
            status_code=400,
            detail="Confirmation failed. Must provide confirm='RESET' to reset database"
        )

    from sqlalchemy import delete, text
    from app.models import (
        Statement, Transaction, Fee, InterestCharge,
        CategorySummary, RewardsSummary, Payment,
        AiExtraction, CategoryRule, Insight, AdvisorReport,
        Budget, Account, FinancialInstitution,
    )
    from app.config import settings
    import shutil

    # Delete all uploaded files
    if os.path.exists(settings.upload_dir):
        try:
            for filename in os.listdir(settings.upload_dir):
                file_path = os.path.join(settings.upload_dir, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
        except Exception as e:
            print(f"Warning: Error cleaning upload directory: {e}")

    # Delete all database records (child → parent order for FK safety)
    await db.execute(delete(Payment))
    await db.execute(delete(CategorySummary))
    await db.execute(delete(RewardsSummary))
    await db.execute(delete(InterestCharge))
    await db.execute(delete(Fee))
    await db.execute(delete(Transaction))
    await db.execute(delete(AiExtraction))
    await db.execute(delete(Insight))
    await db.execute(delete(AdvisorReport))
    await db.execute(delete(Budget))
    await db.execute(delete(CategoryRule))
    await db.execute(delete(Statement))
    await db.execute(delete(Account))
    await db.execute(delete(FinancialInstitution))
    await db.commit()

    # Re-seed default institutions
    await db.execute(text("DELETE FROM sqlite_sequence"))
    await db.commit()

    return {
        "success": True,
        "message": "Full reset complete. All data, files, and AI cache deleted.",
        "warning": "This action cannot be undone."
    }


@router.get("/transactions/search")
async def search_transactions(
    date: Optional[str] = Query(None, description="Filter by transaction date (YYYY-MM-DD)"),
    description: Optional[str] = Query(None, description="Search in description (partial match)"),
    amount: Optional[float] = Query(None, description="Filter by exact amount"),
    category: Optional[str] = Query(None, description="Filter by category"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Search transactions across all statements.

    - **date**: Filter by specific date
    - **description**: Search description (case-insensitive, partial match)
    - **amount**: Filter by exact amount
    - **category**: Filter by merchant category

    Returns list of matching transactions with statement info.
    """
    from sqlalchemy import select, and_, or_
    from app.models import Transaction, Statement

    # Build query
    query = select(
        Transaction.id,
        Transaction.transaction_date,
        Transaction.description_raw,
        Transaction.merchant_name,
        Transaction.merchant_category,
        Transaction.amount,
        Transaction.debit_credit,
        Transaction.transaction_type,
        Transaction.statement_id,
        Statement.filename.label('statement_filename')
    ).join(Statement, Transaction.statement_id == Statement.id).where(Transaction.user_id == current_user.id)

    # Apply filters (description filter done in Python since field is encrypted)
    conditions = []

    if date:
        try:
            from datetime import datetime
            date_obj = datetime.fromisoformat(date).date()
            conditions.append(Transaction.transaction_date == date_obj)
        except:
            pass

    if amount is not None:
        conditions.append(Transaction.amount == amount)

    if category:
        conditions.append(Transaction.merchant_category == category)

    if conditions:
        query = query.where(and_(*conditions))

    # Order by date desc
    query = query.order_by(Transaction.transaction_date.desc())

    result = await db.execute(query)
    transactions = result.all()

    # Filter by description in Python (field is encrypted)
    if description:
        desc_lower = description.lower()
        transactions = [
            txn for txn in transactions
            if txn.description_raw and desc_lower in txn.description_raw.lower()
        ]

    # Convert to dict
    return [
        {
            "id": txn.id,
            "transaction_date": txn.transaction_date.isoformat(),
            "description_raw": txn.description_raw,
            "merchant_name": txn.merchant_name,
            "merchant_category": txn.merchant_category,
            "amount": float(txn.amount),
            "debit_credit": txn.debit_credit,
            "transaction_type": txn.transaction_type,
            "statement_id": txn.statement_id,
            "statement_filename": txn.statement_filename
        }
        for txn in transactions
    ]


@router.get("/transactions/export/csv")
async def export_transactions_csv(
    date: Optional[str] = Query(None),
    description: Optional[str] = Query(None),
    amount: Optional[float] = Query(None),
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Export search results to CSV."""
    from sqlalchemy import select, and_
    from app.models import Transaction, Statement

    # Same query as search
    query = select(
        Transaction.transaction_date,
        Transaction.description_raw,
        Transaction.merchant_name,
        Transaction.merchant_category,
        Transaction.amount,
        Transaction.debit_credit,
        Statement.filename
    ).join(Statement, Transaction.statement_id == Statement.id).where(Transaction.user_id == current_user.id)

    conditions = []
    if date:
        try:
            from datetime import datetime
            date_obj = datetime.fromisoformat(date).date()
            conditions.append(Transaction.transaction_date == date_obj)
        except:
            pass
    if amount is not None:
        conditions.append(Transaction.amount == amount)
    if category:
        conditions.append(Transaction.merchant_category == category)

    if conditions:
        query = query.where(and_(*conditions))

    query = query.order_by(Transaction.transaction_date.desc())

    result = await db.execute(query)
    transactions = result.all()

    # Filter by description in Python (field is encrypted)
    if description:
        desc_lower = description.lower()
        transactions = [
            txn for txn in transactions
            if txn.description_raw and desc_lower in txn.description_raw.lower()
        ]

    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        'Date', 'Description', 'Merchant', 'Category',
        'Amount', 'Type', 'Statement'
    ])

    # Data
    for txn in transactions:
        writer.writerow([
            txn.transaction_date.isoformat(),
            txn.description_raw,
            txn.merchant_name or '',
            txn.merchant_category or '',
            float(txn.amount),
            'Credit' if txn.debit_credit == 'C' else 'Debit',
            txn.filename
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions_export.csv"}
    )


# ---------------------------------------------------------------------------
# Unified Money Events endpoints
# ---------------------------------------------------------------------------

_SOURCE_FILTER_MAP = {
    "all": ("all", None),
    "statement_txn": ("card", EventSource.STATEMENT_TXN),
    "daily_expense": ("cash", EventSource.DAILY_EXPENSE),
    "daily_income": ("cash", EventSource.DAILY_INCOME),
    "liability_paid": ("liability", EventSource.LIABILITY_PAID),
}


@router.get("/money-events")
async def get_money_events(
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    source: str = Query("all", description="Source filter: all|statement_txn|daily_expense|daily_income|liability_paid"),
    direction: Optional[str] = Query(None, description="Direction filter: inflow|outflow"),
    description: Optional[str] = Query(None, description="Search in description (partial match)"),
    amount_min: Optional[float] = Query(None, description="Minimum amount"),
    amount_max: Optional[float] = Query(None, description="Maximum amount"),
    category: Optional[str] = Query(None, description="Filter by category"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Unified money events endpoint — merged view across all financial data sources.
    Returns paginated events with summary totals.
    """
    from datetime import datetime as dt

    # Default date range: last 12 months
    today = date.today()
    if date_from:
        d_from = dt.fromisoformat(date_from).date()
    else:
        d_from = today - timedelta(days=365)
    if date_to:
        d_to = dt.fromisoformat(date_to).date()
    else:
        d_to = today

    # Map source param to payment_source + EventSource filter
    source_cfg = _SOURCE_FILTER_MAP.get(source, _SOURCE_FILTER_MAP["all"])
    payment_source, event_source_filter = source_cfg

    # Fetch from MoneyEventQuery
    mq = MoneyEventQuery(db)
    events = await mq.fetch(
        user_id=current_user.id,
        date_from=d_from,
        date_to=d_to,
        payment_source=payment_source,
        include_transfers=False,
        include_deduped=False,
    )

    # Post-fetch: filter by EventSource if specific source tab selected
    if event_source_filter is not None:
        # For daily_expense / daily_income we fetched cash which includes both
        events = [e for e in events if e.source == event_source_filter]

    # Post-fetch: direction
    if direction:
        events = [e for e in events if e.direction.value == direction]

    # Post-fetch: description substring
    if description:
        desc_lower = description.lower()
        events = [
            e for e in events
            if e.description and desc_lower in e.description.lower()
        ]

    # Post-fetch: amount range
    if amount_min is not None:
        events = [e for e in events if e.amount_bdt >= Decimal(str(amount_min))]
    if amount_max is not None:
        events = [e for e in events if e.amount_bdt <= Decimal(str(amount_max))]

    # Post-fetch: category
    if category:
        events = [e for e in events if e.category == category]

    # Compute summary
    total_inflow = MoneyEventQuery.total_inflow(events)
    total_outflow = MoneyEventQuery.total_outflow(events)
    net = total_inflow - total_outflow
    total_count = len(events)

    # Pagination
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    start = (page - 1) * page_size
    end = start + page_size
    page_events = events[start:end]

    return {
        "events": [
            {
                "event_date": e.event_date.isoformat(),
                "direction": e.direction.value,
                "source": e.source.value,
                "category": e.category,
                "description": e.description,
                "merchant": e.merchant,
                "amount_bdt": float(e.amount_bdt),
                "payment_method": e.payment_method.value,
            }
            for e in page_events
        ],
        "summary": {
            "total_inflow": float(total_inflow),
            "total_outflow": float(total_outflow),
            "net": float(net),
            "count": total_count,
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total_count,
            "total_pages": total_pages,
        },
    }


@router.get("/money-events/export/csv")
async def export_money_events_csv(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    source: str = Query("all"),
    direction: Optional[str] = Query(None),
    description: Optional[str] = Query(None),
    amount_min: Optional[float] = Query(None),
    amount_max: Optional[float] = Query(None),
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export filtered money events to CSV."""
    from datetime import datetime as dt

    today = date.today()
    if date_from:
        d_from = dt.fromisoformat(date_from).date()
    else:
        d_from = today - timedelta(days=365)
    if date_to:
        d_to = dt.fromisoformat(date_to).date()
    else:
        d_to = today

    source_cfg = _SOURCE_FILTER_MAP.get(source, _SOURCE_FILTER_MAP["all"])
    payment_source, event_source_filter = source_cfg

    mq = MoneyEventQuery(db)
    events = await mq.fetch(
        user_id=current_user.id,
        date_from=d_from,
        date_to=d_to,
        payment_source=payment_source,
        include_transfers=False,
        include_deduped=False,
    )

    if event_source_filter is not None:
        events = [e for e in events if e.source == event_source_filter]
    if direction:
        events = [e for e in events if e.direction.value == direction]
    if description:
        desc_lower = description.lower()
        events = [e for e in events if e.description and desc_lower in e.description.lower()]
    if amount_min is not None:
        events = [e for e in events if e.amount_bdt >= Decimal(str(amount_min))]
    if amount_max is not None:
        events = [e for e in events if e.amount_bdt <= Decimal(str(amount_max))]
    if category:
        events = [e for e in events if e.category == category]

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Direction", "Source", "Category", "Description", "Merchant", "Amount (BDT)", "Payment Method"])

    for e in events:
        writer.writerow([
            e.event_date.isoformat(),
            e.direction.value,
            e.source.value,
            e.category,
            e.description or "",
            e.merchant or "",
            float(e.amount_bdt),
            e.payment_method.value,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=money_events_export.csv"},
    )
