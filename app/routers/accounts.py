"""
Accounts router — manage financial institutions and account registry.
"""
import hashlib
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from datetime import datetime
from decimal import Decimal

from app.database import get_db
from app.models import FinancialInstitution, Account, Statement, Transaction, User
from app.routers.auth import get_current_user

router = APIRouter(prefix="/api", tags=["accounts"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class InstitutionResponse(BaseModel):
    id: int
    name: str
    short_name: str
    country: str
    statement_format_hint: str
    has_sidebar: bool
    default_currency: str

    class Config:
        from_attributes = True


class AccountCreate(BaseModel):
    institution_id: Optional[int] = None
    account_type: str = "credit_card"
    account_number_masked: str
    account_number_full: Optional[str] = None  # Used only to generate hash, never stored
    cardholder_name: Optional[str] = None
    account_nickname: Optional[str] = None
    card_network: Optional[str] = None
    card_tier: str = "primary"
    parent_account_id: Optional[int] = None
    billing_currency: str = "BDT"
    credit_limit: Optional[float] = None
    cash_limit: Optional[float] = None
    reward_program_name: Optional[str] = None
    reward_type: Optional[str] = None
    reward_expiry_months: Optional[int] = None
    points_value_rate: Optional[float] = None
    color_hex: Optional[str] = None


class AccountUpdate(BaseModel):
    account_nickname: Optional[str] = None
    color_hex: Optional[str] = None
    credit_limit: Optional[float] = None
    cash_limit: Optional[float] = None
    reward_program_name: Optional[str] = None
    reward_type: Optional[str] = None
    reward_expiry_months: Optional[int] = None
    points_value_rate: Optional[float] = None
    is_active: Optional[bool] = None


class AccountResponse(BaseModel):
    id: int
    institution_id: Optional[int]
    account_type: str
    account_number_masked: str
    cardholder_name: Optional[str]
    account_nickname: Optional[str]
    card_network: Optional[str]
    card_tier: Optional[str]
    parent_account_id: Optional[int]
    billing_currency: str
    credit_limit: Optional[Decimal]
    cash_limit: Optional[Decimal]
    reward_program_name: Optional[str]
    reward_type: Optional[str]
    reward_expiry_months: Optional[int]
    points_value_rate: Optional[Decimal]
    is_active: bool
    color_hex: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Financial Institutions
# ---------------------------------------------------------------------------

@router.get("/institutions", response_model=List[InstitutionResponse])
async def list_institutions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all registered financial institutions."""
    result = await db.execute(
        select(FinancialInstitution)
        .where(FinancialInstitution.user_id == current_user.id)
        .order_by(FinancialInstitution.name)
    )
    return result.scalars().all()


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

@router.get("/accounts", response_model=List[AccountResponse])
async def list_accounts(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all registered accounts/cards."""
    query = select(Account).where(Account.user_id == current_user.id)
    if active_only:
        query = query.where(Account.is_active == True)
    query = query.order_by(Account.card_tier, Account.cardholder_name)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/accounts", response_model=AccountResponse)
async def create_account(
    body: AccountCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Register a new card or account."""
    # Generate hash from full account number if provided, else from masked
    hash_source = body.account_number_full or body.account_number_masked
    account_hash = hashlib.sha256(hash_source.encode()).hexdigest()

    # Check for duplicate
    existing = await db.execute(
        select(Account).where(
            Account.account_number_hash == account_hash,
            Account.user_id == current_user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Account already registered")

    # Extract last 4 digits for plaintext matching column
    card_last_four = body.account_number_masked.replace("*", "").replace("-", "").replace(" ", "")[-4:]

    account = Account(
        uuid=str(uuid.uuid4()),
        user_id=current_user.id,
        institution_id=body.institution_id,
        account_type=body.account_type,
        account_number_masked=body.account_number_masked,
        account_number_hash=account_hash,
        card_last_four=card_last_four,
        cardholder_name=body.cardholder_name,
        account_nickname=body.account_nickname,
        card_network=body.card_network,
        card_tier=body.card_tier,
        parent_account_id=body.parent_account_id,
        billing_currency=body.billing_currency,
        credit_limit=Decimal(str(body.credit_limit)) if body.credit_limit else None,
        cash_limit=Decimal(str(body.cash_limit)) if body.cash_limit else None,
        reward_program_name=body.reward_program_name,
        reward_type=body.reward_type,
        reward_expiry_months=body.reward_expiry_months,
        points_value_rate=Decimal(str(body.points_value_rate)) if body.points_value_rate else None,
        color_hex=body.color_hex,
        is_active=True,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


@router.put("/accounts/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int,
    body: AccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update account details (nickname, limits, rewards info, etc.)."""
    result = await db.execute(
        select(Account).where(Account.id == account_id, Account.user_id == current_user.id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    for field, value in body.model_dump(exclude_none=True).items():
        if field in ("credit_limit", "cash_limit", "points_value_rate") and value is not None:
            value = Decimal(str(value))
        setattr(account, field, value)

    await db.commit()
    await db.refresh(account)
    return account


@router.get("/accounts/{account_id}/summary")
async def get_account_summary(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get spending, rewards, and statement summary across all statements
    linked to this account.
    """
    result = await db.execute(
        select(Account).where(Account.id == account_id, Account.user_id == current_user.id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Count statements
    stmt_result = await db.execute(
        select(func.count(Statement.id))
        .where(Statement.account_id == account_id, Statement.user_id == current_user.id)
    )
    statement_count = stmt_result.scalar() or 0

    # Sum total transactions
    txn_result = await db.execute(
        select(func.count(Transaction.id), func.sum(Transaction.amount))
        .where(
            Transaction.account_id == account_id,
            Transaction.debit_credit == "D",
            Transaction.user_id == current_user.id,
        )
    )
    txn_row = txn_result.one()
    txn_count = txn_row[0] or 0
    total_spending = float(txn_row[1] or 0)

    # Latest statement
    latest_stmt = await db.execute(
        select(Statement)
        .where(Statement.account_id == account_id, Statement.user_id == current_user.id)
        .order_by(Statement.statement_date.desc())
        .limit(1)
    )
    latest = latest_stmt.scalar_one_or_none()

    # Supplement cards
    supp_result = await db.execute(
        select(Account).where(
            Account.parent_account_id == account_id,
            Account.user_id == current_user.id,
        )
    )
    supplement_cards = supp_result.scalars().all()

    return {
        "account_id": account_id,
        "account_number_masked": account.account_number_masked,
        "cardholder_name": account.cardholder_name,
        "card_network": account.card_network,
        "billing_currency": account.billing_currency,
        "credit_limit": float(account.credit_limit) if account.credit_limit else None,
        "reward_program": account.reward_program_name,
        "statement_count": statement_count,
        "total_transactions": txn_count,
        "total_spending": total_spending,
        "latest_statement": {
            "id": latest.id,
            "date": latest.statement_date.isoformat() if latest else None,
            "balance": float(latest.new_balance) if latest and latest.new_balance else None,
            "rewards_closing": latest.rewards_closing if latest else None,
        } if latest else None,
        "supplement_cards": [
            {
                "id": s.id,
                "card_number_masked": s.account_number_masked,
                "cardholder_name": s.cardholder_name,
            }
            for s in supplement_cards
        ],
    }
