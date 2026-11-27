"""
S3 / MinIO Dual-Zone Storage Service (Quarantine -> Secured Vault).

Architecture:
1. Untrusted user uploads ALWAYS land in `S3_QUARANTINE_BUCKET` (`vault-quarantine`).
2. Only after passing ClamAV antivirus + ML SQLi/Prompt-Injection scanning is the object
   promoted (copied + deleted from quarantine) into `S3_SECURED_BUCKET` (`vault-secured`).
3. User view/download endpoints ONLY read from `S3_SECURED_BUCKET`, making it impossible
   to serve unscanned, infected, or blocked files.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings

logger = logging.getLogger("vaultcity.s3")

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(filename: str) -> str:
    """Strip POSIX/Windows path components and unsafe characters while keeping extension intact."""
    normalized = (filename or "document.bin").strip().replace("\\", "/")
    base = os.path.basename(normalized)
    cleaned = _SAFE_FILENAME_RE.sub("_", base).strip("._")
    return cleaned[:180] or "document.bin"


class S3StorageService:
    """Async wrapper over boto3 S3 client supporting both local MinIO/Silo and AWS S3."""

    def __init__(self):
        self._client = None
        self._buckets_ready = False

    def _get_client(self):
        if self._client is None:
            endpoint_url = settings.MINIO_ENDPOINT_URL or None
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=settings.MINIO_ACCESS_KEY,
                aws_secret_access_key=settings.MINIO_SECRET_KEY,
                region_name=settings.MINIO_REGION,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    connect_timeout=5,
                    read_timeout=30,
                ),
            )
        return self._client

    def _ensure_buckets_sync(self) -> None:
        if self._buckets_ready:
            return
        client = self._get_client()
        for bucket in (settings.S3_QUARANTINE_BUCKET, settings.S3_SECURED_BUCKET):
            try:
                client.head_bucket(Bucket=bucket)
            except ClientError:
                try:
                    client.create_bucket(Bucket=bucket)
                    logger.info("Created S3 bucket: %s", bucket)
                except ClientError as exc:
                    code = exc.response.get("Error", {}).get("Code", "")
                    if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                        raise
        self._buckets_ready = True

    async def ensure_buckets(self) -> None:
        await asyncio.to_thread(self._ensure_buckets_sync)

    @staticmethod
    def build_quarantine_key(user_id: str, file_id: str, filename: str) -> str:
        safe = sanitize_filename(filename)
        return f"quarantine/{user_id}/{file_id}/{safe}"

    @staticmethod
    def build_secured_key(user_id: str, file_id: str, filename: str) -> str:
        safe = sanitize_filename(filename)
        return f"vault/{user_id}/{file_id}/{safe}"

    def _put_object_sync(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> None:
        self._ensure_buckets_sync()
        client = self._get_client()
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
            Metadata=metadata,
        )

    async def save_secured_file(
        self,
        *,
        secured_key: str,
        data: bytes,
        content_type: str = "text/markdown; charset=utf-8",
        metadata: Optional[dict[str, str]] = None,
    ) -> None:
        """Directly write verified/system files (such as user_memory.md) to the secured vault bucket."""
        meta = metadata or {"zone": "secured", "type": "system_profile"}
        await asyncio.to_thread(
            self._put_object_sync,
            settings.S3_SECURED_BUCKET,
            secured_key,
            data,
            content_type,
            meta,
        )

    async def upload_to_quarantine(
        self,
        *,
        user_id: str,
        file_id: str,
        filename: str,
        data: bytes,
        content_type: str,
        sha256_hex: str,
    ) -> str:
        """Stage untrusted file bytes into the isolated quarantine bucket."""
        key = self.build_quarantine_key(user_id, file_id, filename)
        await asyncio.to_thread(
            self._put_object_sync,
            settings.S3_QUARANTINE_BUCKET,
            key,
            data,
            content_type,
            {
                "user_id": str(user_id),
                "file_id": str(file_id),
                "sha256": sha256_hex,
                "zone": "quarantine",
            },
        )
        logger.info("Uploaded file_id=%s to s3://%s/%s", file_id, settings.S3_QUARANTINE_BUCKET, key)
        return key

    def _get_object_sync(self, bucket: str, key: str) -> Tuple[bytes, str]:
        self._ensure_buckets_sync()
        client = self._get_client()
        obj = client.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        content_type = obj.get("ContentType") or "application/octet-stream"
        return body, content_type

    async def download_from_quarantine(self, quarantine_key: str) -> bytes:
        body, _ = await asyncio.to_thread(
            self._get_object_sync, settings.S3_QUARANTINE_BUCKET, quarantine_key
        )
        return body

    async def download_from_secured(self, secured_key: str) -> Tuple[bytes, str]:
        """Download verified file bytes strictly from the secured vault bucket."""
        return await asyncio.to_thread(
            self._get_object_sync, settings.S3_SECURED_BUCKET, secured_key
        )

    def _promote_sync(
        self,
        quarantine_key: str,
        secured_key: str,
        content_type: str,
        metadata: dict[str, str],
    ) -> None:
        self._ensure_buckets_sync()
        client = self._get_client()
        # Copy verified object from quarantine into secured vault bucket
        client.copy_object(
            CopySource={"Bucket": settings.S3_QUARANTINE_BUCKET, "Key": quarantine_key},
            Bucket=settings.S3_SECURED_BUCKET,
            Key=secured_key,
            ContentType=content_type or "application/octet-stream",
            Metadata=metadata,
            MetadataDirective="REPLACE",
        )
        # Remove original from quarantine immediately after promotion
        try:
            client.delete_object(Bucket=settings.S3_QUARANTINE_BUCKET, Key=quarantine_key)
        except Exception as exc:
            logger.warning("Failed removing promoted quarantine object %s: %s", quarantine_key, exc)

    async def promote_to_secured(
        self,
        *,
        quarantine_key: str,
        user_id: str,
        file_id: str,
        filename: str,
        content_type: str,
        sha256_hex: str,
    ) -> str:
        """Promote a verified file from quarantine into the secured vault bucket."""
        secured_key = self.build_secured_key(user_id, file_id, filename)
        await asyncio.to_thread(
            self._promote_sync,
            quarantine_key,
            secured_key,
            content_type,
            {
                "user_id": str(user_id),
                "file_id": str(file_id),
                "sha256": sha256_hex,
                "scan_verified": "true",
                "zone": "secured",
            },
        )
        logger.info(
            "Promoted file_id=%s -> s3://%s/%s",
            file_id,
            settings.S3_SECURED_BUCKET,
            secured_key,
        )
        return secured_key

    def _delete_sync(self, bucket: str, key: str) -> None:
        try:
            self._ensure_buckets_sync()
            client = self._get_client()
            client.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            logger.warning("S3 delete_object failed for s3://%s/%s: %s", bucket, key, exc)

    async def delete_from_quarantine(self, quarantine_key: str) -> None:
        if quarantine_key:
            await asyncio.to_thread(self._delete_sync, settings.S3_QUARANTINE_BUCKET, quarantine_key)

    async def delete_from_secured(self, secured_key: str) -> None:
        if secured_key:
            await asyncio.to_thread(self._delete_sync, settings.S3_SECURED_BUCKET, secured_key)


s3_service = S3StorageService()
