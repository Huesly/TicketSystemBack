# =============================================================================
# ENRUTADOR PRINCIPAL API V1 (/api/v1)
# =============================================================================

from fastapi import APIRouter
from app.api.v1.auth import router as auth_router
from app.api.v1.me import router as me_router
from app.api.v1.cashier import router as cashier_router
from app.api.v1.admin import router as admin_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(me_router)
api_router.include_router(cashier_router)
api_router.include_router(admin_router)
