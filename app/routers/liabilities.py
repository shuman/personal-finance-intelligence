from fastapi import APIRouter, Depends, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from pydantic import BaseModel
from typing import List, Optional
from decimal import Decimal
from datetime import date
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.liabilities import LiabilityTemplate, MonthlyRecord, MonthlyLiability
from app.models import User
from app.routers.auth import get_current_user
from app.utils.page_auth import require_login

router = APIRouter(prefix="/liabilities", tags=["liabilities"])
templates = Jinja2Templates(directory="templates")

# --- Pydantic Schemas ---
class TemplateCreate(BaseModel):
    name: str
    default_amount: Optional[Decimal] = None
    priority: str = "Primary"

class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    default_amount: Optional[Decimal] = None
    priority: Optional[str] = None
    is_active: Optional[bool] = None

class StatusUpdate(BaseModel):
    status: str
    paid_amount: Optional[Decimal] = None
    paid_date: Optional[date] = None

class LiabilityUpdate(BaseModel):
    name: Optional[str] = None
    amount: Optional[Decimal] = None
    priority: Optional[str] = None
    paid_date: Optional[date] = None

class LiabilityCreate(BaseModel):
    record_id: int
    name: str
    amount: Decimal
    priority: str = "Secondary"

class ReorderItem(BaseModel):
    id: int
    sort_order: int

# --- HTML Routes ---
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Liabilities dashboard page (requires login)"""
    user = await require_login(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "liabilities/dashboard.html", {"title": "Monthly Liabilities", "user": user})

@router.get("/templates", response_class=HTMLResponse)
async def templates_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Liability templates page (requires login)"""
    user = await require_login(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "liabilities/templates.html", {"title": "Liability Templates", "user": user})

# --- API Routes ---
@router.get("/api/templates")
async def get_templates(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(LiabilityTemplate).where(LiabilityTemplate.user_id == current_user.id).order_by(LiabilityTemplate.id))
    return result.scalars().all()

@router.post("/api/templates")
async def create_template(data: TemplateCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    template = LiabilityTemplate(
        uuid=str(uuid.uuid4()),
        **data.model_dump(),
        user_id=current_user.id
    )
    db.add(template)
    await db.commit()
    return {"status": "success", "id": template.id}

@router.put("/api/templates/{template_id}")
async def update_template(template_id: int, data: TemplateUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(LiabilityTemplate).where(
            LiabilityTemplate.id == template_id,
            LiabilityTemplate.user_id == current_user.id,
        )
    )
    template = result.scalars().first()
    if not template:
        raise HTTPException(status_code=404, detail="Not found")

    if data.name is not None:
        template.name = data.name
    if data.default_amount is not None:
        template.default_amount = data.default_amount
    if data.priority is not None:
        template.priority = data.priority
    if data.is_active is not None:
        template.is_active = data.is_active

    await db.commit()
    return {"status": "success"}

@router.delete("/api/templates/{template_id}")
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(LiabilityTemplate).where(
            LiabilityTemplate.id == template_id,
            LiabilityTemplate.user_id == current_user.id,
        )
    )
    template = result.scalars().first()
    if not template:
        raise HTTPException(status_code=404, detail="Not found")

    await db.delete(template)
    await db.commit()
    return {"status": "success"}

