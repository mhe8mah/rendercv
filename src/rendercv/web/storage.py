"""
File storage service for RenderCV SaaS.

Supports local filesystem and S3-compatible storage backends.
"""

import hashlib
import shutil
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiofiles
import aiofiles.os

from rendercv.web.config import get_settings

settings = get_settings()


class StorageBackend(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    async def save(
        self,
        content: bytes,
        path: str,
        content_type: str | None = None,
    ) -> str:
        """Save content to storage and return the public URL."""
        pass

    @abstractmethod
    async def get(self, path: str) -> bytes | None:
        """Get content from storage."""
        pass

    @abstractmethod
    async def delete(self, path: str) -> bool:
        """Delete content from storage."""
        pass

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if a file exists."""
        pass

    @abstractmethod
    async def get_url(self, path: str, expires_in: int | None = None) -> str:
        """Get a URL for accessing the file."""
        pass

    @abstractmethod
    async def get_size(self, path: str) -> int | None:
        """Get file size in bytes."""
        pass


class LocalStorageBackend(StorageBackend):
    """Local filesystem storage backend."""

    def __init__(self, base_path: Path | None = None):
        self.base_path = base_path or settings.storage_dir
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_full_path(self, path: str) -> Path:
        """Get full filesystem path."""
        return self.base_path / path

    async def save(
        self,
        content: bytes,
        path: str,
        content_type: str | None = None,
    ) -> str:
        """Save content to local filesystem."""
        full_path = self._get_full_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(full_path, "wb") as f:
            await f.write(content)

        return path

    async def get(self, path: str) -> bytes | None:
        """Get content from local filesystem."""
        full_path = self._get_full_path(path)

        if not full_path.exists():
            return None

        async with aiofiles.open(full_path, "rb") as f:
            return await f.read()

    async def delete(self, path: str) -> bool:
        """Delete file from local filesystem."""
        full_path = self._get_full_path(path)

        if full_path.exists():
            await aiofiles.os.remove(full_path)
            return True
        return False

    async def exists(self, path: str) -> bool:
        """Check if file exists."""
        return self._get_full_path(path).exists()

    async def get_url(self, path: str, expires_in: int | None = None) -> str:
        """Get URL for local file (relative path for serving via API)."""
        return f"/api/v1/files/{path}"

    async def get_size(self, path: str) -> int | None:
        """Get file size in bytes."""
        full_path = self._get_full_path(path)
        if full_path.exists():
            stat = await aiofiles.os.stat(full_path)
            return stat.st_size
        return None

    async def cleanup_old_files(self, older_than_days: int = 30) -> int:
        """Delete files older than specified days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        deleted_count = 0

        for root, dirs, files in self.base_path.walk():
            for file in files:
                file_path = root / file
                stat = await aiofiles.os.stat(file_path)
                file_time = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

                if file_time < cutoff:
                    await aiofiles.os.remove(file_path)
                    deleted_count += 1

        return deleted_count


class S3StorageBackend(StorageBackend):
    """S3-compatible storage backend."""

    def __init__(self):
        try:
            import aioboto3
        except ImportError as e:
            raise ImportError(
                "S3 storage requires aioboto3. Install with: pip install aioboto3"
            ) from e

        self.session = aioboto3.Session()
        self.bucket = settings.s3_bucket
        self.region = settings.s3_region
        self.endpoint_url = settings.s3_endpoint_url

    def _get_client_config(self) -> dict:
        """Get S3 client configuration."""
        config = {
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": settings.s3_secret_key,
            "region_name": self.region,
        }
        if self.endpoint_url:
            config["endpoint_url"] = self.endpoint_url
        return config

    async def save(
        self,
        content: bytes,
        path: str,
        content_type: str | None = None,
    ) -> str:
        """Save content to S3."""
        async with self.session.client("s3", **self._get_client_config()) as s3:
            extra_args = {}
            if content_type:
                extra_args["ContentType"] = content_type

            await s3.put_object(
                Bucket=self.bucket,
                Key=path,
                Body=content,
                **extra_args,
            )
        return path

    async def get(self, path: str) -> bytes | None:
        """Get content from S3."""
        try:
            async with self.session.client("s3", **self._get_client_config()) as s3:
                response = await s3.get_object(Bucket=self.bucket, Key=path)
                return await response["Body"].read()
        except Exception:
            return None

    async def delete(self, path: str) -> bool:
        """Delete object from S3."""
        try:
            async with self.session.client("s3", **self._get_client_config()) as s3:
                await s3.delete_object(Bucket=self.bucket, Key=path)
            return True
        except Exception:
            return False

    async def exists(self, path: str) -> bool:
        """Check if object exists in S3."""
        try:
            async with self.session.client("s3", **self._get_client_config()) as s3:
                await s3.head_object(Bucket=self.bucket, Key=path)
            return True
        except Exception:
            return False

    async def get_url(self, path: str, expires_in: int | None = None) -> str:
        """Generate presigned URL for S3 object."""
        expires_in = expires_in or 3600  # Default 1 hour

        async with self.session.client("s3", **self._get_client_config()) as s3:
            return await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": path},
                ExpiresIn=expires_in,
            )

    async def get_size(self, path: str) -> int | None:
        """Get object size in bytes."""
        try:
            async with self.session.client("s3", **self._get_client_config()) as s3:
                response = await s3.head_object(Bucket=self.bucket, Key=path)
            return response.get("ContentLength")
        except Exception:
            return None


