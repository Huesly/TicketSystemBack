# =============================================================================
# MODELOS ORM: NEGOCIO Y MULTISEDES
# =============================================================================

import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    String, DateTime, ForeignKey, Numeric, Integer, JSON, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    legal_name: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    default_currency: Mapped[str] = mapped_column(String(3), default="COP", nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="America/Bogota", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    stores: Mapped[List["Store"]] = relationship("Store", back_populates="business", cascade="all, delete-orphan")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    department: Mapped[str] = mapped_column(String(80), nullable=False)
    country: Mapped[str] = mapped_column(String(2), default="CO", nullable=False)
    latitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7), nullable=True)
    geofence_radius_meters: Mapped[int] = mapped_column(Integer, default=150, nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="America/Bogota", nullable=False)
    opening_hours: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    business: Mapped["Business"] = relationship("Business", back_populates="stores")

    __table_args__ = (
        UniqueConstraint("business_id", "code", name="uq_store_business_code"),
    )
