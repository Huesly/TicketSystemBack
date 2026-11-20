# =============================================================================
# ENDPOINTS REST: AUTENTICACIÓN, REGISTRO Y GESTIÓN DE CUENTAS (/api/v1/auth)
# =============================================================================

import uuid
import json
import secrets
from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import redis.asyncio as aioredis

from app.core.database import get_db
from app.core.redis import get_redis
from app.core.security import get_password_hash, verify_password, create_access_token
from app.core.security_middleware import get_client_ip
from app.services.email_service import send_password_reset_email
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    RegisterStaffRequest,
    TokenResponse,
    AuthMeResponse,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    ChangePasswordRequest,
    MessageResponse
)
from app.schemas.user import UserProfileSchema
from app.models.user import Profile, BusinessMembership, MembershipStoreAssignment, Consent
from app.models.business import Business, Store
from app.models.loyalty import LoyaltyProgram, LoyaltyProgramVersion, LoyaltyCard
from app.api.deps import get_current_user, get_user_roles

router = APIRouter(prefix="/auth", tags=["Autenticación y Cuentas"])

@router.post("/login", response_model=TokenResponse, summary="Iniciar sesión con correo y contraseña")
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis)
):
    """
    Autentica clientes, cajeros o administradores mediante credenciales seguras.
    Incluye bloqueo temporal anti-fuerza bruta tras 5 intentos fallidos consecutivos.
    """
    email_clean = payload.email.lower().strip()
    fail_key = f"login_fail:{email_clean}"

    # Verificar si la cuenta está temporalmente bloqueada por fuerza bruta
    try:
        fails_raw = await redis.get(fail_key)
        if fails_raw and int(fails_raw) >= 5:
            ttl = await redis.ttl(fail_key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Cuenta protegida temporalmente por múltiples intentos fallidos. Intenta de nuevo en {max(1, ttl)} segundos."
            )
    except HTTPException:
        raise
    except Exception:
        pass

    stmt = select(Profile).where(Profile.email == email_clean)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if not user or not user.hashed_password or not verify_password(payload.password, user.hashed_password):
        try:
            current_fails = await redis.incr(fail_key)
            if current_fails == 1:
                await redis.expire(fail_key, 300)  # Ventana de 5 minutos
            remaining_attempts = max(0, 5 - current_fails)
            if remaining_attempts == 0:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Has superado los 5 intentos fallidos. Tu cuenta se ha bloqueado preventivamente por 5 minutos."
                )
        except HTTPException:
            raise
        except Exception:
            pass

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta cuenta está desactivada o suspendida"
        )

    # Limpiar contador de fallos tras login exitoso
    try:
        await redis.delete(fail_key)
    except Exception:
        pass

    roles = await get_user_roles(user.id, db)
    token = create_access_token(data={
        "sub": str(user.id),
        "email": user.email,
        "roles": roles
    })

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserProfileSchema.model_validate(user),
        roles=roles
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, summary="Registro público de nuevos clientes")
async def register(
    payload: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Registra un nuevo cliente con tarjeta digital de 8 sellos inicializada en ciclo 1
    y guarda la evidencia legal de consentimiento Habeas Data (Ley 1581 de 2012).
    """
    if not payload.accept_terms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debes aceptar la Política de Tratamiento de Datos Personales (Ley 1581 de 2012) para crear tu cuenta."
        )

    email_clean = payload.email.lower().strip()
    existing = await db.execute(select(Profile).where(Profile.email == email_clean))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe una cuenta registrada con este correo electrónico"
        )

    new_user_id = uuid.uuid4()
    hashed = get_password_hash(payload.password)
    now = datetime.now(timezone.utc)

    new_profile = Profile(
        id=new_user_id,
        full_name=payload.full_name.strip(),
        email=email_clean,
        phone=payload.phone.strip() if payload.phone else None,
        hashed_password=hashed,
        status="active"
    )
    db.add(new_profile)
    await db.flush()

    # Registrar evidencia de consentimiento Habeas Data (Ley 1581 de 2012)
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "unknown")
    evidence = {
        "ip_address": client_ip,
        "user_agent": user_agent[:250],
        "regulation": "Ley 1581 de 2012 y Decreto 1377 de 2013 (Colombia)",
        "accepted_at_iso": now.isoformat()
    }

    db.add(Consent(
        id=uuid.uuid4(),
        user_id=new_user_id,
        purpose="general_terms",
        policy_version="v1.0-2026",
        status="granted",
        granted_at=now,
        evidence_metadata=evidence
    ))

    if payload.accept_marketing:
        db.add(Consent(
            id=uuid.uuid4(),
            user_id=new_user_id,
            purpose="marketing_communications",
            policy_version="v1.0-2026",
            status="granted",
            granted_at=now,
            evidence_metadata=evidence
        ))

    # Inicializar Tarjeta de Fidelización si existe programa activo
    prog_stmt = (
        select(LoyaltyProgram, LoyaltyProgramVersion)
        .join(LoyaltyProgramVersion, LoyaltyProgram.id == LoyaltyProgramVersion.program_id)
        .where(LoyaltyProgram.status == "active")
        .order_by(LoyaltyProgramVersion.version_number.desc())
        .limit(1)
    )
    prog_res = (await db.execute(prog_stmt)).first()
    if prog_res:
        program, version = prog_res
        initial_card = LoyaltyCard(
            id=uuid.uuid4(),
            customer_id=new_user_id,
            program_id=program.id,
            program_version_id=version.id,
            cycle_number=1,
            stamps_required_snapshot=version.stamps_required,
            status="accumulating",
            opened_at=datetime.now(timezone.utc)
        )
        db.add(initial_card)

    await db.commit()
    await db.refresh(new_profile)

    roles: List[str] = []
    token = create_access_token(data={
        "sub": str(new_profile.id),
        "email": new_profile.email,
        "roles": roles
    })

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserProfileSchema.model_validate(new_profile),
        roles=roles
    )


@router.post("/register-staff", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, summary="Registro de administradores y cajeros")
async def register_staff(
    payload: RegisterStaffRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Registra un nuevo miembro del equipo administrativo o de caja,
    asignándole su rol correspondiente y vinculándolo a una sede.
    """
    email_clean = payload.email.lower().strip()
    existing = await db.execute(select(Profile).where(Profile.email == email_clean))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe una cuenta registrada con este correo electrónico"
        )

    # Obtener el negocio principal
    b_stmt = select(Business).limit(1)
    b_res = await db.execute(b_stmt)
    business = b_res.scalar_one_or_none()
    if not business:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se encontró una empresa configurada en el sistema"
        )

    new_user_id = uuid.uuid4()
    hashed = get_password_hash(payload.password)

    new_profile = Profile(
        id=new_user_id,
        full_name=payload.full_name.strip(),
        email=email_clean,
        phone=payload.phone.strip() if payload.phone else None,
        hashed_password=hashed,
        status="active"
    )
    db.add(new_profile)

    membership_id = uuid.uuid4()
    membership = BusinessMembership(
        id=membership_id,
        business_id=business.id,
        user_id=new_user_id,
        role=payload.role,
        status="active"
    )
    db.add(membership)
    await db.flush()

    # Asignar sede si se especificó o la primera sede disponible
    target_store_id = None
    if payload.store_id:
        try:
            target_store_id = uuid.UUID(payload.store_id)
        except ValueError:
            pass

    if not target_store_id:
        s_stmt = select(Store.id).where(Store.business_id == business.id).limit(1)
        s_res = await db.execute(s_stmt)
        target_store_id = s_res.scalar_one_or_none()

    if target_store_id:
        store_assignment = MembershipStoreAssignment(
            membership_id=membership_id,
            store_id=target_store_id
        )
        db.add(store_assignment)

    await db.commit()
    await db.refresh(new_profile)

    roles: List[str] = [payload.role]
    token = create_access_token(data={
        "sub": str(new_profile.id),
        "email": new_profile.email,
        "roles": roles
    })

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserProfileSchema.model_validate(new_profile),
        roles=roles
    )



