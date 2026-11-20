# =============================================================================
# MODELOS ORM: COMPRAS Y TOKENS QR DINÁMICOS
# =============================================================================

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, ForeignKey, BigInteger, Integer, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False)
    cashier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False)
    receipt_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    gross_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    eligible_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="COP", nullable=False)
    program_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loyalty_program_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="confirmed", nullable=False) # confirmed, cancelled, partially_refunded, refunded
    idempotency_key: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("business_id", "store_id", "receipt_reference", name="uq_purchase_store_receipt"),
    )


class QrToken(Base):
    __tablename__ = "qr_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False) # SHA-256
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False) # PURCHASE, REDEMPTION
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    store_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class DianSettings(Base):
    __tablename__ = "dian_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), unique=True, nullable=False)
    nit_emisor: Mapped[str] = mapped_column(String(30), default="901452890", nullable=False)
    dv_emisor: Mapped[str] = mapped_column(String(5), default="3", nullable=False)
    razon_social: Mapped[str] = mapped_column(String(180), default="CONFYTICKETS CAFÉ DE ESPECIALIDAD S.A.S.", nullable=False)
    nombre_comercial: Mapped[str] = mapped_column(String(150), default="ConfyTickets Specialty Coffee", nullable=False)
    regimen_fiscal: Mapped[str] = mapped_column(String(100), default="Responsable del Impuesto Nacional al Consumo (INC 8%)", nullable=False)
    actividad_ciiu: Mapped[str] = mapped_column(String(30), default="5613", nullable=False)
    resolucion_numero: Mapped[str] = mapped_column(String(50), default="18764065891234", nullable=False)
    resolucion_fecha: Mapped[str] = mapped_column(String(20), default="2026-01-15", nullable=False)
    resolucion_vigencia_meses: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    prefijo: Mapped[str] = mapped_column(String(15), default="SETT", nullable=False)
    rango_desde: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    rango_hasta: Mapped[int] = mapped_column(BigInteger, default=50000, nullable=False)
    consecutivo_actual: Mapped[int] = mapped_column(BigInteger, default=1000, nullable=False)
    clave_tecnica: Mapped[str] = mapped_column(String(128), default="fc8eac422eba16e22ffd8c6f94b3f40a6e38162c", nullable=False)
    ambiente: Mapped[str] = mapped_column(String(5), default="2", nullable=False)
    tarifa_impoconsumo_pct: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    direccion_emisor: Mapped[str] = mapped_column(String(255), default="Carrera 35 # 48-12 Cabecera del Llano", nullable=False)
    municipio_emisor: Mapped[str] = mapped_column(String(80), default="Bucaramanga", nullable=False)
    departamento_emisor: Mapped[str] = mapped_column(String(80), default="Santander", nullable=False)
    codigo_municipio_dane: Mapped[str] = mapped_column(String(10), default="68001", nullable=False)
    telefono_emisor: Mapped[str] = mapped_column(String(40), default="+57 607 6438890", nullable=False)
    correo_emisor: Mapped[str] = mapped_column(String(150), default="facturacion@confytickets.co", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class ElectronicInvoice(Base):
    __tablename__ = "electronic_invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("purchases.id", ondelete="CASCADE"), unique=True, nullable=False)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(50), nullable=False)
    prefix: Mapped[str] = mapped_column(String(15), default="SETT", nullable=False)
    consecutive: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    cufe: Mapped[str] = mapped_column(String(128), nullable=False)
    qr_url: Mapped[str] = mapped_column(String(500), nullable=False)
    issue_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(40), default="Efectivo", nullable=False)
    customer_doc_type: Mapped[str] = mapped_column(String(20), default="CC", nullable=False)
    customer_doc_number: Mapped[str] = mapped_column(String(40), default="222222222222", nullable=False)
    customer_name: Mapped[str] = mapped_column(String(150), nullable=False)
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subtotal_cop: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_type: Mapped[str] = mapped_column(String(30), default="INC", nullable=False)
    tax_rate_pct: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    tax_amount_cop: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_cop: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_cop: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stamps_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    dian_status: Mapped[str] = mapped_column(String(30), default="READY_FOR_DIAN", nullable=False)
    credit_note_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    credit_note_cude: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    cancellation_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    reprint_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reprinted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

