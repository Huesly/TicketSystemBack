# =============================================================================
# SCHEMAS PYDANTIC: FIDELIZACIÓN Y TARJETAS
# =============================================================================

from uuid import UUID
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field

class StampMovementSchema(BaseModel):
    id: UUID
    quantity: int
    movement_type: str
    source_store_name: Optional[str] = None
    created_at: datetime
    reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CardSummarySchema(BaseModel):
    id: UUID
    cycle_number: int
    stamps_count: int = Field(..., ge=0, le=8)
    stamps_required: int = Field(default=8)
    stamps_remaining: int
    progress_percentage: float
    status: str
    opened_at: datetime
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CustomerCardsResponse(BaseModel):
    active_card: Optional[CardSummarySchema] = None
    past_cycles: List[CardSummarySchema] = []
