"""
User management API routes.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rendercv.web.auth import get_current_active_user, get_current_superuser, hash_password
from rendercv.web.database import get_db
from rendercv.web.models import CV, RenderJob, User
from rendercv.web.schemas import (
    MessageResponse,
    UserCreate,
    UserProfile,
    UserResponse,
    UserStats,
    UserUpdate,
)
from rendercv.web.storage import get_file_service

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserProfile)
async def get_my_profile(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserProfile:
    """
    Get the current user's profile with detailed information.
    """
    # Count CVs
    cv_count_result = await db.execute(
        select(func.count(CV.id)).where(CV.user_id == current_user.id)
    )
    cv_count = cv_count_result.scalar() or 0

    return UserProfile(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        is_verified=current_user.is_verified,
        tier=current_user.tier,
        renders_this_month=current_user.renders_this_month,
        render_limit=current_user.render_limit,
        created_at=current_user.created_at,
        last_login_at=current_user.last_login_at,
        updated_at=current_user.updated_at,
        cv_count=cv_count,
    )


@router.patch("/me", response_model=UserResponse)
async def update_my_profile(
    user_update: UserUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    """
    Update the current user's profile.
    """
    update_data = user_update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(current_user, field, value)

    await db.commit()
    await db.refresh(current_user)

    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        is_verified=current_user.is_verified,
        tier=current_user.tier,
        renders_this_month=current_user.renders_this_month,
        render_limit=current_user.render_limit,
        created_at=current_user.created_at,
    )


@router.get("/me/stats", response_model=UserStats)
async def get_my_stats(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserStats:
    """
    Get usage statistics for the current user.
    """
    # Count CVs
    cv_count_result = await db.execute(
        select(func.count(CV.id)).where(CV.user_id == current_user.id)
    )
    total_cvs = cv_count_result.scalar() or 0

    # Count total renders
    render_count_result = await db.execute(
        select(func.count(RenderJob.id)).where(RenderJob.user_id == current_user.id)
    )
    total_renders = render_count_result.scalar() or 0

    # Get most used theme
    theme_result = await db.execute(
        select(CV.theme, func.count(CV.id).label("count"))
        .where(CV.user_id == current_user.id)
        .group_by(CV.theme)
        .order_by(func.count(CV.id).desc())
        .limit(1)
    )
    theme_row = theme_result.first()
    most_used_theme = theme_row[0] if theme_row else None

    # Calculate storage used
    file_service = get_file_service()
    storage_bytes = await file_service.get_user_storage_usage(current_user.id)
    storage_mb = storage_bytes / (1024 * 1024)

    return UserStats(
        total_cvs=total_cvs,
        total_renders=total_renders,
        renders_this_month=current_user.renders_this_month,
        render_limit=current_user.render_limit,
        most_used_theme=most_used_theme,
        storage_used_mb=round(storage_mb, 2),
    )


@router.delete("/me", response_model=MessageResponse)
async def delete_my_account(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """
    Delete the current user's account and all associated data.

    This action is irreversible.
    """
    # Delete user's files from storage
    file_service = get_file_service()
    await file_service.cleanup_user_files(current_user.id)

    # Delete user (cascades to CVs, render jobs, API keys, etc.)
    await db.delete(current_user)
    await db.commit()

    return MessageResponse(message="Account deleted successfully")


# Admin routes


@router.get("/", response_model=list[UserResponse])
async def list_users(
    current_user: Annotated[User, Depends(get_current_superuser)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    per_page: int = 20,
) -> list[UserResponse]:
    """
    List all users (admin only).
    """
    offset = (page - 1) * per_page
    result = await db.execute(
        select(User).order_by(User.created_at.desc()).offset(offset).limit(per_page)
    )
    users = result.scalars().all()

    return [
        UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_verified=user.is_verified,
            tier=user.tier,
            renders_this_month=user.renders_this_month,
            render_limit=user.render_limit,
            created_at=user.created_at,
        )
        for user in users
    ]


@router.get("/{user_id}", response_model=UserProfile)
async def get_user(
    user_id: str,
    current_user: Annotated[User, Depends(get_current_superuser)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserProfile:
    """
    Get a specific user by ID (admin only).
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Count CVs
    cv_count_result = await db.execute(
        select(func.count(CV.id)).where(CV.user_id == user.id)
    )
    cv_count = cv_count_result.scalar() or 0

    return UserProfile(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
        tier=user.tier,
        renders_this_month=user.renders_this_month,
        render_limit=user.render_limit,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        updated_at=user.updated_at,
        cv_count=cv_count,
    )


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    current_user: Annotated[User, Depends(get_current_superuser)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    """
    Create a new user (admin only).
    """
    from rendercv.web.auth import get_user_by_email

    # Check if email exists
    existing = await get_user_by_email(db, user_data.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        full_name=user_data.full_name,
        is_active=user_data.is_active,
        is_verified=user_data.is_verified,
        is_superuser=user_data.is_superuser,
        tier=user_data.tier.value,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
        tier=user.tier,
        renders_this_month=user.renders_this_month,
        render_limit=user.render_limit,
        created_at=user.created_at,
    )


@router.delete("/{user_id}", response_model=MessageResponse)
async def delete_user(
    user_id: str,
    current_user: Annotated[User, Depends(get_current_superuser)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """
    Delete a user by ID (admin only).
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account via admin API",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Delete files
    file_service = get_file_service()
    await file_service.cleanup_user_files(user.id)

    # Delete user
    await db.delete(user)
    await db.commit()

    return MessageResponse(message="User deleted successfully")
