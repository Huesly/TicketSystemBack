# =============================================================================
# SCHEMAS PYDANTIC: AUTENTICACIÓN, REGISTRO Y ROLES
# =============================================================================

from typing import List, Optional
from pydantic import BaseModel, EmailStr, Field
from app.schemas.user import UserProfileSchema

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=3)

class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=3, max_length=150)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=100)
    phone: Optional[str] = Field(None, max_length=30)
    accept_terms: bool = Field(True, description="Consentimiento expreso de Habeas Data (Ley 1581 de 2012)")
    accept_marketing: bool = Field(False, description="Autorización opcional para envíos promocionales")

class RegisterStaffRequest(BaseModel):
    full_name: str = Field(..., min_length=3, max_length=150)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=100)
    phone: Optional[str] = Field(None, max_length=30)
    role: str = Field("GENERAL_ADMIN", pattern="^(GENERAL_ADMIN|CASHIER|STORE_ADMIN)$")
    store_id: Optional[str] = None

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserProfileSchema
    roles: List[str] = []

class AuthMeResponse(BaseModel):
    user: UserProfileSchema
    roles: List[str] = []

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=10)
    new_password: str = Field(..., min_length=6, max_length=100)

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=100)

class MessageResponse(BaseModel):
    message: str
    expires_in_minutes: Optional[int] = None
    dev_code: Optional[str] = None
