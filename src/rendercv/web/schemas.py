"""
Pydantic schemas for API request/response models.

These schemas define the structure of data sent and received by the API.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from rendercv.web.models import OutputFormat, RenderStatus, UserTier


# ============================================================================
# Auth Schemas
# ============================================================================


class UserRegister(BaseModel):
    """User registration request."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str | None = Field(None, max_length=255)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Ensure password has minimum complexity."""
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserLogin(BaseModel):
    """User login request."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Token response after login."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(BaseModel):
    """Refresh token request."""

    refresh_token: str


class PasswordResetRequest(BaseModel):
    """Password reset request."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Password reset confirmation."""

    token: str
    new_password: str = Field(..., min_length=8, max_length=128)


class PasswordChange(BaseModel):
    """Password change request."""

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


# ============================================================================
# User Schemas
# ============================================================================


class UserBase(BaseModel):
    """Base user schema."""

    email: EmailStr
    full_name: str | None = None


class UserCreate(UserBase):
    """User creation schema (admin)."""

    password: str
    is_active: bool = True
    is_verified: bool = False
    is_superuser: bool = False
    tier: UserTier = UserTier.FREE


class UserUpdate(BaseModel):
    """User update schema."""

    email: EmailStr | None = None
    full_name: str | None = None


class UserResponse(UserBase):
    """User response schema."""

    id: str
    is_active: bool
    is_verified: bool
    tier: str
    renders_this_month: int
    render_limit: int
    created_at: datetime

    class Config:
        from_attributes = True


class UserProfile(UserResponse):
    """Detailed user profile."""

    last_login_at: datetime | None
    updated_at: datetime
    cv_count: int = 0


# ============================================================================
# CV Schemas
# ============================================================================


class CVBase(BaseModel):
    """Base CV schema."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    theme: str = Field(default="classic", max_length=50)
    is_public: bool = False


class CVCreate(CVBase):
    """CV creation schema."""

    yaml_content: str = Field(..., min_length=1)
    design_override: str | None = None
    locale_override: str | None = None


class CVUpdate(BaseModel):
    """CV update schema."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    yaml_content: str | None = None
    design_override: str | None = None
    locale_override: str | None = None
    theme: str | None = Field(None, max_length=50)
    is_public: bool | None = None


class CVResponse(CVBase):
    """CV response schema."""

    id: str
    user_id: str
    public_slug: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CVDetailResponse(CVResponse):
    """Detailed CV response with content."""

    yaml_content: str
    design_override: str | None
    locale_override: str | None


class CVListResponse(BaseModel):
    """Paginated CV list response."""

    items: list[CVResponse]
    total: int
    page: int
    per_page: int
    pages: int


# ============================================================================
# Render Job Schemas
# ============================================================================


class RenderRequest(BaseModel):
    """Render job request."""

    output_format: OutputFormat = OutputFormat.PDF


class RenderJobResponse(BaseModel):
    """Render job response."""

    id: str
    cv_id: str
    status: str
    output_format: str
    output_url: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None

    class Config:
        from_attributes = True


class RenderJobListResponse(BaseModel):
    """Paginated render job list."""

    items: list[RenderJobResponse]
    total: int
    page: int
    per_page: int


# ============================================================================
# API Key Schemas
# ============================================================================


class APIKeyCreate(BaseModel):
    """API key creation request."""

    name: str = Field(..., min_length=1, max_length=100)
    scopes: str = "read,write,render"
    expires_in_days: int | None = Field(None, ge=1, le=365)


class APIKeyResponse(BaseModel):
    """API key response (without full key)."""

    id: str
    name: str
    key_prefix: str
    scopes: str
    is_active: bool
    last_used_at: datetime | None
    usage_count: int
    created_at: datetime
    expires_at: datetime | None

    class Config:
        from_attributes = True


class APIKeyCreatedResponse(APIKeyResponse):
    """API key creation response (includes full key - shown only once)."""

    api_key: str  # Full API key, only shown once


# ============================================================================
# Validation Schemas
# ============================================================================


class CVValidationRequest(BaseModel):
    """CV YAML validation request."""

    yaml_content: str


class CVValidationResponse(BaseModel):
    """CV validation response."""

    is_valid: bool
    errors: list[dict] | None = None
    warnings: list[str] | None = None


# ============================================================================
# Stats Schemas
# ============================================================================


class UserStats(BaseModel):
    """User statistics."""

    total_cvs: int
    total_renders: int
    renders_this_month: int
    render_limit: int
    most_used_theme: str | None
    storage_used_mb: float


class SystemStats(BaseModel):
    """System-wide statistics (admin only)."""

    total_users: int
    total_cvs: int
    total_renders: int
    renders_today: int
    active_users_today: int
    storage_used_gb: float


# ============================================================================
# Generic Schemas
# ============================================================================


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
    detail: str | None = None


class ErrorResponse(BaseModel):
    """Error response."""

    error: str
    detail: str | None = None
    code: str | None = None


class PaginationParams(BaseModel):
    """Pagination parameters."""

    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        """Calculate offset for database query."""
        return (self.page - 1) * self.per_page


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    database: str
    redis: str
    timestamp: datetime
