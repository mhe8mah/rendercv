"""
CV Rendering service for RenderCV SaaS.

Integrates with the core RenderCV rendering pipeline.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rendercv.web.config import get_settings
from rendercv.web.models import CV, RenderJob, RenderStatus, OutputFormat
from rendercv.web.storage import get_file_service

settings = get_settings()


class RenderService:
    """Service for rendering CVs to various output formats."""

    def __init__(self):
        self.file_service = get_file_service()

    async def create_render_job(
        self,
        db: AsyncSession,
        cv: CV,
        output_format: OutputFormat,
    ) -> RenderJob:
        """Create a new render job."""
        job = RenderJob(
            cv_id=cv.id,
            user_id=cv.user_id,
            output_format=output_format.value,
            status=RenderStatus.PENDING.value,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job

    async def process_render_job(
        self,
        db: AsyncSession,
        job_id: str,
    ) -> RenderJob:
        """Process a render job synchronously."""
        # Get the job
        result = await db.execute(
            select(RenderJob).where(RenderJob.id == job_id)
        )
        job = result.scalar_one_or_none()
        if not job:
            raise ValueError(f"Render job {job_id} not found")

        # Get the CV
        result = await db.execute(select(CV).where(CV.id == job.cv_id))
        cv = result.scalar_one_or_none()
        if not cv:
            job.status = RenderStatus.FAILED.value
            job.error_message = "CV not found"
            await db.commit()
            return job

        # Update status to processing
        job.status = RenderStatus.PROCESSING.value
        job.started_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            # Render the CV
            output_content = await self._render_cv(
                cv.yaml_content,
                cv.design_override,
                cv.locale_override,
                job.output_format,
            )

            # Save output to storage
            path, url, size = await self.file_service.save_render_output(
                output_content,
                cv.user_id,
                cv.id,
                job.id,
                job.output_format,
            )

            # Update job with success
            job.status = RenderStatus.COMPLETED.value
            job.output_path = path
            job.output_url = url
            job.file_size_bytes = size
            job.completed_at = datetime.now(timezone.utc)

        except Exception as e:
            # Update job with failure
            job.status = RenderStatus.FAILED.value
            job.error_message = str(e)
            job.completed_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(job)
        return job

    async def _render_cv(
        self,
        yaml_content: str,
        design_override: str | None,
        locale_override: str | None,
        output_format: str,
    ) -> bytes:
        """Render CV using the core RenderCV library."""
        import asyncio

        # Run synchronous rendering in thread pool
        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._render_cv_sync,
            yaml_content,
            design_override,
            locale_override,
            output_format,
        )

    def _render_cv_sync(
        self,
        yaml_content: str,
        design_override: str | None,
        locale_override: str | None,
        output_format: str,
    ) -> bytes:
        """Synchronous CV rendering."""
        import json

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

            # Parse design and locale overrides if provided
            design_dict = json.loads(design_override) if design_override else None
            locale_dict = json.loads(locale_override) if locale_override else None

            # Build the model
            model = read_and_construct_model_from_yaml_string(
                yaml_content,
                design_dictionary=design_dict,
                locale_dictionary=locale_dict,
            )

            # Process model for rendering
            processed_model = process_model(model)

            # Generate output based on format
            if output_format in ("pdf", "png"):
                # Create Typst file
                typst_file = create_typst_file(processed_model, temp_path)

                # Render PDF
                pdf_file = render_pdf_from_typst(typst_file)

                if output_format == "pdf":
                    return pdf_file.read_bytes()
                else:
                    # Render PNG from PDF
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
                raise ValueError(f"Unsupported output format: {output_format}")

    async def validate_yaml(self, yaml_content: str) -> tuple[bool, list[dict] | None]:
        """Validate CV YAML content.

        Returns (is_valid, errors)
        """
        import asyncio

        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._validate_yaml_sync,
            yaml_content,
        )

    def _validate_yaml_sync(self, yaml_content: str) -> tuple[bool, list[dict] | None]:
        """Synchronous YAML validation."""
        try:
            from rendercv.schema import read_and_construct_model_from_yaml_string

            read_and_construct_model_from_yaml_string(yaml_content)
            return True, None
        except Exception as e:
            error_info = {
                "message": str(e),
                "type": type(e).__name__,
            }
            return False, [error_info]

    async def get_job_status(
        self,
        db: AsyncSession,
        job_id: str,
    ) -> RenderJob | None:
        """Get render job by ID."""
        result = await db.execute(
            select(RenderJob).where(RenderJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def get_user_jobs(
        self,
        db: AsyncSession,
        user_id: str,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[RenderJob], int]:
        """Get paginated render jobs for a user."""
        # Get total count
        from sqlalchemy import func

        count_result = await db.execute(
            select(func.count(RenderJob.id)).where(RenderJob.user_id == user_id)
        )
        total = count_result.scalar() or 0

        # Get paginated results
        offset = (page - 1) * per_page
        result = await db.execute(
            select(RenderJob)
            .where(RenderJob.user_id == user_id)
            .order_by(RenderJob.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
        jobs = list(result.scalars().all())

        return jobs, total


# Singleton instance
_render_service: RenderService | None = None


def get_render_service() -> RenderService:
    """Get the render service singleton."""
    global _render_service
    if _render_service is None:
        _render_service = RenderService()
    return _render_service
