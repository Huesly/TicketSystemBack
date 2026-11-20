# =============================================================================
# MIDDLEWARE DE CIBERSEGURIDAD: ANTI-DDOS, RATE LIMITING, WAF SQLi/XSS & HEADERS
# =============================================================================

import re
import time
import logging
from typing import Callable, Tuple
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.redis import redis_client

logger = logging.getLogger("confytickets.security")

# Patrones de detección temprana de inyección SQL y XSS en parámetros/payloads
SQLI_XSS_PATTERNS = [
    re.compile(r"(\bUNION\b\s+\bSELECT\b)", re.IGNORECASE),
    re.compile(r"(\bDROP\b\s+\b(TABLE|DATABASE|SCHEMA)\b)", re.IGNORECASE),
    re.compile(r"(\bTRUNCATE\b\s+\bTABLE\b)", re.IGNORECASE),
    re.compile(r"(\bOR\b\s+['\"]?1['\"]?\s*=\s*['\"]?1['\"]?)", re.IGNORECASE),
    re.compile(r"(--\s|;\s*--\s|/\*.*\*/)", re.IGNORECASE),
    re.compile(r"(\bEXEC(\s|\+)+(s|x)p\w+)", re.IGNORECASE),
    re.compile(r"(<script\b[^>]*>[\s\S]*?</script>)", re.IGNORECASE),
    re.compile(r"(javascript\s*:)", re.IGNORECASE),
    re.compile(r"(\bSLEEP\s*\(\s*\d+\s*\))", re.IGNORECASE),
    re.compile(r"(\bPG_SLEEP\s*\(\s*\d+\s*\))", re.IGNORECASE),
]


def get_client_ip(request: Request) -> str:
    """Obtiene la IP real del cliente considerando proxies inversos y túneles Ngrok."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def inspect_malicious_payload(raw_text: str) -> Tuple[bool, str]:
    """
    Inspecciona cadenas de consulta o cuerpos de petición en busca de vectores
    clásicos de SQL Injection (SQLi) o Cross-Site Scripting (XSS).
    """
    if not raw_text:
        return False, ""
    for pattern in SQLI_XSS_PATTERNS:
        match = pattern.search(raw_text)
        if match:
            return True, match.group(0)
    return False, ""


class CybersecurityMiddleware(BaseHTTPMiddleware):
    """
    Middleware integral de ciberseguridad:
    1. Contención de ataques DDoS / Fuerza Bruta mediante Rate Limiting en Redis.
    2. Inspección WAF contra SQL Injection y XSS en Query Strings y Body JSON.
    3. Inyección de cabeceras HTTP de endurecimiento (CSP, HSTS, X-Frame-Options, nosniff).
    """

    GLOBAL_LIMIT_PER_MIN = 120
    AUTH_LIMIT_PER_MIN = 15
    WINDOW_SECONDS = 60

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_ip = get_client_ip(request)
        path = request.url.path

        # ---------------------------------------------------------------------
        # 1. INSPECCIÓN WAF (SQL Injection / XSS) EN URL Y QUERY PARAMS
        #    (Excluimos el endpoint administrativo de importación SQL controlada)
        # ---------------------------------------------------------------------
        if not path.endswith("/admin/sql-import"):
            raw_query = str(request.url.query or "")
            is_malicious, signature = inspect_malicious_payload(raw_query)
            if is_malicious:
                logger.warning(f"[WAF BLOCK] IP={client_ip} Path={path} Signature={signature}")
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "detail": "Petición bloqueada por el escudo de ciberseguridad (patrón sospechoso de inyección SQL/XSS detectado en parámetros).",
                        "code": "WAF_SQLI_XSS_BLOCKED",
                        "signature": signature,
                    },
                )

            # Inspeccionar cuerpo en peticiones POST/PUT/PATCH de tamaño moderado
            if request.method in {"POST", "PUT", "PATCH"}:
                content_type = request.headers.get("content-type", "")
                if "application/json" in content_type:
                    try:
                        body_bytes = await request.body()
                        body_text = body_bytes.decode("utf-8", errors="ignore")
                        is_bad_body, body_sig = inspect_malicious_payload(body_text)
                        if is_bad_body:
                            logger.warning(f"[WAF BODY BLOCK] IP={client_ip} Path={path} Signature={body_sig}")
                            return JSONResponse(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                content={
                                    "detail": "Petición bloqueada por el escudo de ciberseguridad (intento de inyección SQL o script malicioso detectado).",
                                    "code": "WAF_SQLI_XSS_BLOCKED",
                                    "signature": body_sig,
                                },
                            )
                    except Exception:
                        pass

        # ---------------------------------------------------------------------
        # 2. RATE LIMITING ANTI-DDOS Y PROTECCIÓN DE AUTENTICACIÓN (REDIS)
        # ---------------------------------------------------------------------
        is_auth_route = path.startswith("/api/v1/auth/login") or path.startswith("/api/v1/auth/register")
        limit = self.AUTH_LIMIT_PER_MIN if is_auth_route else self.GLOBAL_LIMIT_PER_MIN
        bucket_prefix = "rl:auth" if is_auth_route else "rl:global"
        minute_slot = int(time.time() // self.WINDOW_SECONDS)
        redis_key = f"{bucket_prefix}:{client_ip}:{minute_slot}"

        current_count = 1
        remaining = limit - 1
        try:
            current_count = await redis_client.incr(redis_key)
            if current_count == 1:
                await redis_client.expire(redis_key, self.WINDOW_SECONDS)
            remaining = max(0, limit - current_count)

            if current_count > limit:
                logger.warning(f"[RATE LIMIT EXCEEDED] IP={client_ip} Path={path} Count={current_count}/{limit}")
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "detail": "Has excedido el límite de peticiones permitidas por minuto (Protección Anti-DDoS activa). Intenta nuevamente en unos segundos.",
                        "code": "RATE_LIMIT_EXCEEDED",
                        "retry_after_seconds": self.WINDOW_SECONDS,
                    },
                    headers={
                        "Retry-After": str(self.WINDOW_SECONDS),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                    },
                )
        except Exception as e:
            # Fail-open controlado si Redis está temporalmente inaccesible
            logger.debug(f"Redis rate-limit bypass temporal: {e}")

        # ---------------------------------------------------------------------
        # 3. EJECUTAR ENDPOINT Y ENDURECER CABECERAS HTTP DE RESPUESTA
        # ---------------------------------------------------------------------
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(self), geolocation=(self), microphone=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' https: data: blob: 'unsafe-inline' 'unsafe-eval'; "
            "connect-src 'self' http: https: ws: wss:; "
            "img-src 'self' https: data: blob:; "
            "frame-ancestors 'self';"
        )
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-Security-Shield"] = "ConfyTickets-WAF-v1"

        return response
