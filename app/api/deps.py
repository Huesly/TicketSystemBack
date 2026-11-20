# =============================================================================
# DEPENDENCIAS COMUNES DE INYECCIÓN (FASTAPI)
# =============================================================================

from uuid import UUID
from typing import Optional, List
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import Profile, BusinessMembership

security_scheme = HTTPBearer(auto_error=False)

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: AsyncSession = Depends(get_db)
) -> Profile:
    """Extrae y valida el JWT del header Authorization: Bearer <token>."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere token de autenticación",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    payload = decode_access_token(credentials.credentials)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        user_id = UUID(payload["sub"])
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identificador de usuario inválido en el token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await db.get(Profile, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado"
        )
    
    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta de usuario no está activa"
        )
    
    return user


async def get_user_roles(user_id: UUID, db: AsyncSession) -> List[str]:
    """Obtiene los roles activos asignados al usuario en membresías comerciales."""
    stmt = (
        select(BusinessMembership.role)
        .where(
            BusinessMembership.user_id == user_id,
            BusinessMembership.status == "active"
        )
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def get_current_cashier_or_admin(
    current_user: Profile = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Profile:
    """Valida que el usuario tenga privilegios de Cajero o Administrador."""
    roles = await get_user_roles(current_user.id, db)
    allowed_roles = {"CASHIER", "STORE_ADMIN", "GENERAL_ADMIN", "OWNER"}
    if not any(role in allowed_roles for role in roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: se requieren permisos de cajero o administrador"
        )
    return current_user


async def get_current_admin(
    current_user: Profile = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Profile:
    """Valida que el usuario tenga rol administrativo (STORE_ADMIN, GENERAL_ADMIN, OWNER)."""
    roles = await get_user_roles(current_user.id, db)
    admin_roles = {"STORE_ADMIN", "GENERAL_ADMIN", "OWNER"}
    if not any(role in admin_roles for role in roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido a administradores de sede o generales"
        )
    return current_user
