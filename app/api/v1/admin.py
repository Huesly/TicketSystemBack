# =============================================================================
# ENDPOINTS REST: ADMINISTRACIÓN, GESTIÓN CRUD Y AUDITORÍA (/api/v1/admin)
# =============================================================================

import uuid
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from app.core.database import get_db
from app.models.business import Business, Store
from app.models.user import Profile, BusinessMembership
from app.models.loyalty import LoyaltyCard, StampMovement, LoyaltyProgram, LoyaltyProgramVersion
from app.models.purchase import Purchase, DianSettings, ElectronicInvoice
from app.models.reward import CustomerReward, RewardRedemption, RewardDefinition
from app.models.content import Product, Banner
from app.services.dian_invoice_service import DianInvoiceService
from app.services.loyalty_service import LoyaltyService
from app.schemas.admin import (
    ProductCreateSchema,
    ProductUpdateSchema,
    ProductResponseSchema,
    StoreCreateSchema,
    StoreUpdateSchema,
    AuditTransactionItem,
    BannerUpdateSchema,
    BannerResponseSchema
)
from pydantic import BaseModel, Field
from app.api.deps import get_current_cashier_or_admin, get_current_admin

router = APIRouter(prefix="/admin", tags=["Administración y Backoffice"])


# Helper para obtener el business_id principal
async def get_default_business_id(db: AsyncSession) -> UUID:
    stmt = select(Business.id).limit(1)
    res = await db.execute(stmt)
    bid = res.scalar_one_or_none()
    if not bid:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se encontró una empresa configurada en el sistema"
        )
    return bid

