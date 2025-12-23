"""
CV management API routes.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rendercv.web.auth import get_current_active_user
from rendercv.web.database import get_db
from rendercv.web.models import CV, User
from rendercv.web.render_service import get_render_service
from rendercv.web.schemas import (
    CVCreate,
    CVDetailResponse,
    CVListResponse,
    CVResponse,
    CVUpdate,
    CVValidationRequest,
    CVValidationResponse,
    MessageResponse,
)

router = APIRouter(prefix="/cvs", tags=["CVs"])


@router.get("/", response_model=CVListResponse)
async def list_cvs(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    per_page: int = 20,
) -> CVListResponse:
    """
    List all CVs for the current user.
    """
    offset = (page - 1) * per_page

    # Get total count
    count_result = await db.execute(
        select(func.count(CV.id)).where(CV.user_id == current_user.id)
    )
    total = count_result.scalar() or 0

    # Get paginated results
    result = await db.execute(
        select(CV)
        .where(CV.user_id == current_user.id)
        .order_by(CV.updated_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    cvs = result.scalars().all()

    pages = (total + per_page - 1) // per_page

    return CVListResponse(
        items=[
            CVResponse(
                id=cv.id,
                user_id=cv.user_id,
                name=cv.name,
                description=cv.description,
                theme=cv.theme,
                is_public=cv.is_public,
                public_slug=cv.public_slug,
                created_at=cv.created_at,
                updated_at=cv.updated_at,
            )
            for cv in cvs
        ],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


@router.post("/", response_model=CVDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_cv(
    cv_data: CVCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CVDetailResponse:
    """
    Create a new CV.

    The YAML content is validated before saving.
    """
    # Validate YAML content
    render_service = get_render_service()
    is_valid, errors = await render_service.validate_yaml(cv_data.yaml_content)

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid CV YAML", "errors": errors},
        )

    # Create CV
    cv = CV(
        user_id=current_user.id,
        name=cv_data.name,
        description=cv_data.description,
        yaml_content=cv_data.yaml_content,
        design_override=cv_data.design_override,
        locale_override=cv_data.locale_override,
        theme=cv_data.theme,
        is_public=cv_data.is_public,
    )

    # Generate public slug if public
    if cv.is_public:
        cv.public_slug = f"{secrets.token_urlsafe(8)}"

    db.add(cv)
    await db.commit()
    await db.refresh(cv)

    return CVDetailResponse(
        id=cv.id,
        user_id=cv.user_id,
        name=cv.name,
        description=cv.description,
        theme=cv.theme,
        is_public=cv.is_public,
        public_slug=cv.public_slug,
        yaml_content=cv.yaml_content,
        design_override=cv.design_override,
        locale_override=cv.locale_override,
        created_at=cv.created_at,
        updated_at=cv.updated_at,
    )


@router.get("/{cv_id}", response_model=CVDetailResponse)
async def get_cv(
    cv_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CVDetailResponse:
    """
    Get a specific CV by ID.
    """
    result = await db.execute(
        select(CV).where(CV.id == cv_id, CV.user_id == current_user.id)
    )
    cv = result.scalar_one_or_none()

    if not cv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CV not found",
        )

    return CVDetailResponse(
        id=cv.id,
        user_id=cv.user_id,
        name=cv.name,
        description=cv.description,
        theme=cv.theme,
        is_public=cv.is_public,
        public_slug=cv.public_slug,
        yaml_content=cv.yaml_content,
        design_override=cv.design_override,
        locale_override=cv.locale_override,
        created_at=cv.created_at,
        updated_at=cv.updated_at,
    )


@router.patch("/{cv_id}", response_model=CVDetailResponse)
async def update_cv(
    cv_id: str,
    cv_update: CVUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CVDetailResponse:
    """
    Update a CV.

    Only provided fields will be updated.
    """
    result = await db.execute(
        select(CV).where(CV.id == cv_id, CV.user_id == current_user.id)
    )
    cv = result.scalar_one_or_none()

    if not cv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CV not found",
        )

    update_data = cv_update.model_dump(exclude_unset=True)

    # Validate YAML if being updated
    if "yaml_content" in update_data:
        render_service = get_render_service()
        is_valid, errors = await render_service.validate_yaml(update_data["yaml_content"])
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"message": "Invalid CV YAML", "errors": errors},
            )

    # Handle public slug
    if "is_public" in update_data:
        if update_data["is_public"] and not cv.public_slug:
            cv.public_slug = f"{secrets.token_urlsafe(8)}"
        elif not update_data["is_public"]:
            cv.public_slug = None

    # Apply updates
    for field, value in update_data.items():
        setattr(cv, field, value)

    await db.commit()
    await db.refresh(cv)

    return CVDetailResponse(
        id=cv.id,
        user_id=cv.user_id,
        name=cv.name,
        description=cv.description,
        theme=cv.theme,
        is_public=cv.is_public,
        public_slug=cv.public_slug,
        yaml_content=cv.yaml_content,
        design_override=cv.design_override,
        locale_override=cv.locale_override,
        created_at=cv.created_at,
        updated_at=cv.updated_at,
    )


@router.delete("/{cv_id}", response_model=MessageResponse)
async def delete_cv(
    cv_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    """
    Delete a CV and all its render jobs.
    """
    result = await db.execute(
        select(CV).where(CV.id == cv_id, CV.user_id == current_user.id)
    )
    cv = result.scalar_one_or_none()

    if not cv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CV not found",
        )

    await db.delete(cv)
    await db.commit()

    return MessageResponse(message="CV deleted successfully")


@router.post("/{cv_id}/duplicate", response_model=CVDetailResponse)
async def duplicate_cv(
    cv_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CVDetailResponse:
    """
    Create a copy of an existing CV.
    """
    result = await db.execute(
        select(CV).where(CV.id == cv_id, CV.user_id == current_user.id)
    )
    cv = result.scalar_one_or_none()

    if not cv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CV not found",
        )

    # Create duplicate
    new_cv = CV(
        user_id=current_user.id,
        name=f"{cv.name} (Copy)",
        description=cv.description,
        yaml_content=cv.yaml_content,
        design_override=cv.design_override,
        locale_override=cv.locale_override,
        theme=cv.theme,
        is_public=False,  # Copies start as private
    )

    db.add(new_cv)
    await db.commit()
    await db.refresh(new_cv)

    return CVDetailResponse(
        id=new_cv.id,
        user_id=new_cv.user_id,
        name=new_cv.name,
        description=new_cv.description,
        theme=new_cv.theme,
        is_public=new_cv.is_public,
        public_slug=new_cv.public_slug,
        yaml_content=new_cv.yaml_content,
        design_override=new_cv.design_override,
        locale_override=new_cv.locale_override,
        created_at=new_cv.created_at,
        updated_at=new_cv.updated_at,
    )


@router.post("/validate", response_model=CVValidationResponse)
async def validate_cv_yaml(
    validation_request: CVValidationRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> CVValidationResponse:
    """
    Validate CV YAML content without saving.
    """
    render_service = get_render_service()
    is_valid, errors = await render_service.validate_yaml(validation_request.yaml_content)

    return CVValidationResponse(
        is_valid=is_valid,
        errors=errors,
    )


# Public CV routes (no authentication required)


@router.get("/public/{slug}", response_model=CVResponse)
async def get_public_cv(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CVResponse:
    """
    Get a public CV by its slug.

    This endpoint does not require authentication.
    """
    result = await db.execute(
        select(CV).where(CV.public_slug == slug, CV.is_public.is_(True))
    )
    cv = result.scalar_one_or_none()

    if not cv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CV not found",
        )

    return CVResponse(
        id=cv.id,
        user_id=cv.user_id,
        name=cv.name,
        description=cv.description,
        theme=cv.theme,
        is_public=cv.is_public,
        public_slug=cv.public_slug,
        created_at=cv.created_at,
        updated_at=cv.updated_at,
    )
