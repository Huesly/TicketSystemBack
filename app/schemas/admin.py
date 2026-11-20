# =============================================================================
# SCHEMAS PYDANTIC: BACKOFFICE ADMINISTRATIVO (CRUD PRODUCTOS, SEDES Y AUDITORÍA)
# =============================================================================

from uuid import UUID
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

# --- PRODUCTOS ---
class ProductCreateSchema(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    description: str = Field(..., min_length=2)
    price: int = Field(..., ge=0)
    currency: str = Field("COP", max_length=3)
    available: bool = True
    featured_order: Optional[int] = Field(None, ge=1, le=10)

class ProductUpdateSchema(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    description: Optional[str] = None
    price: Optional[int] = Field(None, ge=0)
    available: Optional[bool] = None
    featured_order: Optional[int] = Field(None, ge=1, le=10)

class ProductResponseSchema(BaseModel):
    id: UUID
    business_id: UUID
    name: str
    description: str
    price: int
    currency: str
    available: bool
    featured_order: Optional[int] = None
    published_at: datetime

# --- SEDES ---
class StoreCreateSchema(BaseModel):
    code: str = Field(..., min_length=2, max_length=30)
    name: str = Field(..., min_length=2, max_length=120)
    address: str = Field(..., min_length=3, max_length=255)
    city: str = Field(..., min_length=2, max_length=80)
    department: str = Field("Santander", max_length=80)
    phone: Optional[str] = Field(None, max_length=30)
    status: str = Field("active", max_length=20)

class StoreUpdateSchema(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    address: Optional[str] = Field(None, min_length=3, max_length=255)
    city: Optional[str] = Field(None, min_length=2, max_length=80)
    phone: Optional[str] = None
    status: Optional[str] = None

# --- AUDITORÍA DE TRANSACCIONES EN VIVO ---
class AuditTransactionItem(BaseModel):
    id: UUID
    type: str # 'PURCHASE' | 'REDEMPTION'
    reference: str # Factura o Código de Canje
    created_at: datetime
    store_name: str
    customer_name: str
    customer_email: str
    cashier_name: str
    amount_cop: Optional[int] = None
    stamps_awarded: Optional[int] = None
    reward_name: Optional[str] = None
    status: str

# --- BANNER Y NOVEDADES DINÁMICAS ---
class BannerUpdateSchema(BaseModel):
    badge_text: str = Field(..., min_length=2, max_length=30, description="Etiqueta superior (ej. Cosecha de Origen, Novedad)")
    title: str = Field(..., min_length=2, max_length=60, description="Título principal de la novedad o cosecha")
    description: str = Field(..., min_length=2, max_length=140, description="Descripción breve o notas de cata")
    icon: str = Field("coffee", max_length=30, description="Identificador visual del icono")
    status: str = Field("active", pattern="^(active|inactive)$", description="Estado visible (active) u oculto (inactive)")
    target_url: Optional[str] = Field(None, max_length=255)

class BannerResponseSchema(BaseModel):
    id: UUID
    business_id: UUID
    badge_text: str
    title: str
    description: Optional[str] = None
    icon: str
    status: str
    target_url: Optional[str] = None
    updated_at: datetime
