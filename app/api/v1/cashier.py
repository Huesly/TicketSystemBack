# =============================================================================
# ENPOINTS REST: CAJA Y TERMINAL POS (/api/v1/cashier)
# =============================================================================

from uuid import UUID
from typing import Optional, List
from fastapi import APIRouter, Depends, Header, status, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.api.deps import get_current_cashier_or_admin
from app.schemas.purchase import (
    PurchaseCreateRequest,
    PurchaseResponse,
    PurchaseCancelRequest
)
from app.schemas.reward import (
    RedemptionCreateRequest,
    RedemptionResponse
)
from app.services.loyalty_service import LoyaltyService
from app.services.qr_service import QrService
from app.models.reward import CustomerReward, RewardRedemption, RewardDefinition
from app.models.user import Profile
from app.models.loyalty import LoyaltyCard, StampMovement
from app.models.content import Product
from app.models.purchase import QrToken
from datetime import datetime, timezone

router = APIRouter(prefix="/cashier", tags=["Caja y POS"])

class MemberLookupRequest(BaseModel):
    member_token: str = Field(..., description="Token del carnet QR permanente del socio o QR temporal")
    store_id: UUID

class MemberLookupResponse(BaseModel):
    customer_id: UUID
    full_name: str
    email: str
    phone: Optional[str] = None
    member_code: str
    active_card: dict
    has_available_reward: bool
    available_rewards_count: int
    available_reward_id: Optional[UUID] = None
    catalog_options: List[dict] = []

class RedeemMemberRewardRequest(BaseModel):
    customer_id: UUID
    store_id: UUID
    reward_id: Optional[UUID] = None
    product_name_note: str = Field(default="Bebida de Cortesía", description="Producto elegido por el cliente en barra")

@router.post(
    "/purchases",
    response_model=PurchaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar compra escaneada y acreditar sellos"
)
async def create_purchase(
    payload: PurchaseCreateRequest,
    idempotency_key: Optional[UUID] = Header(None, alias="Idempotency-Key"),
    current_cashier: Profile = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Escanea el QR temporal del cliente, registra la factura y calcula los sellos.
    Si la tarjeta alcanza 8 sellos, se completa y emite recompensa; los sellos
    sobrantes pasan automáticamente al ciclo consecutivo (arrastre atómico).
    """
    return await LoyaltyService.process_purchase(
        db=db,
        cashier_id=current_cashier.id,
        payload=payload,
        idempotency_key=idempotency_key
    )

@router.post(
    "/redemptions",
    response_model=RedemptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirmar canje de recompensa en caja física"
)
async def redeem_reward(
    payload: RedemptionCreateRequest,
    idempotency_key: Optional[UUID] = Header(None, alias="Idempotency-Key"),
    current_cashier: Profile = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Escanea el QR de redención del cliente y marca la recompensa como canjeada.
    """
    cashier_id = current_cashier.id
    now = datetime.now(timezone.utc)
    # Validar y consumir QR de redención
    qr = await QrService.consume_token(
        db=db,
        raw_token=payload.qr_token,
        expected_purpose="REDEMPTION",
        consumer_id=cashier_id,
        store_id=payload.store_id
    )

    if not qr.resource_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "QR_NO_REWARD", "message": "El código QR no tiene un premio asociado."}
        )

    # Bloqueo pesimista de la recompensa
    reward_stmt = (
        select(CustomerReward)
        .where(CustomerReward.id == qr.resource_id)
        .with_for_update()
    )
    reward = (await db.execute(reward_stmt)).scalar_one_or_none()

    if not reward:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recompensa no encontrada.")

    if reward.status != "AVAILABLE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "REWARD_NOT_AVAILABLE", "message": f"La recompensa no está disponible (estado actual: {reward.status})."}
        )

    if reward.expires_at and reward.expires_at < now:
        reward.status = "EXPIRED"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "REWARD_EXPIRED", "message": "La recompensa ha vencido y ya no puede ser canjeada."}
        )

    # Marcar como redimida
    reward.status = "REDEEMED"
    reward.redeemed_at = now
    terms = dict(reward.terms_snapshot or {})
    terms["selected_product"] = payload.product_name_note
    reward.terms_snapshot = terms

    redemption = RewardRedemption(
        customer_reward_id=reward.id,
        purchase_id=payload.purchase_id,
        store_id=payload.store_id,
        cashier_id=cashier_id,
        status="confirmed",
        idempotency_key=idempotency_key,
        created_at=now
    )
    db.add(redemption)
    await db.commit()

    # Cargar nombre de recompensa y cliente
    reward_def = await db.get(RewardDefinition, reward.definition_id)
    customer = await db.get(Profile, reward.customer_id)

    return RedemptionResponse(
        redemption_id=redemption.id,
        status=redemption.status,
        reward_name=reward_def.name if reward_def else "Cortesía",
        customer_name=customer.full_name if customer else "Cliente",
        redeemed_at=now
    )