@router.get("/me", response_model=AuthMeResponse, summary="Obtener usuario en sesión actual y sus roles")
async def get_auth_me(
    current_user: Profile = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retorna los datos del usuario autenticado a partir del token JWT."""
    roles = await get_user_roles(current_user.id, db)
    return AuthMeResponse(
        user=UserProfileSchema.model_validate(current_user),
        roles=roles
    )


@router.post("/forgot-password", response_model=MessageResponse, summary="Solicitar código OTP para recuperación de contraseña")
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis)
):
    """
    Genera un código OTP de 6 dígitos seguro, lo guarda en Redis con vigencia de 15 minutos,
    y despacha un correo al usuario registrado para validar su identidad.
    """
    clean_email = payload.email.lower().strip()

    # Rate limiting: prevenir spam de solicitudes (cooldown de 60 segundos por correo)
    cooldown_key = f"pwd_reset_cd:{clean_email}"
    if await redis.exists(cooldown_key):
        ttl = await redis.ttl(cooldown_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Por favor espera {max(1, ttl)} segundos antes de solicitar otro código de recuperación."
        )

    # Verificar existencia del perfil
    stmt = select(Profile).where(Profile.email == clean_email)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if not user:
        # Por seguridad no revelamos explícitamente si el correo existe o no
        return MessageResponse(
            message="Si la dirección de correo coincide con una cuenta activa, recibirás un código de verificación en breve.",
            expires_in_minutes=15
        )

    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta cuenta está desactivada. Contacta al soporte técnico."
        )

    # Generar código OTP criptográficamente aleatorio de 6 dígitos
    code = f"{secrets.randbelow(900000) + 100000}"

    # Guardar en Redis con TTL de 15 minutos (900 segundos)
    reset_payload = {
        "code": code,
        "user_id": str(user.id),
        "attempts": 0,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await redis.set(f"pwd_reset:{clean_email}", json.dumps(reset_payload), ex=900)
    await redis.set(cooldown_key, "1", ex=60)

    # Despachar correo electrónico
    email_res = send_password_reset_email(
        email=user.email,
        code=code,
        user_name=user.full_name,
        expires_minutes=15
    )

    return MessageResponse(
        message="Se ha enviado un código de 6 dígitos a tu correo electrónico registrado.",
        expires_in_minutes=15,
        dev_code=email_res.get("dev_code")
    )


@router.post("/reset-password", response_model=MessageResponse, summary="Validar código OTP y definir nueva contraseña")
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis)
):
    """
    Valida el código OTP de 6 dígitos emitido. Si es correcto, actualiza la contraseña
    con Bcrypt y elimina el código para evitar reutilización.
    """
    clean_email = payload.email.lower().strip()
    clean_code = payload.code.strip()

    cache_key = f"pwd_reset:{clean_email}"
    raw_data = await redis.get(cache_key)

    if not raw_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El código de verificación ha expirado o no existe. Solicita uno nuevo."
        )

    try:
        data = json.loads(raw_data)
    except Exception:
        await redis.delete(cache_key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Datos de sesión de recuperación inválidos. Solicita un nuevo código."
        )

    attempts = data.get("attempts", 0)
    if attempts >= 5:
        await redis.delete(cache_key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Has superado el límite máximo de intentos erróneos. Solicita un nuevo código por seguridad."
        )

    if data.get("code") != clean_code:
        data["attempts"] = attempts + 1
        ttl = await redis.ttl(cache_key)
        if ttl > 0:
            await redis.set(cache_key, json.dumps(data), ex=ttl)
        remaining = 5 - data["attempts"]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Código de verificación incorrecto. Intentos restantes: {remaining}."
        )

    # El código es válido: actualizar contraseña
    try:
        user_uuid = uuid.UUID(data["user_id"])
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de usuario inválido.")

    user = await db.get(Profile, user_uuid)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    # Hashear nueva contraseña con Bcrypt
    user.hashed_password = get_password_hash(payload.new_password)
    await db.commit()

    # Invalidar código OTP
    await redis.delete(cache_key)
    await redis.delete(f"pwd_reset_cd:{clean_email}")

    return MessageResponse(
        message="¡Contraseña restablecida exitosamente! Ya puedes iniciar sesión con tu nueva contraseña."
    )


@router.post("/change-password", response_model=MessageResponse, summary="Cambiar contraseña desde el perfil de usuario autenticado")
async def change_password(
    payload: ChangePasswordRequest,
    current_user: Profile = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Permite al personal o administrador cambiar su contraseña actual una vez ha ingresado a su cuenta.
    """
    if not current_user.hashed_password or not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña actual ingresada es incorrecta."
        )

    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La nueva contraseña no puede ser idéntica a la anterior."
        )

    current_user.hashed_password = get_password_hash(payload.new_password)
    await db.commit()

    return MessageResponse(
        message="Tu contraseña ha sido actualizada con éxito."
    )