@router.get("/api/months/{year}/{month}")
async def get_monthly_record(year: int, month: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Find record
    stmt = select(MonthlyRecord).where(MonthlyRecord.year == year, MonthlyRecord.month == month, MonthlyRecord.user_id == current_user.id).options(selectinload(MonthlyRecord.liabilities))
    result = await db.execute(stmt)
    record = result.scalars().first()

    if record:
        liabilities = record.liabilities
        liabilities.sort(key=lambda x: (x.sort_order, x.id))
        return {"data": record, "liabilities": liabilities}

    return {"data": None, "liabilities": []}

@router.post("/api/months/{year}/{month}/generate")
async def generate_month(year: int, month: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Check if exists
    stmt = select(MonthlyRecord).where(MonthlyRecord.year == year, MonthlyRecord.month == month, MonthlyRecord.user_id == current_user.id)
    result = await db.execute(stmt)
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Month already generated")

    # Get active templates
    t_result = await db.execute(select(LiabilityTemplate).where(LiabilityTemplate.is_active == True, LiabilityTemplate.user_id == current_user.id))
    active_templates = t_result.scalars().all()

    # Create Record
    record = MonthlyRecord(
        uuid=str(uuid.uuid4()),
        year=year,
        month=month,
        user_id=current_user.id
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    # Create liabilities
    for idx, t in enumerate(active_templates):
        liability = MonthlyLiability(
            uuid=str(uuid.uuid4()),
            monthly_record_id=record.id,
            template_id=t.id,
            name=t.name,
            priority=t.priority,
            amount=t.default_amount or Decimal('0.00'),
            status="Unpaid",
            sort_order=idx,
            user_id=current_user.id,
        )
        db.add(liability)

    await db.commit()
    return {"status": "success", "record_id": record.id}

@router.put("/api/liabilities/{liability_id}/status")
async def update_status(liability_id: int, data: StatusUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(MonthlyLiability).where(MonthlyLiability.id == liability_id, MonthlyLiability.user_id == current_user.id))
    liability = result.scalars().first()
    if not liability:
        raise HTTPException(status_code=404, detail="Not found")

    liability.status = data.status
    if data.paid_amount is not None:
        liability.paid_amount = data.paid_amount

    # If explicitly passed or null
    liability.paid_date = data.paid_date

    await db.commit()
    return {"status": "success"}

@router.put("/api/liabilities/{liability_id}/edit")
async def edit_liability(liability_id: int, data: LiabilityUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(MonthlyLiability).where(MonthlyLiability.id == liability_id, MonthlyLiability.user_id == current_user.id))
    liability = result.scalars().first()
    if not liability:
        raise HTTPException(status_code=404, detail="Not found")

    if data.name is not None:
        liability.name = data.name
    if data.amount is not None:
        liability.amount = data.amount
    if data.priority is not None:
        liability.priority = data.priority
    if data.paid_date is not None:
        liability.paid_date = data.paid_date

    await db.commit()
    return {"status": "success"}

@router.delete("/api/liabilities/{liability_id}")
async def delete_liability(liability_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(MonthlyLiability).where(MonthlyLiability.id == liability_id, MonthlyLiability.user_id == current_user.id))
    liability = result.scalars().first()
    if not liability:
        raise HTTPException(status_code=404, detail="Not found")

    await db.delete(liability)
    await db.commit()
    return {"status": "success"}

@router.post("/api/liabilities")
async def add_one_off_liability(data: LiabilityCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Find max sort order
    result = await db.execute(
        select(MonthlyLiability)
        .where(MonthlyLiability.monthly_record_id == data.record_id, MonthlyLiability.user_id == current_user.id)
        .order_by(MonthlyLiability.sort_order.desc())
    )
    last_item = result.scalars().first()
    next_order = (last_item.sort_order + 1) if last_item else 0

    item = MonthlyLiability(
        uuid=str(uuid.uuid4()),
        monthly_record_id=data.record_id,
        name=data.name,
        amount=data.amount,
        priority=data.priority,
        status="Unpaid",
        sort_order=next_order,
        user_id=current_user.id,
    )
    db.add(item)
    await db.commit()
    return {"status": "success"}

@router.put("/api/liabilities/reorder")
async def reorder_liabilities(items: List[ReorderItem], db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    for item in items:
        # Avoid selectin inside loop normally, but batch update here is fine for ~20 items
        result = await db.execute(select(MonthlyLiability).where(MonthlyLiability.id == item.id, MonthlyLiability.user_id == current_user.id))
        liability = result.scalars().first()
        if liability:
            liability.sort_order = item.sort_order
    await db.commit()
    return {"status": "success"}