# -----------------------------------------------------------------------------
# 1. DASHBOARD & MÉTRICAS GLOBALES
# -----------------------------------------------------------------------------
@router.get("/dashboard", summary="Resumen ejecutivo de KPIs multisede")
async def get_dashboard(
    store_id: Optional[UUID] = Query(None),
    current_user = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    total_customers = (await db.execute(select(func.count(Profile.id)))).scalar_one()

    active_cards = (await db.execute(
        select(func.count(LoyaltyCard.id)).where(LoyaltyCard.status == "accumulating")
    )).scalar_one()

    completed_cards = (await db.execute(
        select(func.count(LoyaltyCard.id)).where(LoyaltyCard.status == "completed")
    )).scalar_one()

    purchases_query = select(
        func.coalesce(func.sum(Purchase.eligible_amount), 0),
        func.count(Purchase.id)
    ).where(Purchase.status == "confirmed")

    if store_id:
        purchases_query = purchases_query.where(Purchase.store_id == store_id)

    sales_amount, total_purchases = (await db.execute(purchases_query)).one()

    stamps_awarded = (await db.execute(
        select(func.coalesce(func.sum(StampMovement.quantity), 0))
        .where(StampMovement.movement_type.in_(["EARN", "CARRY_IN"]))
    )).scalar_one()

    redeemed_count = (await db.execute(
        select(func.count(RewardRedemption.id)).where(RewardRedemption.status == "confirmed")
    )).scalar_one()

    return {
        "summary": {
            "total_customers": total_customers,
            "active_cards": active_cards,
            "completed_cards": completed_cards,
            "total_purchases_count": total_purchases,
            "total_sales_amount_cop": sales_amount,
            "total_stamps_awarded": stamps_awarded,
            "total_rewards_redeemed": redeemed_count
        }
    }


# -----------------------------------------------------------------------------
# 2. AUDITORÍA DE TRANSACCIONES EN VIVO
# -----------------------------------------------------------------------------
@router.get("/transactions", response_model=List[AuditTransactionItem], summary="Bitácora en vivo de compras y canjes")
async def list_transactions(
    store_id: Optional[UUID] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Retorna el flujo unificado en tiempo real de facturas registradas y
    recompensas redimidas en caja, con datos de auditoría completos.
    """
    # 1. Consultar Compras
    purchase_stmt = (
        select(
            Purchase.id,
            Purchase.receipt_reference,
            Purchase.created_at,
            Purchase.gross_amount,
            Purchase.status,
            Store.name.label("store_name"),
            Profile.full_name.label("customer_name"),
            Profile.email.label("customer_email")
        )
        .join(Store, Purchase.store_id == Store.id)
        .join(Profile, Purchase.customer_id == Profile.id)
        .order_by(Purchase.created_at.desc())
        .limit(limit)
    )
    if store_id:
        purchase_stmt = purchase_stmt.where(Purchase.store_id == store_id)

    purchases = (await db.execute(purchase_stmt)).all()

    # 2. Consultar Canjes de Recompensas
    redemption_stmt = (
        select(
            RewardRedemption.id,
            RewardRedemption.created_at,
            RewardRedemption.status,
            RewardDefinition.name.label("reward_name"),
            Store.name.label("store_name"),
            Profile.full_name.label("customer_name"),
            Profile.email.label("customer_email")
        )
        .join(Store, RewardRedemption.store_id == Store.id)
        .join(CustomerReward, RewardRedemption.customer_reward_id == CustomerReward.id)
        .join(RewardDefinition, CustomerReward.definition_id == RewardDefinition.id)
        .join(Profile, CustomerReward.customer_id == Profile.id)
        .order_by(RewardRedemption.created_at.desc())
        .limit(limit)
    )
    if store_id:
        redemption_stmt = redemption_stmt.where(RewardRedemption.store_id == store_id)

    redemptions = (await db.execute(redemption_stmt)).all()

    items: List[AuditTransactionItem] = []

    for p in purchases:
        # Calcular sellos asignados a esta compra
        stamps_stmt = (
            select(func.coalesce(func.sum(StampMovement.quantity), 0))
            .where(StampMovement.purchase_id == p.id)
        )
        stamps_count = (await db.execute(stamps_stmt)).scalar_one()

        items.append(AuditTransactionItem(
            id=p.id,
            type="PURCHASE",
            reference=f"Factura #{p.receipt_reference}",
            created_at=p.created_at,
            store_name=p.store_name,
            customer_name=p.customer_name,
            customer_email=p.customer_email,
            cashier_name="Cajero en Turno",
            amount_cop=p.gross_amount,
            stamps_awarded=stamps_count,
            reward_name=None,
            status=p.status
        ))

    for r in redemptions:
        items.append(AuditTransactionItem(
            id=r.id,
            type="REDEMPTION",
            reference=f"Canje de {r.reward_name}",
            created_at=r.created_at,
            store_name=r.store_name,
            customer_name=r.customer_name,
            customer_email=r.customer_email,
            cashier_name="Cajero en Turno",
            amount_cop=None,
            stamps_awarded=None,
            reward_name=r.reward_name,
            status=r.status
        ))

    # Ordenar unificado cronológico descendente
    items.sort(key=lambda x: x.created_at, reverse=True)
    return items[:limit]


# -----------------------------------------------------------------------------
# 3. GESTIÓN CRUD DE SEDES FÍSICAS
# -----------------------------------------------------------------------------
@router.get("/stores", summary="Listar todas las sedes del negocio")
async def list_stores(
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Store).order_by(Store.name.asc())
    stores = (await db.execute(stmt)).scalars().all()
    return stores

@router.post("/stores", status_code=status.HTTP_201_CREATED, summary="Registrar nueva sede física")
async def create_store(
    payload: StoreCreateSchema,
    current_user = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    business_id = await get_default_business_id(db)

    # Verificar unicidad de código
    exists = await db.execute(select(Store).where(Store.code == payload.code.strip()))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"El código de sede '{payload.code}' ya existe.")

    new_store = Store(
        id=uuid.uuid4(),
        business_id=business_id,
        code=payload.code.strip(),
        name=payload.name.strip(),
        address=payload.address.strip(),
        city=payload.city.strip(),
        department=payload.department.strip(),
        phone=payload.phone.strip() if payload.phone else None,
        status=payload.status,
        timezone="America/Bogota",
        geofence_radius_meters=150,
        opening_hours={}
    )
    db.add(new_store)
    await db.commit()
    await db.refresh(new_store)
    return new_store

@router.put("/stores/{store_id}", summary="Actualizar datos de una sede física")
async def update_store(
    store_id: UUID,
    payload: StoreUpdateSchema,
    current_user = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Sede no encontrada")

    if payload.name is not None:
        store.name = payload.name.strip()
    if payload.address is not None:
        store.address = payload.address.strip()
    if payload.city is not None:
        store.city = payload.city.strip()
    if payload.phone is not None:
        store.phone = payload.phone.strip()
    if payload.status is not None:
        store.status = payload.status

    await db.commit()
    await db.refresh(store)
    return store


# -----------------------------------------------------------------------------
# 4. GESTIÓN CRUD DE PRODUCTOS DEL MENÚ Y VITRINA
# -----------------------------------------------------------------------------
@router.get("/products", summary="Listar productos del menú y vitrina")
async def list_products(
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Product).order_by(Product.featured_order.asc().nulls_last(), Product.name.asc())
    products = (await db.execute(stmt)).scalars().all()
    return products

@router.post("/products", response_model=ProductResponseSchema, status_code=status.HTTP_201_CREATED, summary="Crear nuevo producto en vitrina")
async def create_product(
    payload: ProductCreateSchema,
    current_user = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    business_id = await get_default_business_id(db)

    new_product = Product(
        id=uuid.uuid4(),
        business_id=business_id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        price=payload.price,
        currency=payload.currency,
        available=payload.available,
        featured_order=payload.featured_order,
        published_at=datetime.now(timezone.utc)
    )
    db.add(new_product)
    await db.commit()
    await db.refresh(new_product)
    return new_product

@router.put("/products/{product_id}", response_model=ProductResponseSchema, summary="Modificar producto del catálogo")
async def update_product(
    product_id: UUID,
    payload: ProductUpdateSchema,
    current_user = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if payload.name is not None:
        product.name = payload.name.strip()
    if payload.description is not None:
        product.description = payload.description.strip()
    if payload.price is not None:
        product.price = payload.price
    if payload.available is not None:
        product.available = payload.available
    if payload.featured_order is not None:
        product.featured_order = payload.featured_order

    await db.commit()
    await db.refresh(product)
    return product

@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar producto")
async def delete_product(
    product_id: UUID,
    current_user = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    await db.delete(product)
    await db.commit()
    return None


# -----------------------------------------------------------------------------
# 5. GESTIÓN DE BANNER Y NOVEDADES DINÁMICAS
# -----------------------------------------------------------------------------
@router.get("/banner", response_model=BannerResponseSchema, summary="Obtener configuración del banner activo")
async def get_admin_banner(
    current_user = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    bid = await get_default_business_id(db)
    stmt = select(Banner).where(Banner.business_id == bid).order_by(Banner.sort_order.asc()).limit(1)
    res = await db.execute(stmt)
    banner = res.scalar_one_or_none()
    if not banner:
        now = datetime.now(timezone.utc)
        banner = Banner(
            business_id=bid,
            badge_text="Cosecha de Origen",
            title="Café de Origen Huila",
            description="Notas tostadas a chocolate negro y caramelo.",
            icon="coffee",
            status="active",
            created_at=now,
            updated_at=now
        )
        db.add(banner)
        await db.commit()
        await db.refresh(banner)

    return banner


@router.put("/banner", response_model=BannerResponseSchema, summary="Actualizar banner de novedades con validación")
async def update_admin_banner(
    payload: BannerUpdateSchema,
    current_user = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    bid = await get_default_business_id(db)
    stmt = select(Banner).where(Banner.business_id == bid).order_by(Banner.sort_order.asc()).limit(1)
    res = await db.execute(stmt)
    banner = res.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if not banner:
        banner = Banner(
            business_id=bid,
            badge_text=payload.badge_text.strip(),
            title=payload.title.strip(),
            description=payload.description.strip(),
            icon=payload.icon.strip(),
            status=payload.status,
            target_url=payload.target_url.strip() if payload.target_url else None,
            created_at=now,
            updated_at=now
        )
        db.add(banner)
    else:
        banner.badge_text = payload.badge_text.strip()
        banner.title = payload.title.strip()
        banner.description = payload.description.strip()
        banner.icon = payload.icon.strip()
        banner.status = payload.status
        banner.target_url = payload.target_url.strip() if payload.target_url else None
        banner.updated_at = now

    await db.commit()
    await db.refresh(banner)
    return banner


# -----------------------------------------------------------------------------
# 6. FACTURACIÓN ELECTRÓNICA DIAN (UBL 2.1 / CUFE SHA-384) Y REIMPRESIÓN POS
# -----------------------------------------------------------------------------
class DianSettingsUpdateRequest(BaseModel):
    nit_emisor: str = Field(..., min_length=5, max_length=30)
    dv_emisor: str = Field(..., min_length=1, max_length=5)
    razon_social: str = Field(..., min_length=3, max_length=180)
    nombre_comercial: str = Field(..., min_length=2, max_length=150)
    regimen_fiscal: str = Field(..., min_length=3, max_length=100)
    actividad_ciiu: str = Field(default="5613", max_length=30)
    resolucion_numero: str = Field(..., min_length=4, max_length=50)
    resolucion_fecha: str = Field(..., min_length=8, max_length=20)
    prefijo: str = Field(..., min_length=1, max_length=15)
    rango_desde: int = Field(default=1, ge=1)
    rango_hasta: int = Field(default=50000, ge=1)
    clave_tecnica: str = Field(..., min_length=10, max_length=128)
    ambiente: str = Field(default="2", pattern="^(1|2)$")
    tarifa_impoconsumo_pct: int = Field(default=8, ge=0, le=30)
    direccion_emisor: str = Field(..., min_length=3, max_length=255)
    municipio_emisor: str = Field(..., min_length=2, max_length=80)
    telefono_emisor: str = Field(..., min_length=5, max_length=40)
    correo_emisor: str = Field(..., min_length=5, max_length=150)


@router.get("/dian-settings", summary="Consultar configuración de Facturación Electrónica DIAN del negocio")
async def get_dian_settings(
    current_user = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    bid = await get_default_business_id(db)
    settings = await DianInvoiceService.get_or_create_settings(db, bid)
    return settings


@router.put("/dian-settings", summary="Actualizar resolución, NIT y parámetros DIAN del negocio")
async def update_dian_settings(
    payload: DianSettingsUpdateRequest,
    current_user = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    bid = await get_default_business_id(db)
    settings = await DianInvoiceService.get_or_create_settings(db, bid)

    settings.nit_emisor = payload.nit_emisor.strip()
    settings.dv_emisor = payload.dv_emisor.strip()
    settings.razon_social = payload.razon_social.strip()
    settings.nombre_comercial = payload.nombre_comercial.strip()
    settings.regimen_fiscal = payload.regimen_fiscal.strip()
    settings.actividad_ciiu = payload.actividad_ciiu.strip()
    settings.resolucion_numero = payload.resolucion_numero.strip()
    settings.resolucion_fecha = payload.resolucion_fecha.strip()
    settings.prefijo = payload.prefijo.strip().upper()
    settings.rango_desde = payload.rango_desde
    settings.rango_hasta = payload.rango_hasta
    settings.clave_tecnica = payload.clave_tecnica.strip()
    settings.ambiente = payload.ambiente
    settings.tarifa_impoconsumo_pct = payload.tarifa_impoconsumo_pct
    settings.direccion_emisor = payload.direccion_emisor.strip()
    settings.municipio_emisor = payload.municipio_emisor.strip()
    settings.telefono_emisor = payload.telefono_emisor.strip()
    settings.correo_emisor = payload.correo_emisor.strip()
    settings.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(settings)
    return settings


@router.get("/invoices/{purchase_id}", summary="Obtener o reimprimir Factura Electrónica DIAN por ID de compra")
async def get_or_reprint_invoice(
    purchase_id: UUID,
    reprint: bool = Query(True, description="Si es True incrementa el contador de reimpresión"),
    current_user = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    return await DianInvoiceService.ensure_invoice_for_purchase(
        db=db,
        purchase_id=purchase_id,
        increment_reprint=reprint
    )


@router.get("/invoices/{purchase_id}/xml", summary="Descargar archivo XML UBL 2.1 oficial para transmisión DIAN")
async def download_dian_xml(
    purchase_id: UUID,
    current_user = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    inv_data = await DianInvoiceService.ensure_invoice_for_purchase(
        db=db,
        purchase_id=purchase_id,
        increment_reprint=False
    )
    xml_content = DianInvoiceService.generate_ubl21_xml(inv_data)
    filename = f"DIAN_{inv_data['invoice_number']}.xml"
    return Response(
        content=xml_content,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# -----------------------------------------------------------------------------
# 8. ANULACIÓN / REVERSIÓN DE COMPRAS CON NOTA CRÉDITO DIAN (RB-019, RB-020)
# -----------------------------------------------------------------------------
class CancelPurchaseAdminRequest(BaseModel):
    reason: str = Field(..., min_length=4, max_length=400, description="Motivo obligatorio de anulación / devolución")


@router.post("/purchases/{purchase_id}/cancel", summary="Anular compra, revertir sellos y emitir Nota Crédito DIAN")
async def cancel_purchase_endpoint(
    purchase_id: UUID,
    payload: CancelPurchaseAdminRequest,
    current_user: Profile = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    return await LoyaltyService.cancel_purchase(
        db=db,
        purchase_id=purchase_id,
        actor_id=current_user.id,
        reason=payload.reason
    )


# -----------------------------------------------------------------------------
# 9. GESTIÓN DE CLIENTES Y AJUSTE MANUAL AUDITADO DE SELLOS (MOD-02)
# -----------------------------------------------------------------------------
class CustomerUpdateAdminRequest(BaseModel):
    status: Optional[str] = Field(None, pattern="^(active|suspended)$")
    document_type: Optional[str] = Field(None, max_length=20)
    document_number: Optional[str] = Field(None, max_length=40)


class AdjustStampsAdminRequest(BaseModel):
    delta_stamps: int = Field(..., ge=-8, le=8, description="Cantidad de sellos a sumar (+) o restar (-)")
    reason: str = Field(..., min_length=4, max_length=400, description="Motivo obligatorio de auditoría")


@router.get("/customers", summary="Listar y buscar clientes registrados con su saldo de sellos y compras")
async def list_customers(
    search: Optional[str] = Query(None),
    current_user = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    # Excluir usuarios que tienen rol de staff en business_memberships
    staff_subq = select(BusinessMembership.user_id)
    stmt = (
        select(Profile)
        .where(Profile.id.not_in(staff_subq))
        .order_by(Profile.created_at.desc())
        .limit(100)
    )
    if search and search.strip():
        q = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Profile.full_name.ilike(q),
                Profile.email.ilike(q),
                Profile.phone.ilike(q),
                Profile.document_number.ilike(q)
            )
        )

    profiles = (await db.execute(stmt)).scalars().all()
    results = []

    for prof in profiles:
        card_stmt = (
            select(LoyaltyCard)
            .where(LoyaltyCard.customer_id == prof.id, LoyaltyCard.status == "accumulating")
            .order_by(LoyaltyCard.cycle_number.desc())
            .limit(1)
        )
        card = (await db.execute(card_stmt)).scalar_one_or_none()
        stamps_count = 0
        cycle_num = 1
        target = 8
        if card:
            cycle_num = card.cycle_number
            target = card.stamps_required_snapshot or 8
            pos = (await db.execute(
                select(func.coalesce(func.sum(StampMovement.quantity), 0)).where(
                    StampMovement.card_id == card.id,
                    StampMovement.movement_type.in_(["EARN", "CARRY_IN", "ADJUSTMENT"])
                )
            )).scalar_one()
            neg = (await db.execute(
                select(func.coalesce(func.sum(StampMovement.quantity), 0)).where(
                    StampMovement.card_id == card.id,
                    StampMovement.movement_type.in_(["REVERSAL", "EXPIRATION", "CARRY_OUT"])
                )
            )).scalar_one()
            stamps_count = max(0, min(target, pos - neg))

        purchases_count, total_spent = (await db.execute(
            select(func.count(Purchase.id), func.coalesce(func.sum(Purchase.eligible_amount), 0))
            .where(Purchase.customer_id == prof.id, Purchase.status == "confirmed")
        )).one()

        rewards_avail = (await db.execute(
            select(func.count(CustomerReward.id))
            .where(CustomerReward.customer_id == prof.id, CustomerReward.status == "AVAILABLE")
        )).scalar_one()

        results.append({
            "id": str(prof.id),
            "full_name": prof.full_name,
            "email": prof.email,
            "phone": prof.phone,
            "document_type": prof.document_type or "CC",
            "document_number": prof.document_number or "222222222222",
            "status": prof.status,
            "cycle_number": cycle_num,
            "stamps_count": stamps_count,
            "stamps_required": target,
            "purchases_count": purchases_count,
            "total_spent_cop": int(total_spent),
            "available_rewards": rewards_avail,
            "created_at": prof.created_at.isoformat()
        })

    return results


@router.patch("/customers/{customer_id}", summary="Activar/suspender cliente o actualizar NIT/Cédula DIAN")
async def update_customer_admin(
    customer_id: UUID,
    payload: CustomerUpdateAdminRequest,
    current_user = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    customer = await db.get(Profile, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    if payload.status is not None:
        customer.status = payload.status
    if payload.document_type is not None:
        customer.document_type = payload.document_type.strip().upper()
    if payload.document_number is not None:
        customer.document_number = payload.document_number.strip()

    await db.commit()
    await db.refresh(customer)
    return {
        "id": str(customer.id),
        "status": customer.status,
        "document_type": customer.document_type,
        "document_number": customer.document_number,
        "message": "Datos del cliente actualizados correctamente."
    }


@router.post("/customers/{customer_id}/adjust-stamps", summary="Ajustar manualmente los sellos de un cliente con motivo auditado")
async def adjust_customer_stamps_endpoint(
    customer_id: UUID,
    payload: AdjustStampsAdminRequest,
    current_user: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    return await LoyaltyService.adjust_customer_stamps(
        db=db,
        customer_id=customer_id,
        actor_id=current_user.id,
        delta_stamps=payload.delta_stamps,
        reason=payload.reason
    )


# -----------------------------------------------------------------------------
# 10. CONFIGURACIÓN DINÁMICA DE REGLAS DEL PROGRAMA (MOD-04)
# -----------------------------------------------------------------------------
class LoyaltyRulesUpdateRequest(BaseModel):
    amount_per_stamp: int = Field(..., ge=1000, le=500000, description="Valor en COP por cada sello (ej. 10000)")
    minimum_purchase: int = Field(default=0, ge=0, le=500000, description="Compra mínima en COP")
    stamps_required: int = Field(default=8, ge=3, le=20, description="Meta de sellos para completar tarjeta")
    max_stamps_per_purchase: int = Field(default=8, ge=1, le=20, description="Tope máximo de sellos por factura")


@router.get("/loyalty-rules", summary="Obtener reglas vigentes del programa de fidelización")
async def get_loyalty_rules(
    current_user = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(LoyaltyProgramVersion)
        .join(LoyaltyProgram, LoyaltyProgramVersion.program_id == LoyaltyProgram.id)
        .where(LoyaltyProgram.status == "active")
        .order_by(LoyaltyProgramVersion.version_number.desc())
        .limit(1)
    )
    version = (await db.execute(stmt)).scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="No se encontró versión activa de reglas")

    return {
        "id": str(version.id),
        "version_number": version.version_number,
        "amount_per_stamp": int(version.amount_per_stamp),
        "minimum_purchase": int(version.minimum_purchase),
        "stamps_required": int(version.stamps_required),
        "max_stamps_per_purchase": int(version.max_stamps_per_purchase),
        "reward_expiry_days": version.reward_expiry_days
    }


@router.put("/loyalty-rules", summary="Actualizar parámetros matemáticos del programa de sellos")
async def update_loyalty_rules(
    payload: LoyaltyRulesUpdateRequest,
    current_user = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(LoyaltyProgramVersion)
        .join(LoyaltyProgram, LoyaltyProgramVersion.program_id == LoyaltyProgram.id)
        .where(LoyaltyProgram.status == "active")
        .order_by(LoyaltyProgramVersion.version_number.desc())
        .limit(1)
    )
    version = (await db.execute(stmt)).scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="No se encontró versión activa de reglas")

    version.amount_per_stamp = payload.amount_per_stamp
    version.minimum_purchase = payload.minimum_purchase
    version.stamps_required = payload.stamps_required
    version.max_stamps_per_purchase = payload.max_stamps_per_purchase

    await db.commit()
    await db.refresh(version)
    return {
        "id": str(version.id),
        "version_number": version.version_number,
        "amount_per_stamp": int(version.amount_per_stamp),
        "minimum_purchase": int(version.minimum_purchase),
        "stamps_required": int(version.stamps_required),
        "max_stamps_per_purchase": int(version.max_stamps_per_purchase),
        "message": "Reglas del programa actualizadas exitosamente."
    }



