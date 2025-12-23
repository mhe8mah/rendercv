"""
Background task processing for RenderCV SaaS.

Uses Redis Queue (RQ) for asynchronous job processing.
"""

import logging
from datetime import datetime, timezone

from redis import Redis
from rq import Queue, Worker
from rq.job import Job

from rendercv.web.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


def get_redis_connection() -> Redis:
    """Get Redis connection."""
    return Redis.from_url(settings.redis_url)


def get_task_queue() -> Queue:
    """Get the default task queue."""
    return Queue("default", connection=get_redis_connection())


def get_render_queue() -> Queue:
    """Get the render-specific queue."""
    return Queue("render", connection=get_redis_connection())


def enqueue_render_job(job_id: str) -> Job:
    """Enqueue a render job for background processing."""
    queue = get_render_queue()
    return queue.enqueue(
        "rendercv.web.tasks.process_render_job",
        job_id,
        job_timeout=settings.max_render_timeout_seconds,
        result_ttl=3600,  # Keep result for 1 hour
        failure_ttl=86400,  # Keep failed jobs for 24 hours
    )


def process_render_job(job_id: str) -> dict:
    """
    Process a render job (runs in worker process).

    This function is executed by RQ workers.
    """
    import asyncio
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from rendercv.web.models import RenderJob, RenderStatus, CV
    from rendercv.web.storage import get_file_service

    # Use sync engine for worker process
    sync_engine = create_engine(
        settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", ""),
    )
    Session = sessionmaker(bind=sync_engine)
    session = Session()

    try:
        # Get the job
        job = session.query(RenderJob).filter(RenderJob.id == job_id).first()
        if not job:
            return {"error": f"Job {job_id} not found"}

        # Get the CV
        cv = session.query(CV).filter(CV.id == job.cv_id).first()
        if not cv:
            job.status = RenderStatus.FAILED.value
            job.error_message = "CV not found"
            session.commit()
            return {"error": "CV not found"}

        # Update status
        job.status = RenderStatus.PROCESSING.value
        job.started_at = datetime.now(timezone.utc)
        session.commit()

        # Render the CV
        try:
            output_content = _render_cv_sync(
                cv.yaml_content,
                cv.design_override,
                cv.locale_override,
                job.output_format,
            )

            # Save output
            file_service = get_file_service()
            path, url, size = asyncio.run(
                file_service.save_render_output(
                    output_content,
                    cv.user_id,
                    cv.id,
                    job.id,
                    job.output_format,
                )
            )

            # Update job
            job.status = RenderStatus.COMPLETED.value
            job.output_path = path
            job.output_url = url
            job.file_size_bytes = size
            job.completed_at = datetime.now(timezone.utc)
            session.commit()

            return {
                "status": "completed",
                "job_id": job_id,
                "output_url": url,
            }

        except Exception as e:
            logger.exception(f"Render failed for job {job_id}")
            job.status = RenderStatus.FAILED.value
            job.error_message = str(e)
            job.completed_at = datetime.now(timezone.utc)
            session.commit()
            return {"error": str(e)}

    finally:
        session.close()


def _render_cv_sync(
    yaml_content: str,
    design_override: str | None,
    locale_override: str | None,
    output_format: str,
) -> bytes:
    """Synchronous CV rendering for worker process."""
    import json
    import tempfile
    from pathlib import Path

    from rendercv.schema import read_and_construct_model_from_yaml_string
    from rendercv.renderer import (
        create_typst_file,
        render_pdf_from_typst,
        render_pngs_from_pdf,
        create_markdown_file,
        create_html_from_markdown,
    )
    from rendercv.renderer.templater import process_model

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Write YAML content to temp file
        input_file = temp_path / "cv.yaml"
        input_file.write_text(yaml_content)

        # Parse overrides
        design_dict = json.loads(design_override) if design_override else None
        locale_dict = json.loads(locale_override) if locale_override else None

        # Build the model
        model = read_and_construct_model_from_yaml_string(
            yaml_content,
            design_dictionary=design_dict,
            locale_dictionary=locale_dict,
        )

        # Process model
        processed_model = process_model(model)

        # Generate output
        if output_format in ("pdf", "png"):
            typst_file = create_typst_file(processed_model, temp_path)
            pdf_file = render_pdf_from_typst(typst_file)

            if output_format == "pdf":
                return pdf_file.read_bytes()
            else:
                png_files = render_pngs_from_pdf(pdf_file)
                if png_files:
                    return png_files[0].read_bytes()
                raise ValueError("Failed to generate PNG")

        elif output_format in ("markdown", "md"):
            md_file = create_markdown_file(processed_model, temp_path)
            return md_file.read_bytes()

        elif output_format == "html":
            md_file = create_markdown_file(processed_model, temp_path)
            html_file = create_html_from_markdown(md_file)
            return html_file.read_bytes()

        else:
            raise ValueError(f"Unsupported format: {output_format}")


def start_worker(queues: list[str] | None = None):
    """Start an RQ worker process."""
    redis_conn = get_redis_connection()
    queues = queues or ["render", "default"]

    worker = Worker(
        queues=[Queue(q, connection=redis_conn) for q in queues],
        connection=redis_conn,
    )
    worker.work()


def get_job_info(job_id: str) -> dict | None:
    """Get info about an RQ job."""
    try:
        job = Job.fetch(job_id, connection=get_redis_connection())
        return {
            "id": job.id,
            "status": job.get_status(),
            "result": job.result,
            "started_at": job.started_at,
            "ended_at": job.ended_at,
            "exc_info": job.exc_info,
        }
    except Exception:
        return None


def cancel_job(job_id: str) -> bool:
    """Cancel a pending job."""
    try:
        job = Job.fetch(job_id, connection=get_redis_connection())
        job.cancel()
        return True
    except Exception:
        return False


def get_queue_stats() -> dict:
    """Get statistics about task queues."""
    redis_conn = get_redis_connection()

    queues = {
        "default": Queue("default", connection=redis_conn),
        "render": Queue("render", connection=redis_conn),
    }

    stats = {}
    for name, queue in queues.items():
        stats[name] = {
            "jobs_pending": len(queue),
            "jobs_started": queue.started_job_registry.count,
            "jobs_finished": queue.finished_job_registry.count,
            "jobs_failed": queue.failed_job_registry.count,
        }

    return stats
