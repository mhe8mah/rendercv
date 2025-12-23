"""
Render job API routes.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rendercv.web.auth import get_current_active_user
from rendercv.web.config import get_settings
from rendercv.web.database import get_db
from rendercv.web.models import CV, RenderJob, RenderStatus, User, OutputFormat
from rendercv.web.render_service import get_render_service
from rendercv.web.schemas import (
    RenderJobListResponse,
    RenderJobResponse,
    RenderRequest,
)
from rendercv.web.storage import get_file_service
from rendercv.web.tasks import enqueue_render_job

settings = get_settings()
router = APIRouter(prefix="/render", tags=["Render"])


@router.post("/{cv_id}", response_model=RenderJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def render_cv(
    cv_id: str,
    render_request: RenderRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RenderJobResponse:
    """
    Start a render job for a CV.

    The render job will be processed asynchronously. Poll the job status
    endpoint to check when it completes.
    """
    # Check render limits
    if not current_user.can_render():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Monthly render limit ({current_user.render_limit}) exceeded",
        )

    # Get the CV
    result = await db.execute(
        select(CV).where(CV.id == cv_id, CV.user_id == current_user.id)
    )
    cv = result.scalar_one_or_none()

    if not cv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CV not found",
        )

    # Create render job
    render_service = get_render_service()
    job = await render_service.create_render_job(db, cv, render_request.output_format)

    # Increment render count
    current_user.renders_this_month += 1
    if not current_user.renders_reset_at:
        current_user.renders_reset_at = datetime.now(timezone.utc)
    await db.commit()

    # Enqueue for background processing
    try:
        enqueue_render_job(job.id)
    except Exception:
        # If Redis is unavailable, process synchronously
        job = await render_service.process_render_job(db, job.id)

    return RenderJobResponse(
        id=job.id,
        cv_id=job.cv_id,
        status=job.status,
        output_format=job.output_format,
        output_url=job.output_url,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        duration_seconds=job.duration_seconds,
    )


@router.post("/{cv_id}/sync", response_model=RenderJobResponse)
async def render_cv_sync(
    cv_id: str,
    render_request: RenderRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RenderJobResponse:
    """
    Render a CV synchronously (waits for completion).

    Use this for immediate results when you need the output right away.
    For larger CVs, prefer the async endpoint.
    """
    # Check render limits
    if not current_user.can_render():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Monthly render limit ({current_user.render_limit}) exceeded",
        )

    # Get the CV
    result = await db.execute(
        select(CV).where(CV.id == cv_id, CV.user_id == current_user.id)
    )
    cv = result.scalar_one_or_none()

    if not cv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CV not found",
        )

    # Create and process render job synchronously
    render_service = get_render_service()
    job = await render_service.create_render_job(db, cv, render_request.output_format)

    # Increment render count
    current_user.renders_this_month += 1
    if not current_user.renders_reset_at:
        current_user.renders_reset_at = datetime.now(timezone.utc)
    await db.commit()

    # Process synchronously
    job = await render_service.process_render_job(db, job.id)

    if job.status == RenderStatus.FAILED.value:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=job.error_message or "Render failed",
        )

    return RenderJobResponse(
        id=job.id,
        cv_id=job.cv_id,
        status=job.status,
        output_format=job.output_format,
        output_url=job.output_url,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        duration_seconds=job.duration_seconds,
    )


@router.get("/jobs", response_model=RenderJobListResponse)
async def list_render_jobs(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    per_page: int = 20,
    cv_id: str | None = None,
    status_filter: str | None = None,
) -> RenderJobListResponse:
    """
    List render jobs for the current user.
    """
    from sqlalchemy import func

    # Build query
    query = select(RenderJob).where(RenderJob.user_id == current_user.id)
    count_query = select(func.count(RenderJob.id)).where(
        RenderJob.user_id == current_user.id
    )

    if cv_id:
        query = query.where(RenderJob.cv_id == cv_id)
        count_query = count_query.where(RenderJob.cv_id == cv_id)

    if status_filter:
        query = query.where(RenderJob.status == status_filter)
        count_query = count_query.where(RenderJob.status == status_filter)

    # Get total
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Get paginated results
    offset = (page - 1) * per_page
    query = query.order_by(RenderJob.created_at.desc()).offset(offset).limit(per_page)
    result = await db.execute(query)
    jobs = result.scalars().all()

    return RenderJobListResponse(
        items=[
            RenderJobResponse(
                id=job.id,
                cv_id=job.cv_id,
                status=job.status,
                output_format=job.output_format,
                output_url=job.output_url,
                error_message=job.error_message,
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                duration_seconds=job.duration_seconds,
            )
            for job in jobs
        ],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/jobs/{job_id}", response_model=RenderJobResponse)
async def get_render_job(
    job_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RenderJobResponse:
    """
    Get the status of a specific render job.
    """
    result = await db.execute(
        select(RenderJob).where(
            RenderJob.id == job_id,
            RenderJob.user_id == current_user.id,
        )
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Render job not found",
        )

    return RenderJobResponse(
        id=job.id,
        cv_id=job.cv_id,
        status=job.status,
        output_format=job.output_format,
        output_url=job.output_url,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        duration_seconds=job.duration_seconds,
    )


@router.get("/jobs/{job_id}/download")
async def download_render_output(
    job_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """
    Download the output of a completed render job.
    """
    result = await db.execute(
        select(RenderJob).where(
            RenderJob.id == job_id,
            RenderJob.user_id == current_user.id,
        )
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Render job not found",
        )

    if job.status != RenderStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Render job is not completed (status: {job.status})",
        )

    if not job.output_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Output file not found",
        )

    # Get file content
    file_service = get_file_service()
    content = await file_service.get_render_output(job.output_path)

    if not content:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Output file not found in storage",
        )

    # Determine content type
    content_types = {
        OutputFormat.PDF.value: "application/pdf",
        OutputFormat.PNG.value: "image/png",
        OutputFormat.HTML.value: "text/html",
        OutputFormat.MARKDOWN.value: "text/markdown",
    }
    content_type = content_types.get(job.output_format, "application/octet-stream")

    # Get CV name for filename
    cv_result = await db.execute(select(CV).where(CV.id == job.cv_id))
    cv = cv_result.scalar_one_or_none()
    filename = f"{cv.name if cv else 'cv'}.{job.output_format}"

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.delete("/jobs/{job_id}")
async def delete_render_job(
    job_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """
    Delete a render job and its output file.
    """
    result = await db.execute(
        select(RenderJob).where(
            RenderJob.id == job_id,
            RenderJob.user_id == current_user.id,
        )
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Render job not found",
        )

    # Delete output file if exists
    if job.output_path:
        file_service = get_file_service()
        await file_service.delete_render_output(job.output_path)

    await db.delete(job)
    await db.commit()

    return {"message": "Render job deleted successfully"}
