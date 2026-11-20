# =============================================================================
# SCHEMAS PYDANTIC: TOKENS QR DINÁMICOS
# =============================================================================

from datetime import datetime
from pydantic import BaseModel, Field

class QrTokenResponse(BaseModel):
    qr_token: str = Field(..., description="Token opaco y efímero para renderizar en QR")
    purpose: str = Field(..., description="PURCHASE o REDEMPTION")
    expires_in_seconds: int = Field(default=60)
    expires_at: datetime
