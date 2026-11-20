# =============================================================================
# ENDPOINTS REST: CLIENTE Y PERFIL (/api/v1/me)
# =============================================================================

from uuid import UUID
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.api.deps import security_scheme, HTTPAuthorizationCredentials, get_current_user
from app.core.security import decode_access_token
from app.schemas.user import UserProfileSchema, UserUpdateSchema
from app.schemas.loyalty import CustomerCardsResponse, CardSummarySchema
from app.schemas.qr import QrTokenResponse
from app.schemas.reward import CustomerRewardSchema
from app.models.user import Profile
from app.models.business import Store
from app.models.loyalty import LoyaltyCard, StampMovement
from app.models.purchase import Purchase, QrToken, ElectronicInvoice
from app.models.reward import CustomerReward, RewardRedemption, RewardDefinition
from app.models.content import Banner
from app.services.qr_service import QrService
from app.services.dian_invoice_service import DianInvoiceService

router = APIRouter(prefix="/me", tags=["Cliente"])

# Extrae el ID del usuario autenticado de forma estricta a través del token JWT
async def get_current_user_id(
    current_user: Profile = Depends(get_current_user)
) -> UUID:
    return current_user.id

@router.get("", response_model=UserProfileSchema, summary="Consultar perfil del cliente actual")
async def get_my_profile(
    current_user: Profile = Depends(get_current_user)
):
    return current_user