@router.post(
    "/lookup-member",
    response_model=MemberLookupResponse,
    summary="Identificar cliente por su QR Permanente (Estilo Starbucks) y consultar premios elegibles"
)
async def lookup_member(
    payload: MemberLookupRequest,
    current_cashier: Profile = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    token = payload.member_token.strip()
    customer_id: Optional[UUID] = None

    if token.startswith("cfy_member_"):
        try:
            customer_id = UUID(token.replace("cfy_member_", ""))
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de token de socio inválido.")
    elif token.startswith("cfy_purchase_") or token.startswith("cfy_redeem_"):
        token_hash = QrService.hash_token(token)
        qr_stmt = select(QrToken).where(QrToken.token_hash == token_hash)
        qr = (await db.execute(qr_stmt)).scalar_one_or_none()
        if qr:
            customer_id = qr.user_id
        else:
            raise HTTPException(status_code=404, detail="Código QR temporal no encontrado o expirado.")
    else:
        try:
            customer_id = UUID(token)
        except ValueError:
            raise HTTPException(status_code=400, detail="Código no reconocido como socio ni como QR de TicketsDemo.")

    customer = await db.get(Profile, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Socio no encontrado en el sistema.")

    # 1. Obtener tarjeta activa de 8 sellos
    card_stmt = (
        select(LoyaltyCard)
        .where(LoyaltyCard.customer_id == customer_id, LoyaltyCard.status == "accumulating")
        .order_by(LoyaltyCard.cycle_number.desc())
        .limit(1)
    )
    card = (await db.execute(card_stmt)).scalar_one_or_none()

    stamps_count = 0
    cycle_number = 1
    target = 8
    if card:
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

    # 2. Obtener recompensas disponibles
    now = datetime.now(timezone.utc)
    rewards_stmt = (
        select(CustomerReward, RewardDefinition)
        .join(RewardDefinition, CustomerReward.definition_id == RewardDefinition.id)
        .where(
            CustomerReward.customer_id == customer_id,
            CustomerReward.status == "AVAILABLE"
        )
        .order_by(CustomerReward.issued_at.asc())
    )
    available_rewards = (await db.execute(rewards_stmt)).all()
    first_reward_id = available_rewards[0][0].id if available_rewards else None

    # 3. Opciones del catálogo de la cafetería para que el cajero/dueño elija
    products_stmt = (
        select(Product)
        .where(Product.available == True)
        .order_by(Product.name.asc())
        .limit(10)
    )
    prods = (await db.execute(products_stmt)).scalars().all()

    catalog_options = [
        {"id": str(p.id), "name": p.name, "description": p.description or "Especialidad de la casa"}
        for p in prods
    ]
    if not catalog_options:
        catalog_options = [
            {"id": "c1", "name": "Capuchino de Origen 12oz", "description": "Espresso doble con leche texturizada"},
            {"id": "c2", "name": "Latte Vainilla Artesanal", "description": "Espresso suave con sirope de vainilla"},
            {"id": "c3", "name": "Café Filtrado + Galleta de Avena", "description": "Extracción en V60 con snack dulce"},
            {"id": "c4", "name": "Bebida a Elección del Barista", "description": "Cortesía libre según disponibilidad"}
        ]

    member_code = f"TD-{str(customer_id)[:4].upper()}"

    return MemberLookupResponse(
        customer_id=customer_id,
        full_name=customer.full_name,
        email=customer.email,
        phone=customer.phone,
        member_code=member_code,
        active_card={
            "cycle_number": cycle_number,
            "stamps_count": stamps_count,
            "stamps_required": target,
            "is_completed": stamps_count >= target
        },
        has_available_reward=len(available_rewards) > 0,
        available_rewards_count=len(available_rewards),
        available_reward_id=first_reward_id,
        catalog_options=catalog_options
    )

@router.post(
    "/redeem-member-reward",
    response_model=RedemptionResponse,
    summary="Canjear cortesía del socio asistida en caja por el barista (Estilo Starbucks)"
)
async def redeem_member_reward(
    payload: RedeemMemberRewardRequest,
    current_cashier: Profile = Depends(get_current_cashier_or_admin),
    db: AsyncSession = Depends(get_db)
):
    cashier_id = current_cashier.id
    now = datetime.now(timezone.utc)
    customer = await db.get(Profile, payload.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")

    # Buscar recompensa disponible
    if payload.reward_id:
        reward = await db.get(CustomerReward, payload.reward_id)
        if not reward or reward.customer_id != payload.customer_id or reward.status != "AVAILABLE":
            raise HTTPException(status_code=400, detail="La recompensa seleccionada no está disponible.")
    else:
        stmt = (
            select(CustomerReward)
            .where(CustomerReward.customer_id == payload.customer_id, CustomerReward.status == "AVAILABLE")
            .order_by(CustomerReward.issued_at.asc())
            .limit(1)
        )
        reward = (await db.execute(stmt)).scalar_one_or_none()

    if not reward:
        raise HTTPException(status_code=400, detail="El cliente no tiene recompensas disponibles para canjear.")

    reward.status = "REDEEMED"
    reward.redeemed_at = now
    terms = dict(reward.terms_snapshot or {})
    terms["selected_product"] = payload.product_name_note
    reward.terms_snapshot = terms

    redemption = RewardRedemption(
        customer_reward_id=reward.id,
        store_id=payload.store_id,
        cashier_id=cashier_id,
        status="confirmed",
        created_at=now
    )
    db.add(redemption)
    await db.commit()

    return RedemptionResponse(
        redemption_id=redemption.id,
        status="confirmed",
        reward_name=payload.product_name_note,
        customer_name=customer.full_name,
        redeemed_at=now
    )
