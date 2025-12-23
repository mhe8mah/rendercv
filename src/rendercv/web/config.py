"""
Configuration settings for RenderCV Web SaaS.

Uses Pydantic Settings for environment variable management.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "RenderCV SaaS"
    app_version: str = "1.0.0"
    debug: bool = False
    environment: Literal["development", "staging", "production"] = "development"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4

    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///./rendercv.db",
        description="Database connection URL (async driver required)",
    )

    # Redis (for job queue and caching)
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for job queue",
    )

    # Authentication
    secret_key: str = Field(
        default="change-this-in-production-use-openssl-rand-hex-32",
        description="Secret key for JWT encoding",
    )
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # File Storage
    storage_backend: Literal["local", "s3"] = "local"
    storage_path: Path = Field(
        default=Path("./storage"),
        description="Local file storage path",
    )

    # S3 Configuration (when storage_backend is 's3')
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_endpoint_url: str | None = None  # For MinIO or other S3-compatible storage

    # Rate Limiting
    rate_limit_per_minute: int = 60
    rate_limit_renders_per_hour: int = 20

    # CORS
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8000"],
        description="Allowed CORS origins",
    )

    # Email (for password reset, notifications)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@rendercv.com"
    smtp_tls: bool = True

    # Render Settings
    max_render_timeout_seconds: int = 120
    max_cv_size_kb: int = 500
    cleanup_old_renders_after_days: int = 30

    @computed_field
    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.environment == "production"

    @computed_field
    @property
    def storage_dir(self) -> Path:
        """Get storage directory, creating if needed."""
        self.storage_path.mkdir(parents=True, exist_ok=True)
        return self.storage_path


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
