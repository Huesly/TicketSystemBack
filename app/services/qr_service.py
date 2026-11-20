# =============================================================================
# SERVICIO DE CRIPTOGRAFÍA Y TOKENS QR DINÁMICOS
# =============================================================================

import secrets
import hashlib
from uuid import UUID
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from app.models.purchase import QrToken
from app.schemas.qr import QrTokenResponse
from app.core.config import settings

class QrService:
    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Calcula el hash SHA-256 hexadecimal del token en texto plano."""
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @classmethod
    async def create_token(
        cls,
        db: AsyncSession,
        user_id: UUID,
        purpose: str,
        resource_id: Optional[UUID] = None,
        store_id: Optional[UUID] = None,
        ttl_seconds: int = settings.QR_EXPIRATION_SECONDS
    ) -> QrTokenResponse:
        """Genera un token criptográfico opaco de 32 bytes con expiración estricta."""
        raw_token = f"cfy_{purpose.lower()}_{secrets.token_urlsafe(32)}"
        token_hash = cls.hash_token(raw_token)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)

        qr_entry = QrToken(
            token_hash=token_hash,
            user_id=user_id,
            purpose=purpose,
            resource_id=resource_id,
            store_id=store_id,
            expires_at=expires_at,
            created_at=now
        )
        db.add(qr_entry)
        await db.flush()

        return QrTokenResponse(
            qr_token=raw_token,
            purpose=purpose,
            expires_in_seconds=ttl_seconds,
            expires_at=expires_at
        )

    @classmethod
    async def consume_token(
        cls,
        db: AsyncSession,
        raw_token: str,
        expected_purpose: str,
        consumer_id: UUID,
        store_id: Optional[UUID] = None
    ) -> QrToken:
        """
        Valida y consume el token atómicamente con bloqueo de fila.
        Lanza HTTP 400 si el token es inválido, ya fue usado o expiró.
        """
        token_hash = cls.hash_token(raw_token)
        now = datetime.now(timezone.utc)

        # Bloqueo pesimista de fila para evitar condiciones de carrera
        stmt = (
            select(QrToken)
            .where(QrToken.token_hash == token_hash)
            .with_for_update()
        )
        result = await db.execute(stmt)
        qr_entry = result.scalar_one_or_none()

        if not qr_entry:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "QR_INVALID", "message": "El código QR no es válido."}
            )

        if qr_entry.consumed_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "QR_ALREADY_USED", "message": "Este código QR ya fue utilizado."}
            )

        if qr_entry.expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "QR_EXPIRED", "message": "El código QR ha expirado. Por favor genera uno nuevo."}
            )

        if qr_entry.purpose != expected_purpose:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "QR_PURPOSE_MISMATCH", "message": f"El código presentado no corresponde al propósito '{expected_purpose}'."}
            )

        # Consumo atómico
        qr_entry.consumed_at = now
        qr_entry.consumed_by = consumer_id
        await db.flush()

        return qr_entry
