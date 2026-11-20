# =============================================================================
# SCHEMAS PYDANTIC: RECOMPENSAS Y REDENCIONES
# =============================================================================

from uuid import UUID
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field

class CustomerRewardSchema(BaseModel):
    id: UUID
    name: str
    description: str
    reward_type: str
    status: str
    issued_at: datetime
    expires_at: Optional[datetime] = None
    redeemed_at: Optional[datetime] = None
    selected_product: Optional[str] = None
    eligible_stores: List[str] = []

    model_config = ConfigDict(from_attributes=True)


class RedemptionCreateRequest(BaseModel):
    qr_token: str = Field(..., description="Token escaneado del QR de redención del cliente")
    store_id: UUID = Field(..., description="Sede donde se canjea el premio")
    purchase_id: Optional[UUID] = None


class RedemptionResponse(BaseModel):
    redemption_id: UUID
    status: str
    reward_name: str
    customer_name: str
    redeemed_at: datetime

    model_config = ConfigDict(from_attributes=True)
