# =============================================================================
# MODELOS ORM: PROGRAMAS, TARJETAS DE 8 SELLOS Y MOVIMIENTOS
# =============================================================================

import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, DateTime, ForeignKey, Integer, SmallInteger, BigInteger, Boolean, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class LoyaltyProgram(Base):
    __tablename__ = "loyalty_programs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    versions: Mapped[List["LoyaltyProgramVersion"]] = relationship("LoyaltyProgramVersion", back_populates="program")


class LoyaltyProgramVersion(Base):
    __tablename__ = "loyalty_program_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    program_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loyalty_programs.id", ondelete="RESTRICT"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    stamps_required: Mapped[int] = mapped_column(SmallInteger, default=8, nullable=False)
    minimum_purchase: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    amount_per_stamp: Mapped[int] = mapped_column(BigInteger, default=10000, nullable=False)
    max_stamps_per_purchase: Mapped[int] = mapped_column(SmallInteger, default=3, nullable=False)
    stamp_expiry_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reward_expiry_days: Mapped[Optional[int]] = mapped_column(Integer, default=30, nullable=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    effective_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rules_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    program: Mapped["LoyaltyProgram"] = relationship("LoyaltyProgram", back_populates="versions")

    __table_args__ = (
        UniqueConstraint("program_id", "version_number", name="uq_program_version"),
    )


class ProgramStore(Base):
    __tablename__ = "program_stores"

    program_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loyalty_programs.id", ondelete="CASCADE"), primary_key=True)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class LoyaltyCard(Base):
    __tablename__ = "loyalty_cards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False)
    program_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loyalty_programs.id", ondelete="RESTRICT"), nullable=False)
    program_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loyalty_program_versions.id"), nullable=False)
    cycle_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    stamps_required_snapshot: Mapped[int] = mapped_column(SmallInteger, default=8, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="accumulating", nullable=False) # accumulating, completed, expired, cancelled
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped["Profile"] = relationship("Profile", back_populates="cards")
    movements: Mapped[List["StampMovement"]] = relationship("StampMovement", back_populates="card")

    __table_args__ = (
        UniqueConstraint("customer_id", "program_id", "cycle_number", name="uq_card_customer_program_cycle"),
    )


class StampMovement(Base):
    __tablename__ = "stamp_movements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    card_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loyalty_cards.id", ondelete="RESTRICT"), nullable=False)
    purchase_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("purchases.id", ondelete="RESTRICT"), nullable=True)
    quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    movement_type: Mapped[str] = mapped_column(String(20), nullable=False) # EARN, REVERSAL, EXPIRATION, ADJUSTMENT, CARRY_IN, CARRY_OUT
    reverses_movement_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("stamp_movements.id"), nullable=True)
    source_store_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id"), nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    card: Mapped["LoyaltyCard"] = relationship("LoyaltyCard", back_populates="movements")
