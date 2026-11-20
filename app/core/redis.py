# =============================================================================
# CONEXIÓN ASÍNCRONA A REDIS (CACHE Y TOKENS OTP)
# =============================================================================

import redis.asyncio as aioredis
from app.core.config import settings

# Cliente global reutilizable con pool de conexiones
redis_client = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=20
)

async def get_redis() -> aioredis.Redis:
    """Inyección de dependencias para FastAPI."""
    return redis_client