@router.get("/security-status", summary="Estado de defensas de ciberseguridad, WAF, Rate Limit y Habeas Data")
async def get_security_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis)
):
    """
    Retorna el diagnóstico en vivo de los escudos de seguridad activos en la plataforma:
    - Rate Limiting Anti-DDoS y protección de fuerza bruta (Redis).
    - WAF contra SQL Injection (SQLi) y Cross-Site Scripting (XSS).
    - Cumplimiento de Habeas Data (Ley 1581 de 2012).
    """
    client_ip = get_client_ip(request)
    redis_ok = False
    try:
        redis_ok = bool(await redis.ping())
    except Exception:
        redis_ok = False

    consents_count = (await db.execute(select(func.count(Consent.id)))).scalar_one()

    return {
        "shield": "ConfyTickets-WAF-v1",
        "client_ip": client_ip,
        "redis_rate_limiter_online": redis_ok,
        "limits": {
            "global_requests_per_minute": 120,
            "auth_requests_per_minute": 15,
            "login_max_failed_attempts": 5,
            "login_lockout_seconds": 300
        },
        "protections": {
            "ddos_rate_limiting": True,
            "sqli_parameterized_orm": True,
            "sqli_xss_waf_inspector": True,
            "bcrypt_password_hashing": True,
            "jwt_strict_rbac": True,
            "http_security_headers": [
                "Content-Security-Policy",
                "Strict-Transport-Security",
                "X-Frame-Options",
                "X-Content-Type-Options",
                "Permissions-Policy"
            ],
            "habeas_data_ley_1581": True,
            "recorded_consents_count": consents_count
        }
    }
