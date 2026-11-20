# =============================================================================
# PUNTO DE ENTRADA PRINCIPAL FASTAPI (CONFYTICKETS)
# =============================================================================

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.security_middleware import CybersecurityMiddleware
from app.api.v1.router import api_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="API REST Centralizada para la Plataforma de Fidelización Multisede ConfyTickets",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Middleware integral de Ciberseguridad (Anti-DDoS Rate Limit, WAF SQLi/XSS, Cabeceras HTTP)
app.add_middleware(CybersecurityMiddleware)

# Configuración de CORS compatible con local, apps móviles y túneles ngrok
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-Security-Shield", "Content-Security-Policy"]
)

# Montar enrutador v1
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/health", tags=["Salud"], status_code=status.HTTP_200_OK)
async def health_check():
    """Endpoint de verificación de estado y conectividad del servicio."""
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "version": "1.0.0"
    }

@app.get("/", tags=["Root"])
async def root():
    return {
        "message": f"Bienvenido a la API de {settings.PROJECT_NAME}",
        "documentation": "/docs"
    }
