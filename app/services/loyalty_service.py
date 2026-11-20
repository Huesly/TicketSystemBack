# =============================================================================
# SERVICIO NUCLEAR DE FIDELIZACIÓN, COMPRAS Y ARRASTRE DE SELLOS
# =============================================================================

from uuid import UUID
from datetime import datetime, timezone, timedelta
from typing import Tuple, List, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.models.business import Store
from app.models.user import Profile, MembershipStoreAssignment, BusinessMembership
from app.models.loyalty import (
    LoyaltyProgram,
    LoyaltyProgramVersion,
    LoyaltyCard,
    StampMovement
)
from app.models.purchase import Purchase
from app.models.reward import RewardDefinition, CustomerReward
from app.schemas.purchase import (
    PurchaseCreateRequest,
    PurchaseResponse,
    PurchaseAllocation,
    IssuedRewardInfo
)
from app.services.qr_service import QrService
from app.services.dian_invoice_service import DianInvoiceService
from app.models.purchase import ElectronicInvoice
import hashlib
import uuid as uuid_pkg

class LoyaltyService:
    @staticmethod
    def calculate_stamps(
        eligible_amount: int,
        minimum_purchase: int,
        amount_per_stamp: int,
        max_stamps: int
    ) -> int:
        """
        Aplica la fórmula aprobada del Modo Combinado:
        Si V < M => 0
        Si V >= M => min(C, max(1, floor(V / T)))
        """
        if eligible_amount < minimum_purchase:
            return 0
        if amount_per_stamp <= 0:
            return 1
        stamps_by_amount = eligible_amount // amount_per_stamp
        return min(max_stamps, max(1, stamps_by_amount))

    @classmethod
    async def process_purchase(
        cls,
        db: AsyncSession,
        cashier_id: UUID,
        payload: PurchaseCreateRequest,
        idempotency_key: Optional[UUID] = None
    ) -> PurchaseResponse:
        """
        Procesa una compra completa en una única transacción atómica:
        1. Valida y consume el token QR.
        2. Verifica autorización del cajero en la sede.
        3. Obtiene las reglas vigentes.
        4. Calcula sellos otorgados.
        5. Actualiza tarjeta activa (con carry-over si supera 8 sellos).
        6. Emite recompensa automática si se completa el ciclo.
        """
        now = datetime.now(timezone.utc)

        # 1. Validar token: soporte para QR Permanente de Socio (Starbucks-style) y QR efímero
        raw_token = payload.qr_token.strip()
        if raw_token.startswith("cfy_member_"):
            try:
                customer_id = UUID(raw_token.replace("cfy_member_", ""))
                customer = await db.get(Profile, customer_id)
                if not customer:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"code": "CUSTOMER_NOT_FOUND", "message": "Socio no registrado en la base de datos."}
                    )
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "INVALID_MEMBER_TOKEN", "message": "Formato de token de socio no válido."}
                )
        else:
            qr = await QrService.consume_token(
                db=db,
                raw_token=raw_token,
                expected_purpose="PURCHASE",
                consumer_id=cashier_id,
                store_id=payload.store_id
            )
            customer_id = qr.user_id

        # 2. Verificar existencia y estado de la sede
        store = await db.get(Store, payload.store_id)
        if not store or store.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "STORE_INACTIVE", "message": "La sede seleccionada no está activa."}
            )

        # 3. Verificar permisos del cajero en esta sede
        perm_stmt = (
            select(MembershipStoreAssignment)
            .join(BusinessMembership, MembershipStoreAssignment.membership_id == BusinessMembership.id)
            .where(
                BusinessMembership.user_id == cashier_id,
                MembershipStoreAssignment.store_id == payload.store_id,
                BusinessMembership.status == "active"
            )
        )
        perm_res = await db.execute(perm_stmt)
        if not perm_res.scalar_one_or_none():
            # Permitir si es OWNER o GENERAL_ADMIN del negocio
            admin_stmt = (
                select(BusinessMembership)
                .where(
                    BusinessMembership.user_id == cashier_id,
                    BusinessMembership.business_id == store.business_id,
                    BusinessMembership.role.in_(["OWNER", "GENERAL_ADMIN"]),
                    BusinessMembership.status == "active"
                )
            )
            admin_res = await db.execute(admin_stmt)
            if not admin_res.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "STORE_ACCESS_DENIED", "message": "El empleado no tiene permisos para operar en esta sede."}
                )

        # 4. Verificar unicidad de factura por sede
        check_receipt = await db.execute(
            select(Purchase).where(
                Purchase.business_id == store.business_id,
                Purchase.store_id == payload.store_id,
                Purchase.receipt_reference == payload.receipt_reference
            )
        )
        if check_receipt.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "PURCHASE_DUPLICATE", "message": f"El comprobante '{payload.receipt_reference}' ya fue registrado en esta sede."}
            )

        # 5. Cargar programa y versión de reglas activa
        rules_stmt = (
            select(LoyaltyProgramVersion)
            .join(LoyaltyProgram, LoyaltyProgramVersion.program_id == LoyaltyProgram.id)
            .where(
                LoyaltyProgram.business_id == store.business_id,
                LoyaltyProgram.status == "active",
                LoyaltyProgramVersion.effective_from <= now,
                (LoyaltyProgramVersion.effective_until.is_(None) | (LoyaltyProgramVersion.effective_until > now))
            )
            .order_by(LoyaltyProgramVersion.version_number.desc())
        )
        rules_res = await db.execute(rules_stmt)
        active_rules = rules_res.scalar_one_or_none()
        if not active_rules:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "NO_ACTIVE_LOYALTY_PROGRAM", "message": "No hay un programa de fidelización activo configurado."}
            )

        # 6. Calcular importe elegible y sellos
        eligible_amount = max(0, payload.gross_amount - payload.discount_amount)
        stamps_to_award = cls.calculate_stamps(
            eligible_amount=eligible_amount,
            minimum_purchase=active_rules.minimum_purchase,
            amount_per_stamp=active_rules.amount_per_stamp,
            max_stamps=active_rules.max_stamps_per_purchase
        )

        # 7. Registrar compra
        purchase = Purchase(
            business_id=store.business_id,
            store_id=payload.store_id,
            customer_id=customer_id,
            cashier_id=cashier_id,
            receipt_reference=payload.receipt_reference,
            gross_amount=payload.gross_amount,
            discount_amount=payload.discount_amount,
            eligible_amount=eligible_amount,
            currency=payload.currency,
            program_version_id=active_rules.id,
            status="confirmed",
            idempotency_key=idempotency_key,
            created_at=now
        )
        db.add(purchase)
        await db.flush()

        # 8. Obtener o crear tarjeta activa con bloqueo pesimista
        card_stmt = (
            select(LoyaltyCard)
            .where(
                LoyaltyCard.customer_id == customer_id,
                LoyaltyCard.program_id == active_rules.program_id,
                LoyaltyCard.status == "accumulating"
            )
            .with_for_update()
        )
        card_res = await db.execute(card_stmt)
        card = card_res.scalar_one_or_none()

        if not card:
            # Crear primer ciclo si no tiene
            card = LoyaltyCard(
                customer_id=customer_id,
                program_id=active_rules.program_id,
                program_version_id=active_rules.id,
                cycle_number=1,
                stamps_required_snapshot=active_rules.stamps_required,
                status="accumulating",
                opened_at=now
            )
            db.add(card)
            await db.flush()

        # Obtener saldo actual de sellos en la tarjeta
        balance_stmt = (
            select(func.coalesce(func.sum(StampMovement.quantity), 0))
            .where(
                StampMovement.card_id == card.id,
                StampMovement.movement_type.in_(["EARN", "CARRY_IN", "ADJUSTMENT"])
            )
        )
        current_balance = (await db.execute(balance_stmt)).scalar_one()

        # Deduct reversals/expirations if any
        deductions_stmt = (
            select(func.coalesce(func.sum(StampMovement.quantity), 0))
            .where(
                StampMovement.card_id == card.id,
                StampMovement.movement_type.in_(["REVERSAL", "EXPIRATION", "CARRY_OUT"])
            )
        )
        deductions = (await db.execute(deductions_stmt)).scalar_one()
        net_stamps = max(0, current_balance - deductions)

        target = card.stamps_required_snapshot # Base: 8 sellos
        needed = target - net_stamps

        allocations: List[PurchaseAllocation] = []
        issued_rewards: List[IssuedRewardInfo] = []

        if stamps_to_award > 0:
            if stamps_to_award < needed:
                # Caso A: Los sellos caben dentro del ciclo actual
                db.add(StampMovement(
                    card_id=card.id,
                    purchase_id=purchase.id,
                    quantity=stamps_to_award,
                    movement_type="EARN",
                    source_store_id=payload.store_id,
                    created_by=cashier_id,
                    created_at=now
                ))
                allocations.append(PurchaseAllocation(
                    card_cycle=card.cycle_number,
                    stamps_added=stamps_to_award,
                    total_in_card=net_stamps + stamps_to_award,
                    cycle_status="accumulating"
                ))

            elif stamps_to_award == needed:
                # Caso B: Completa exactamente la tarjeta (8/8)
                db.add(StampMovement(
                    card_id=card.id,
                    purchase_id=purchase.id,
                    quantity=needed,
                    movement_type="EARN",
                    source_store_id=payload.store_id,
                    created_by=cashier_id,
                    created_at=now
                ))
                card.status = "completed"
                card.completed_at = now

                # Emitir recompensa
                reward_info = await cls._issue_reward_for_card(db, customer_id, card.id, store.business_id, active_rules.reward_expiry_days)
                if reward_info:
                    issued_rewards.append(reward_info)

                allocations.append(PurchaseAllocation(
                    card_cycle=card.cycle_number,
                    stamps_added=needed,
                    total_in_card=8,
                    cycle_status="completed"
                ))

                # Abrir nuevo ciclo vacío (ciclo N+1)
                new_card = LoyaltyCard(
                    customer_id=customer_id,
                    program_id=active_rules.program_id,
                    program_version_id=active_rules.id,
                    cycle_number=card.cycle_number + 1,
                    stamps_required_snapshot=active_rules.stamps_required,
                    status="accumulating",
                    opened_at=now
                )
                db.add(new_card)

            else:
                # Caso C: ARRASTRE (CARRY-OVER) - Supera los 8 sellos
                # 1. Completar ciclo actual con los sellos que le faltaban
                db.add(StampMovement(
                    card_id=card.id,
                    purchase_id=purchase.id,
                    quantity=needed,
                    movement_type="EARN",
                    source_store_id=payload.store_id,
                    created_by=cashier_id,
                    created_at=now
                ))
                card.status = "completed"
                card.completed_at = now

                reward_info = await cls._issue_reward_for_card(db, customer_id, card.id, store.business_id, active_rules.reward_expiry_days)
                if reward_info:
                    issued_rewards.append(reward_info)

                allocations.append(PurchaseAllocation(
                    card_cycle=card.cycle_number,
                    stamps_added=needed,
                    total_in_card=8,
                    cycle_status="completed"
                ))

                # 2. Abrir ciclo nuevo y acreditar el remanente como CARRY_IN
                carry_over_stamps = stamps_to_award - needed
                new_card = LoyaltyCard(
                    customer_id=customer_id,
                    program_id=active_rules.program_id,
                    program_version_id=active_rules.id,
                    cycle_number=card.cycle_number + 1,
                    stamps_required_snapshot=active_rules.stamps_required,
                    status="accumulating",
                    opened_at=now
                )
                db.add(new_card)
                await db.flush()

                db.add(StampMovement(
                    card_id=new_card.id,
                    purchase_id=purchase.id,
                    quantity=carry_over_stamps,
                    movement_type="CARRY_IN",
                    source_store_id=payload.store_id,
                    created_by=cashier_id,
                    created_at=now
                ))
                allocations.append(PurchaseAllocation(
                    card_cycle=new_card.cycle_number,
                    stamps_added=carry_over_stamps,
                    total_in_card=carry_over_stamps,
                    cycle_status="accumulating"
                ))

        # Obtener nombre del cliente
        customer = await db.get(Profile, customer_id)
        customer_name = customer.full_name if customer else "Cliente"

        await db.commit()

        # Generar Factura Electrónica DIAN automáticamente (CUFE SHA-384 + UBL 2.1)
        inv_data = await DianInvoiceService.ensure_invoice_for_purchase(
            db=db,
            purchase_id=purchase.id,
            payment_method=payload.payment_method or "Efectivo",
            customer_doc_type=payload.customer_doc_type,
            customer_doc_number=payload.customer_doc_number,
            item_description=payload.item_description,
            increment_reprint=False
        )

        return PurchaseResponse(
            purchase_id=purchase.id,
            status=purchase.status,
            receipt_reference=purchase.receipt_reference,
            invoice_number=inv_data.get("invoice_number"),
            cufe=inv_data.get("cufe"),
            eligible_amount=eligible_amount,
            stamps_awarded=stamps_to_award,
            allocation=allocations,
            rewards_issued=issued_rewards,
            customer_name=customer_name,
            created_at=now
        )

    @classmethod
    async def cancel_purchase(
        cls,
        db: AsyncSession,
        purchase_id: UUID,
        actor_id: UUID,
        reason: str
    ) -> dict:
        """
        Anula una compra registrada, emite Nota Crédito Electrónica DIAN (CUDE SHA-384),
        descuenta los sellos otorgados (REVERSAL) y revoca premios no redimidos originados por la compra.
        """
        now = datetime.now(timezone.utc)
        purchase = await db.get(Purchase, purchase_id)
        if not purchase:
            raise HTTPException(status_code=404, detail="Compra no encontrada")

        if purchase.status == "cancelled":
            raise HTTPException(status_code=400, detail="Esta compra ya fue anulada previamente")

        purchase.status = "cancelled"
        purchase.cancelled_at = now

        # Buscar movimientos de sellos originados por esta compra
        mov_stmt = select(StampMovement).where(
            StampMovement.purchase_id == purchase.id,
            StampMovement.movement_type.in_(["EARN", "CARRY_IN"])
        )
        movements = (await db.execute(mov_stmt)).scalars().all()

        reversed_stamps = 0
        revoked_rewards = 0

        for mov in movements:
            db.add(StampMovement(
                card_id=mov.card_id,
                purchase_id=purchase.id,
                quantity=mov.quantity,
                movement_type="REVERSAL",
                reverses_movement_id=mov.id,
                source_store_id=purchase.store_id,
                created_by=actor_id,
                reason=f"Anulación Factura #{purchase.receipt_reference}: {reason.strip()}",
                created_at=now
            ))
            reversed_stamps += mov.quantity

            # Si la tarjeta se había completado y tiene un premio AVAILABLE, revocarlo y reabrir la tarjeta
            card = await db.get(LoyaltyCard, mov.card_id)
            if card and card.status == "completed":
                rw_stmt = select(CustomerReward).where(
                    CustomerReward.card_id == card.id,
                    CustomerReward.status == "AVAILABLE"
                )
                avail_reward = (await db.execute(rw_stmt)).scalar_one_or_none()
                if avail_reward:
                    avail_reward.status = "CANCELLED"
                    revoked_rewards += 1
                    # Verificar si el cliente ya tiene otra tarjeta accumulating; si sí, cerramos la vacía nueva
                    other_stmt = select(LoyaltyCard).where(
                        LoyaltyCard.customer_id == purchase.customer_id,
                        LoyaltyCard.status == "accumulating",
                        LoyaltyCard.id != card.id
                    )
                    other_card = (await db.execute(other_stmt)).scalar_one_or_none()
                    if other_card:
                        other_card.status = "cancelled"
                        other_card.closed_at = now
                    card.status = "accumulating"
                    card.completed_at = None

        # Emitir Nota Crédito Electrónica DIAN (CUDE SHA-384)
        inv_stmt = select(ElectronicInvoice).where(ElectronicInvoice.purchase_id == purchase.id)
        invoice = (await db.execute(inv_stmt)).scalar_one_or_none()
        nc_number = f"NC-{purchase.receipt_reference}"
        cude = hashlib.sha384(f"{nc_number}|{purchase.id}|{now.isoformat()}|{reason}".encode("utf-8")).hexdigest()
        if invoice:
            nc_number = f"NC-{invoice.invoice_number}"
            invoice.dian_status = "CREDIT_NOTE_ISSUED"
            invoice.credit_note_number = nc_number
            invoice.credit_note_cude = cude
            invoice.cancellation_reason = reason.strip()

        await db.commit()

        return {
            "purchase_id": str(purchase.id),
            "status": "cancelled",
            "receipt_reference": purchase.receipt_reference,
            "reversed_stamps": reversed_stamps,
            "revoked_rewards": revoked_rewards,
            "credit_note_number": nc_number,
            "credit_note_cude": cude,
            "message": f"Factura anulada con éxito. Se descontaron {reversed_stamps} sellos y se emitió la Nota Crédito DIAN {nc_number}."
        }

    @classmethod
    async def adjust_customer_stamps(
        cls,
        db: AsyncSession,
        customer_id: UUID,
        actor_id: UUID,
        delta_stamps: int,
        reason: str
    ) -> dict:
        """
        Ajusta manualmente los sellos de un cliente (+ acredita, - descuenta)
        con motivo obligatorio de auditoría.
        """
        if delta_stamps == 0:
            raise HTTPException(status_code=400, detail="La cantidad de sellos a ajustar no puede ser 0")

        now = datetime.now(timezone.utc)
        customer = await db.get(Profile, customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        card_stmt = (
            select(LoyaltyCard)
            .where(LoyaltyCard.customer_id == customer_id, LoyaltyCard.status == "accumulating")
            .order_by(LoyaltyCard.cycle_number.desc())
            .limit(1)
            .with_for_update()
        )
        card = (await db.execute(card_stmt)).scalar_one_or_none()
        if not card:
            prog_stmt = (
                select(LoyaltyProgram, LoyaltyProgramVersion)
                .join(LoyaltyProgramVersion, LoyaltyProgram.id == LoyaltyProgramVersion.program_id)
                .where(LoyaltyProgram.status == "active")
                .order_by(LoyaltyProgramVersion.version_number.desc())
                .limit(1)
            )
            prog_res = (await db.execute(prog_stmt)).first()
            if not prog_res:
                raise HTTPException(status_code=500, detail="No hay programa activo")
            program, version = prog_res
            card = LoyaltyCard(
                id=uuid_pkg.uuid4(),
                customer_id=customer_id,
                program_id=program.id,
                program_version_id=version.id,
                cycle_number=1,
                stamps_required_snapshot=version.stamps_required,
                status="accumulating",
                opened_at=now
            )
            db.add(card)
            await db.flush()

        mov_type = "ADJUSTMENT" if delta_stamps > 0 else "REVERSAL"
        qty = abs(delta_stamps)

        db.add(StampMovement(
            card_id=card.id,
            quantity=qty,
            movement_type=mov_type,
            created_by=actor_id,
            reason=f"Ajuste manual Admin: {reason.strip()}",
            created_at=now
        ))
        await db.flush()

        # Recalcular saldo de la tarjeta
        pos_stmt = select(func.coalesce(func.sum(StampMovement.quantity), 0)).where(
            StampMovement.card_id == card.id,
            StampMovement.movement_type.in_(["EARN", "CARRY_IN", "ADJUSTMENT"])
        )
        neg_stmt = select(func.coalesce(func.sum(StampMovement.quantity), 0)).where(
            StampMovement.card_id == card.id,
            StampMovement.movement_type.in_(["REVERSAL", "EXPIRATION", "CARRY_OUT"])
        )
        pos_val = (await db.execute(pos_stmt)).scalar_one()
        neg_val = (await db.execute(neg_stmt)).scalar_one()
        net = max(0, pos_val - neg_val)
        target = card.stamps_required_snapshot or 8

        reward_issued = False
        if net >= target:
            card.status = "completed"
            card.completed_at = now
            prog = await db.get(LoyaltyProgram, card.program_id)
            if prog:
                await cls._issue_reward_for_card(db, customer_id, card.id, prog.business_id, 30)
                reward_issued = True
            new_card = LoyaltyCard(
                id=uuid_pkg.uuid4(),
                customer_id=customer_id,
                program_id=card.program_id,
                program_version_id=card.program_version_id,
                cycle_number=card.cycle_number + 1,
                stamps_required_snapshot=target,
                status="accumulating",
                opened_at=now
            )
            db.add(new_card)

        await db.commit()
        return {
            "customer_id": str(customer_id),
            "delta_stamps": delta_stamps,
            "new_balance": min(target, net),
            "reward_issued": reward_issued,
            "message": f"Ajuste aplicado ({'+' if delta_stamps > 0 else ''}{delta_stamps} sellos)."
        }

    @classmethod
    async def _issue_reward_for_card(
        cls,
        db: AsyncSession,
        customer_id: UUID,
        card_id: UUID,
        business_id: UUID,
        expiry_days: Optional[int]
    ) -> Optional[IssuedRewardInfo]:
        """Selecciona la recompensa activa por defecto del catálogo y la emite al cliente."""
        now = datetime.now(timezone.utc)
        def_stmt = (
            select(RewardDefinition)
            .where(
                RewardDefinition.business_id == business_id,
                RewardDefinition.status == "active",
                RewardDefinition.starts_at <= now,
                (RewardDefinition.ends_at.is_(None) | (RewardDefinition.ends_at > now))
            )
            .limit(1)
        )
        reward_def = (await db.execute(def_stmt)).scalar_one_or_none()

        if not reward_def:
            # Crear definición inicial de contingencia si no existe ninguna
            reward_def = RewardDefinition(
                business_id=business_id,
                name="Café Americano 12oz de Cortesía",
                description="Bebida de cortesía por completar tu tarjeta de 8 sellos.",
                reward_type="product",
                reward_value="AMERICANO_12OZ",
                status="active",
                starts_at=now
            )
            db.add(reward_def)
            await db.flush()

        expires_at = now + timedelta(days=expiry_days) if expiry_days else None

        customer_reward = CustomerReward(
            customer_id=customer_id,
            definition_id=reward_def.id,
            card_id=card_id,
            status="AVAILABLE",
            issued_at=now,
            expires_at=expires_at,
            terms_snapshot=reward_def.terms_snapshot
        )
        db.add(customer_reward)
        await db.flush()

        return IssuedRewardInfo(
            reward_id=customer_reward.id,
            name=reward_def.name
        )
