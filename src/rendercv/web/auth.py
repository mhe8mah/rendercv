"""
Authentication and authorization for RenderCV SaaS.

Implements JWT-based authentication with refresh tokens.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rendercv.web.config import get_settings
from rendercv.web.database import get_db
from rendercv.web.models import APIKey, RefreshToken, User

settings = get_settings()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bearer token security
security = HTTPBearer(auto_error=False)


class TokenPayload(BaseModel):
    """JWT token payload."""

    sub: str  # User ID
    exp: datetime
    type: str = "access"  # 'access' or 'refresh'


class TokenPair(BaseModel):
    """Access and refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # Seconds until access token expires


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)


def hash_token(token: str) -> str:
    """Hash a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user_id: str) -> str:
    """Create a new JWT access token."""
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {
        "sub": user_id,
        "exp": expires,
        "type": "access",
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_refresh_token() -> str:
    """Create a secure random refresh token."""
    return secrets.token_urlsafe(32)


async def create_token_pair(
    db: AsyncSession,
    user: User,
    device_info: str | None = None,
    ip_address: str | None = None,
) -> TokenPair:
    """Create a new access/refresh token pair."""
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token()

    # Store refresh token in database
    expires = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    db_token = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_token),
        expires_at=expires,
        device_info=device_info,
        ip_address=ip_address,
    )
    db.add(db_token)
    await db.commit()

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


def decode_access_token(token: str) -> TokenPayload | None:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
        if payload.get("type") != "access":
            return None
        return TokenPayload(**payload)
    except JWTError:
        return None


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Get a user by email address."""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    """Get a user by ID."""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    """Authenticate a user with email and password."""
    user = await get_user_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Get the current authenticated user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not credentials:
        raise credentials_exception

    token = credentials.credentials

    # Try JWT token first
    payload = decode_access_token(token)
    if payload:
        user = await get_user_by_id(db, payload.sub)
        if user and user.is_active:
            return user
        raise credentials_exception

    # Try API key
    user = await authenticate_api_key(db, token)
    if user:
        return user

    raise credentials_exception


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get current user and verify they are active."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )
    return current_user


async def get_current_superuser(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """Get current user and verify they are a superuser."""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser access required",
        )
    return current_user


async def authenticate_api_key(db: AsyncSession, api_key: str) -> User | None:
    """Authenticate a user via API key."""
    key_hash = hash_token(api_key)
    result = await db.execute(
        select(APIKey)
        .where(APIKey.key_hash == key_hash)
        .where(APIKey.is_active.is_(True))
    )
    db_key = result.scalar_one_or_none()

    if not db_key or not db_key.is_valid():
        return None

    # Update usage stats
    db_key.last_used_at = datetime.now(timezone.utc)
    db_key.usage_count += 1
    await db.commit()

    # Get user
    user = await get_user_by_id(db, db_key.user_id)
    return user if user and user.is_active else None


async def refresh_access_token(
    db: AsyncSession,
    refresh_token: str,
) -> TokenPair | None:
    """Refresh an access token using a refresh token."""
    token_hash = hash_token(refresh_token)

    result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .where(RefreshToken.is_revoked.is_(False))
    )
    db_token = result.scalar_one_or_none()

    if not db_token:
        return None

    # Check if expired
    if datetime.now(timezone.utc) > db_token.expires_at:
        db_token.is_revoked = True
        await db.commit()
        return None

    # Get user
    user = await get_user_by_id(db, db_token.user_id)
    if not user or not user.is_active:
        return None

    # Revoke old token and create new pair
    db_token.is_revoked = True
    await db.commit()

    return await create_token_pair(
        db,
        user,
        device_info=db_token.device_info,
        ip_address=db_token.ip_address,
    )


async def revoke_refresh_token(db: AsyncSession, refresh_token: str) -> bool:
    """Revoke a refresh token."""
    token_hash = hash_token(refresh_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    db_token = result.scalar_one_or_none()

    if db_token:
        db_token.is_revoked = True
        await db.commit()
        return True
    return False


async def revoke_all_user_tokens(db: AsyncSession, user_id: str) -> int:
    """Revoke all refresh tokens for a user."""
    result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == user_id)
        .where(RefreshToken.is_revoked.is_(False))
    )
    tokens = result.scalars().all()

    for token in tokens:
        token.is_revoked = True

    await db.commit()
    return len(tokens)


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        Tuple of (full_key, key_prefix, key_hash)
    """
    key = f"rcv_{secrets.token_urlsafe(32)}"
    prefix = key[:12]
    key_hash = hash_token(key)
    return key, prefix, key_hash
