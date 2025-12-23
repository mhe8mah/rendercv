"""
SQLAlchemy database models for RenderCV SaaS.

Defines User, CV, RenderJob, and related models.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rendercv.web.database import Base

if TYPE_CHECKING:
    pass


def generate_uuid() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


class UserTier(str, Enum):
    """User subscription tiers."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class RenderStatus(str, Enum):
    """Status of a render job."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class OutputFormat(str, Enum):
    """Output format for rendered CVs."""

    PDF = "pdf"
    PNG = "png"
    HTML = "html"
    MARKDOWN = "markdown"


class User(Base):
    """User account model."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))

    # Account status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)

    # Subscription
    tier: Mapped[str] = mapped_column(String(50), default=UserTier.FREE.value)
    renders_this_month: Mapped[int] = mapped_column(Integer, default=0)
    renders_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    cvs: Mapped[list["CV"]] = relationship(
        "CV",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    api_keys: Mapped[list["APIKey"]] = relationship(
        "APIKey",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_users_email_active", "email", "is_active"),)

    @property
    def render_limit(self) -> int:
        """Get monthly render limit based on tier."""
        limits = {
            UserTier.FREE.value: 10,
            UserTier.PRO.value: 100,
            UserTier.ENTERPRISE.value: 1000,
        }
        return limits.get(self.tier, 10)

    def can_render(self) -> bool:
        """Check if user can render more CVs this month."""
        return self.renders_this_month < self.render_limit


class CV(Base):
    """CV/Resume model."""

    __tablename__ = "cvs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # CV content stored as YAML
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)

    # Design settings (JSON)
    design_override: Mapped[str | None] = mapped_column(Text)
    locale_override: Mapped[str | None] = mapped_column(Text)

    # Theme
    theme: Mapped[str] = mapped_column(String(50), default="classic")

    # Metadata
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    public_slug: Mapped[str | None] = mapped_column(String(100), unique=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="cvs")
    render_jobs: Mapped[list["RenderJob"]] = relationship(
        "RenderJob",
        back_populates="cv",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_cvs_user_id_name", "user_id", "name"),
        Index("ix_cvs_public_slug", "public_slug"),
    )


class RenderJob(Base):
    """Render job model for tracking CV generation."""

    __tablename__ = "render_jobs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    cv_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("cvs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Job status
    status: Mapped[str] = mapped_column(
        String(20),
        default=RenderStatus.PENDING.value,
        index=True,
    )
    output_format: Mapped[str] = mapped_column(
        String(20),
        default=OutputFormat.PDF.value,
    )

    # Output files
    output_path: Mapped[str | None] = mapped_column(String(500))
    output_url: Mapped[str | None] = mapped_column(String(500))
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)

    # Error tracking
    error_message: Mapped[str | None] = mapped_column(Text)

    # Timing
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    cv: Mapped["CV"] = relationship("CV", back_populates="render_jobs")

    __table_args__ = (
        Index("ix_render_jobs_status_created", "status", "created_at"),
        Index("ix_render_jobs_user_id_created", "user_id", "created_at"),
    )

    @property
    def duration_seconds(self) -> float | None:
        """Calculate job duration in seconds."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class APIKey(Base):
    """API key for programmatic access."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )  # First 8 chars for display

    # Permissions
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    scopes: Mapped[str] = mapped_column(
        String(500),
        default="read,write,render",
    )  # Comma-separated

    # Usage tracking
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_count: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="api_keys")

    def is_valid(self) -> bool:
        """Check if API key is valid and not expired."""
        if not self.is_active:
            return False
        if self.expires_at and datetime.now(tz=self.expires_at.tzinfo) > self.expires_at:
            return False
        return True


class RefreshToken(Base):
    """Refresh token for JWT authentication."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    # Token metadata
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    device_info: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(String(45))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_refresh_tokens_user_expires", "user_id", "expires_at"),)