@router.patch("", response_model=UserProfileSchema, summary="Actualizar datos de perfil y documento DIAN del cliente")
async def update_my_profile(
    payload: UserUpdateSchema,
    current_user: Profile = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if payload.full_name is not None and payload.full_name.strip():
        current_user.full_name = payload.full_name.strip()
    if payload.phone is not None:
        current_user.phone = payload.phone.strip() or None
    if payload.document_type is not None and payload.document_type.strip():
        current_user.document_type = payload.document_type.strip().upper()
    if payload.document_number is not None and payload.document_number.strip():
        current_user.document_number = payload.document_number.strip()

    await db.commit()
    await db.refresh(current_user)
    return current_user

@router.get("/purchases", summary="Historial de compras y facturas electrónicas DIAN del cliente")
async def get_my_purchases(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(Purchase, Store.name.label("store_name"))
        .join(Store, Purchase.store_id == Store.id)
        .where(Purchase.customer_id == user_id)
        .order_by(Purchase.created_at.desc())
        .limit(50)
    )
    rows = (await db.execute(stmt)).all()
    items = []
    for p, store_name in rows:
        st_stmt = select(func.coalesce(func.sum(StampMovement.quantity), 0)).where(
            StampMovement.purchase_id == p.id,
            StampMovement.movement_type.in_(["EARN", "CARRY_IN"])
        )
        stamps = (await db.execute(st_stmt)).scalar_one()

        inv_stmt = select(ElectronicInvoice).where(ElectronicInvoice.purchase_id == p.id)
        inv = (await db.execute(inv_stmt)).scalar_one_or_none()

        items.append({
            "purchase_id": str(p.id),
            "receipt_reference": p.receipt_reference,
            "invoice_number": inv.invoice_number if inv else f"SETT-{str(p.id)[:4].upper()}",
            "cufe": inv.cufe if inv else None,
            "store_name": store_name,
            "gross_amount": p.gross_amount,
            "eligible_amount": p.eligible_amount,
            "stamps_awarded": stamps,
            "status": p.status,
            "created_at": p.created_at.isoformat()
        })
    return items

@router.get("/purchases/{purchase_id}/invoice", summary="Obtener y reimprimir Factura Electrónica DIAN del cliente")
async def get_my_purchase_invoice(
    purchase_id: UUID,
    reprint: bool = Query(True),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    purchase = await db.get(Purchase, purchase_id)
    if not purchase or purchase.customer_id != user_id:
        raise HTTPException(status_code=404, detail="Factura no encontrada para este cliente")

    return await DianInvoiceService.ensure_invoice_for_purchase(
        db=db,
        purchase_id=purchase_id,
        increment_reprint=reprint
    )

@router.get("/purchases/{purchase_id}/invoice-xml", summary="Descargar XML UBL 2.1 DIAN de una compra del cliente")
async def download_my_purchase_invoice_xml(
    purchase_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    purchase = await db.get(Purchase, purchase_id)
    if not purchase or purchase.customer_id != user_id:
        raise HTTPException(status_code=404, detail="Factura no encontrada para este cliente")

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

@router.get("/loyalty/cards", response_model=CustomerCardsResponse, summary="Consultar tarjeta activa e historial")
async def get_my_cards(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    # Obtener todas las tarjetas del cliente
    cards_stmt = (
        select(LoyaltyCard)
        .where(LoyaltyCard.customer_id == user_id)
        .order_by(LoyaltyCard.cycle_number.desc())
    )
    cards = (await db.execute(cards_stmt)).scalars().all()

    active_card_summary: Optional[CardSummarySchema] = None
    past_cards: List[CardSummarySchema] = []

    for card in cards:
        # Calcular balance neto de sellos
        balance_stmt = (
            select(func.coalesce(func.sum(StampMovement.quantity), 0))
            .where(
                StampMovement.card_id == card.id,
                StampMovement.movement_type.in_(["EARN", "CARRY_IN", "ADJUSTMENT"])
            )
        )
        balance = (await db.execute(balance_stmt)).scalar_one()

        deductions_stmt = (
            select(func.coalesce(func.sum(StampMovement.quantity), 0))
            .where(
                StampMovement.card_id == card.id,
                StampMovement.movement_type.in_(["REVERSAL", "EXPIRATION", "CARRY_OUT"])
            )
        )
        deductions = (await db.execute(deductions_stmt)).scalar_one()
        net_stamps = max(0, min(8, balance - deductions))

        target = card.stamps_required_snapshot or 8
        remaining = max(0, target - net_stamps)
        progress = round((net_stamps / target) * 100, 1)

        summary = CardSummarySchema(
            id=card.id,
            cycle_number=card.cycle_number,
            stamps_count=net_stamps,
            stamps_required=target,
            stamps_remaining=remaining,
            progress_percentage=progress,
            status=card.status,
            opened_at=card.opened_at,
            completed_at=card.completed_at
        )

        if card.status == "accumulating" and active_card_summary is None:
            active_card_summary = summary
        else:
            past_cards.append(summary)

    return CustomerCardsResponse(
        active_card=active_card_summary,
        past_cycles=past_cards
    )

@router.post("/qr/purchase", response_model=QrTokenResponse, status_code=status.HTTP_201_CREATED, summary="Generar QR temporal de compra (60 segundos)")
async def generate_purchase_qr(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Genera un token aleatorio opaco y efímero con vigencia de 60 segundos.
    El QR no expone PII ni credenciales de acceso.
    """
    qr_res = await QrService.create_token(
        db=db,
        user_id=user_id,
        purpose="PURCHASE"
    )
    await db.commit()
    return qr_res

@router.get("/qr/status", summary="Consultar estado de consumo en vivo de un código QR")
async def get_qr_status(
    qr_token: str = Query(..., description="Token QR en texto plano"),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    token_hash = QrService.hash_token(qr_token)
    stmt = select(QrToken).where(QrToken.token_hash == token_hash)
    res = await db.execute(stmt)
    qr = res.scalar_one_or_none()

    if not qr:
        raise HTTPException(status_code=404, detail="Token no encontrado")

    if qr.user_id != user_id:
        raise HTTPException(status_code=403, detail="Acceso denegado a este token")

    now = datetime.now(timezone.utc)

    if qr.consumed_at is None:
        if qr.expires_at < now:
            return {"status": "EXPIRED", "purpose": qr.purpose}
        remaining = max(0, int((qr.expires_at - now).total_seconds()))
        return {"status": "PENDING", "purpose": qr.purpose, "expires_in_seconds": remaining}

    # Token ya fue consumido por el cajero
    result = {
        "status": "CONSUMED",
        "purpose": qr.purpose,
        "consumed_at": qr.consumed_at
    }

    if qr.purpose == "PURCHASE":
        # Buscar la compra asociada
        p_stmt = (
            select(Purchase, Store.name.label("store_name"))
            .join(Store, Purchase.store_id == Store.id)
            .where(
                Purchase.customer_id == user_id,
                Purchase.cashier_id == qr.consumed_by,
                Purchase.created_at >= qr.created_at
            )
            .order_by(Purchase.created_at.desc())
            .limit(1)
        )
        p_res = (await db.execute(p_stmt)).first()
        if p_res:
            p, store_name = p_res
            st_stmt = select(func.coalesce(func.sum(StampMovement.quantity), 0)).where(StampMovement.purchase_id == p.id)
            stamps = (await db.execute(st_stmt)).scalar_one()

            result["purchase"] = {
                "store_name": store_name,
                "receipt_reference": p.receipt_reference,
                "gross_amount": p.gross_amount,
                "stamps_awarded": stamps
            }

    elif qr.purpose == "REDEMPTION":
        r_stmt = (
            select(RewardRedemption, Store.name.label("store_name"), RewardDefinition.name.label("reward_name"))
            .join(Store, RewardRedemption.store_id == Store.id)
            .join(CustomerReward, RewardRedemption.customer_reward_id == CustomerReward.id)
            .join(RewardDefinition, CustomerReward.definition_id == RewardDefinition.id)
            .where(CustomerReward.id == qr.resource_id)
            .order_by(RewardRedemption.created_at.desc())
            .limit(1)
        )
        r_res = (await db.execute(r_stmt)).first()
        if r_res:
            _, store_name, reward_name = r_res
            result["redemption"] = {
                "store_name": store_name,
                "reward_name": reward_name
            }

    return result

@router.get("/rewards", response_model=List[CustomerRewardSchema], summary="Consultar recompensas disponibles y canjeadas")
async def get_my_rewards(
    status_filter: Optional[str] = None,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(CustomerReward, RewardDefinition)
        .join(RewardDefinition, CustomerReward.definition_id == RewardDefinition.id)
        .where(CustomerReward.customer_id == user_id)
        .order_by(CustomerReward.issued_at.desc())
    )
    if status_filter:
        stmt = stmt.where(CustomerReward.status == status_filter)

    results = (await db.execute(stmt)).all()

    response: List[CustomerRewardSchema] = []
    for reward, r_def in results:
        product_note = None
        if reward.terms_snapshot and isinstance(reward.terms_snapshot, dict):
            product_note = reward.terms_snapshot.get("selected_product")

        response.append(CustomerRewardSchema(
            id=reward.id,
            name=r_def.name,
            description=r_def.description,
            reward_type=r_def.reward_type,
            status=reward.status,
            issued_at=reward.issued_at,
            expires_at=reward.expires_at,
            redeemed_at=reward.redeemed_at,
            selected_product=product_note,
            eligible_stores=["Todas las sedes participantes"]
        ))
    return response

@router.post("/rewards/{reward_id}/qr", response_model=QrTokenResponse, status_code=status.HTTP_201_CREATED, summary="Generar QR temporal de redención (60 segundos)")
async def generate_redemption_qr(
    reward_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    # Verificar pertenencia y estado AVAILABLE
    reward = await db.get(CustomerReward, reward_id)
    if not reward or reward.customer_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recompensa no encontrada.")

    if reward.status != "AVAILABLE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La recompensa no puede canjearse (estado: {reward.status})."
        )

    qr_res = await QrService.create_token(
        db=db,
        user_id=user_id,
        purpose="REDEMPTION",
        resource_id=reward.id
    )
    await db.commit()
    return qr_res

@router.get("/live-status", summary="Consultar estado de tarjeta en tiempo real y alertas recientes")
async def get_live_status(
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Soporte en tiempo real para el carnet permanente (Estilo Starbucks Rewards).
    Permite a la aplicación del cliente detectar instantáneamente cuándo el cajero
    ha acreditado sellos por compra o confirmado una entrega de cortesía,
    activando la animación de éxito y el sonido de celebración.
    """
    now = datetime.now(timezone.utc)

    # 1. Obtener tarjeta activa de 8 sellos
    card_stmt = (
        select(LoyaltyCard)
        .where(LoyaltyCard.customer_id == user_id, LoyaltyCard.status == "accumulating")
        .order_by(LoyaltyCard.cycle_number.desc())
        .limit(1)
    )
    card = (await db.execute(card_stmt)).scalar_one_or_none()

    stamps_count = 0
    cycle_number = 1
    target = 8
    card_id = None
    if card:
        card_id = card.id
        cycle_number = card.cycle_number
        target = card.stamps_required_snapshot or 8
        balance_stmt = (
            select(func.coalesce(func.sum(StampMovement.quantity), 0))
            .where(
                StampMovement.card_id == card.id,
                StampMovement.movement_type.in_(["EARN", "CARRY_IN", "ADJUSTMENT"])
            )
        )
        balance = (await db.execute(balance_stmt)).scalar_one()

        ded_stmt = (
            select(func.coalesce(func.sum(StampMovement.quantity), 0))
            .where(
                StampMovement.card_id == card.id,
                StampMovement.movement_type.in_(["REVERSAL", "EXPIRATION", "CARRY_OUT"])
            )
        )
        ded = (await db.execute(ded_stmt)).scalar_one()
        stamps_count = max(0, min(target, balance - ded))

    # 2. Conteo de recompensas disponibles
    rew_count_stmt = (
        select(func.count(CustomerReward.id))
        .where(CustomerReward.customer_id == user_id, CustomerReward.status == "AVAILABLE")
    )
    available_rewards_count = (await db.execute(rew_count_stmt)).scalar_one()

    # 3. Detectar alertas recientes (últimos 25 segundos)
    recent_alert = None

    # A. Compra reciente
    p_stmt = (
        select(Purchase, Store.name)
        .join(Store, Purchase.store_id == Store.id)
        .where(
            Purchase.customer_id == user_id,
            Purchase.status == "confirmed"
        )
        .order_by(Purchase.created_at.desc())
        .limit(1)
    )
    p_row = (await db.execute(p_stmt)).first()
    recent_purchase = None
    if p_row:
        p, s_name = p_row
        p_time = p.created_at.replace(tzinfo=timezone.utc) if p.created_at.tzinfo is None else p.created_at
        diff = (now - p_time).total_seconds()
        if diff <= 25:
            st_stmt = select(func.coalesce(func.sum(StampMovement.quantity), 0)).where(StampMovement.purchase_id == p.id)
            awarded = (await db.execute(st_stmt)).scalar_one()
            recent_purchase = {
                "id": str(p.id),
                "type": "PURCHASE",
                "title": f"¡+{awarded} {'Sello Sumado' if awarded == 1 else 'Sellos Sumados'}!",
                "message": f"Registrado en {s_name}",
                "receipt_reference": p.receipt_reference,
                "gross_amount": p.gross_amount,
                "stamps_awarded": awarded,
                "store_name": s_name,
                "created_at": p_time.isoformat()
            }

    # B. Redención de cortesía reciente
    r_stmt = (
        select(RewardRedemption, Store.name, CustomerReward, RewardDefinition)
        .join(Store, RewardRedemption.store_id == Store.id)
        .join(CustomerReward, RewardRedemption.customer_reward_id == CustomerReward.id)
        .join(RewardDefinition, CustomerReward.definition_id == RewardDefinition.id)
        .where(CustomerReward.customer_id == user_id)
        .order_by(RewardRedemption.created_at.desc())
        .limit(1)
    )
    r_row = (await db.execute(r_stmt)).first()
    recent_redemption = None
    if r_row:
        red, s_name, c_rew, r_def = r_row
        red_time = red.created_at.replace(tzinfo=timezone.utc) if red.created_at.tzinfo is None else red.created_at
        diff = (now - red_time).total_seconds()
        if diff <= 25:
            product_name = c_rew.terms_snapshot.get("selected_product") if c_rew.terms_snapshot else r_def.name
            recent_redemption = {
                "id": str(red.id),
                "type": "REDEMPTION",
                "title": "¡Cortesía Entregada!",
                "message": f"Disfruta tu {product_name or 'café'}",
                "store_name": s_name,
                "product_name": product_name or r_def.name,
                "created_at": red_time.isoformat()
            }

    if recent_purchase and recent_redemption:
        if recent_purchase["created_at"] >= recent_redemption["created_at"]:
            recent_alert = recent_purchase
        else:
            recent_alert = recent_redemption
    elif recent_purchase:
        recent_alert = recent_purchase
    elif recent_redemption:
        recent_alert = recent_redemption

    return {
        "user_id": str(user_id),
        "card": {
            "id": str(card_id) if card_id else None,
            "cycle_number": cycle_number,
            "stamps_count": stamps_count,
            "stamps_required": target,
            "stamps_remaining": max(0, target - stamps_count),
            "progress_percentage": round((stamps_count / target) * 100, 1),
            "is_completed": stamps_count >= target
        },
        "available_rewards_count": available_rewards_count,
        "recent_alert": recent_alert
    }


@router.get("/banner", summary="Consultar banner destacado activo para clientes")
async def get_featured_banner(db: AsyncSession = Depends(get_db)):
    """
    Retorna el banner de novedades o cosecha activa configurado por la administración.
    Si está marcado como inactivo u oculto por el administrador, retorna status: inactive.
    """
    stmt = (
        select(Banner)
        .order_by(Banner.sort_order.asc(), Banner.updated_at.desc())
        .limit(1)
    )
    res = await db.execute(stmt)
    banner = res.scalar_one_or_none()

    if not banner or banner.status != "active":
        return {
            "status": "inactive"
        }

    return {
        "id": str(banner.id),
        "badge_text": banner.badge_text or "Cosecha de Origen",
        "title": banner.title,
        "description": banner.description or "",
        "icon": banner.icon or "coffee",
        "status": "active",
        "target_url": banner.target_url
    }


