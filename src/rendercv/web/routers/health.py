"""
Health check and system status routes.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rendercv.web.auth import get_current_superuser
from rendercv.web.config import get_settings
from rendercv.web.database import get_db
from rendercv.web.models import User
from rendercv.web.schemas import HealthResponse, SystemStats
from rendercv.web.tasks import get_queue_stats, get_redis_connection

settings = get_settings()
router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/", response_model=HealthResponse)
async def health_check(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HealthResponse:
    """
    Check the health of the service.

    Returns status of database and Redis connections.
    """
    # Check database
    db_status = "healthy"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unhealthy: {e}"

    # Check Redis
    redis_status = "healthy"
    try:
        redis = get_redis_connection()
        redis.ping()
    except Exception as e:
        redis_status = f"unhealthy: {e}"

    overall_status = "healthy"
    if "unhealthy" in db_status or "unhealthy" in redis_status:
        overall_status = "degraded"

    return HealthResponse(
        status=overall_status,
        version=settings.app_version,
        database=db_status,
        redis=redis_status,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/ready")
async def readiness_check(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """
    Kubernetes-style readiness probe.

    Returns 200 if the service is ready to accept traffic.
    """
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        return {"status": "not ready"}


@router.get("/live")
async def liveness_check() -> dict:
    """
    Kubernetes-style liveness probe.

    Returns 200 if the service process is running.
    """
    return {"status": "alive"}


@router.get("/stats", response_model=SystemStats)
async def system_stats(
    current_user: Annotated[User, Depends(get_current_superuser)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SystemStats:
    """
    Get system-wide statistics (admin only).
    """
    from sqlalchemy import func, select

    from rendercv.web.models import CV, RenderJob
    from rendercv.web.storage import get_file_service

    # Total users
    user_count = await db.execute(select(func.count(User.id)))
    total_users = user_count.scalar() or 0

    # Total CVs
    cv_count = await db.execute(select(func.count(CV.id)))
    total_cvs = cv_count.scalar() or 0

    # Total renders
    render_count = await db.execute(select(func.count(RenderJob.id)))
    total_renders = render_count.scalar() or 0

    # Renders today
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_renders = await db.execute(
        select(func.count(RenderJob.id)).where(RenderJob.created_at >= today_start)
    )
    renders_today = today_renders.scalar() or 0

    # Active users today
    active_today = await db.execute(
        select(func.count(User.id)).where(User.last_login_at >= today_start)
    )
    active_users_today = active_today.scalar() or 0

    # Storage calculation (simplified for local storage)
    storage_gb = 0.0
    try:
        file_service = get_file_service()
        if hasattr(file_service.backend, "base_path"):
            import os

            total_size = 0
            for dirpath, dirnames, filenames in os.walk(file_service.backend.base_path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    total_size += os.path.getsize(fp)
            storage_gb = total_size / (1024**3)
    except Exception:
        pass

    return SystemStats(
        total_users=total_users,
        total_cvs=total_cvs,
        total_renders=total_renders,
        renders_today=renders_today,
        active_users_today=active_users_today,
        storage_used_gb=round(storage_gb, 3),
    )


@router.get("/queues")
async def queue_stats(
    current_user: Annotated[User, Depends(get_current_superuser)],
) -> dict:
    """
    Get task queue statistics (admin only).
    """
    try:
        stats = get_queue_stats()
        return {"status": "healthy", "queues": stats}
    except Exception as e:
        return {"status": "error", "error": str(e)}