def get_storage_backend() -> StorageBackend:
    """Get the configured storage backend."""
    if settings.storage_backend == "s3":
        return S3StorageBackend()
    return LocalStorageBackend()


class FileService:
    """High-level file service for CV operations."""

    def __init__(self, backend: StorageBackend | None = None):
        self.backend = backend or get_storage_backend()

    def _generate_path(
        self,
        user_id: str,
        cv_id: str,
        job_id: str,
        filename: str,
    ) -> str:
        """Generate storage path for a file."""
        return f"renders/{user_id}/{cv_id}/{job_id}/{filename}"

    async def save_render_output(
        self,
        content: bytes,
        user_id: str,
        cv_id: str,
        job_id: str,
        output_format: str,
    ) -> tuple[str, str, int]:
        """Save render output and return (path, url, size)."""
        extension = output_format.lower()
        filename = f"cv.{extension}"

        content_types = {
            "pdf": "application/pdf",
            "png": "image/png",
            "html": "text/html",
            "markdown": "text/markdown",
            "md": "text/markdown",
        }
        content_type = content_types.get(extension, "application/octet-stream")

        path = self._generate_path(user_id, cv_id, job_id, filename)
        await self.backend.save(content, path, content_type)

        url = await self.backend.get_url(path)
        size = len(content)

        return path, url, size

    async def get_render_output(self, path: str) -> bytes | None:
        """Get render output content."""
        return await self.backend.get(path)

    async def delete_render_output(self, path: str) -> bool:
        """Delete render output."""
        return await self.backend.delete(path)

    async def get_user_storage_usage(self, user_id: str) -> int:
        """Calculate total storage used by a user in bytes."""
        if isinstance(self.backend, LocalStorageBackend):
            user_path = self.backend.base_path / "renders" / user_id
            if not user_path.exists():
                return 0

            total = 0
            for root, dirs, files in user_path.walk():
                for file in files:
                    stat = await aiofiles.os.stat(root / file)
                    total += stat.st_size
            return total

        # For S3, would need to list and sum object sizes
        return 0

    async def cleanup_user_files(self, user_id: str) -> int:
        """Delete all files for a user. Returns count deleted."""
        if isinstance(self.backend, LocalStorageBackend):
            user_path = self.backend.base_path / "renders" / user_id
            if user_path.exists():
                count = sum(1 for _ in user_path.rglob("*") if _.is_file())
                shutil.rmtree(user_path)
                return count
        return 0


# Singleton instance
_file_service: FileService | None = None


def get_file_service() -> FileService:
    """Get the file service singleton."""
    global _file_service
    if _file_service is None:
        _file_service = FileService()
    return _file_service
