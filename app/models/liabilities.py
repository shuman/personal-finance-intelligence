from sqlalchemy import Column, String, Integer, Date, DateTime, Numeric, Boolean, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional

from app.database import Base


class LiabilityTemplate(Base):
    """Template for recurring monthly liabilities."""
    __tablename__ = "liability_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    default_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    priority: Mapped[str] = mapped_column(String(20), default="Primary") # Primary, Secondary, Optional
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class MonthlyRecord(Base):
    """Container for a specific month's liabilities."""
    __tablename__ = "monthly_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    liabilities: Mapped[List["MonthlyLiability"]] = relationship(
        "MonthlyLiability", back_populates="monthly_record", cascade="all, delete-orphan"
    )


class MonthlyLiability(Base):
    """A specific liability instance for a given month."""
    __tablename__ = "monthly_liabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    monthly_record_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("monthly_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    
    template_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("liability_templates.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="Primary")
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    
    status: Mapped[str] = mapped_column(String(20), default="Unpaid") # Unpaid, Paid, Partially Paid
    paid_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    paid_date: Mapped[Optional[date]] = mapped_column(Date)
    
    comments: Mapped[Optional[str]] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    monthly_record: Mapped["MonthlyRecord"] = relationship("MonthlyRecord", back_populates="liabilities")
    template: Mapped[Optional["LiabilityTemplate"]] = relationship("LiabilityTemplate")
