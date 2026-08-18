"""S3-compatible storage service (Cloudflare R2, AWS S3, MinIO, ...).

Uses the S3_* settings already defined in the .env:
- S3_ENDPOINT_URL
- S3_ACCESS_KEY
- S3_SECRET_KEY
- S3_BUCKET_NAME
- S3_REGION
"""

import uuid
from functools import lru_cache
from typing import BinaryIO

import boto3
from botocore.client import Config

from app.core.config import settings


class StorageError(Exception):
    pass


@lru_cache(maxsize=1)
def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL.strip(),
        aws_access_key_id=settings.S3_ACCESS_KEY.strip(),
        aws_secret_access_key=settings.S3_SECRET_KEY.strip(),
        region_name=(settings.S3_REGION or "auto").strip(),
        config=Config(signature_version="s3v4"),
    )


def build_storage_key(user_id: uuid.UUID, filename: str) -> str:
    """Generate a unique, collision-free storage key for an uploaded file."""
    suffix = uuid.uuid4().hex[:12]
    safe_filename = filename.replace(" ", "_")
    return f"users/{user_id}/media/{suffix}_{safe_filename}"


def upload_file(file_obj: BinaryIO, storage_key: str, content_type: str) -> None:
    """Upload a file-like object to the bucket under storage_key."""
    client = get_s3_client()
    try:
        client.upload_fileobj(
            file_obj,
            settings.S3_BUCKET_NAME,
            storage_key,
            ExtraArgs={"ContentType": content_type},
        )
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"Failed to upload file: {exc}") from exc


def delete_file(storage_key: str) -> None:
    client = get_s3_client()
    try:
        client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=storage_key)
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"Failed to delete file: {exc}") from exc


def generate_presigned_url(storage_key: str, expires_in: int = 900) -> str:
    """Generate a temporary public URL for a stored object.

    Default expiry is 15 minutes — long enough for a third party (e.g. Meta's
    Graph API) to fetch the file right after we hand out the URL, without
    leaving the bucket permanently public.
    """
    client = get_s3_client()
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_BUCKET_NAME, "Key": storage_key},
            ExpiresIn=expires_in,
        )
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"Failed to generate presigned URL: {exc}") from exc


def get_public_url(storage_key: str) -> str:
    """Build a permanent public URL for a stored object.

    Only valid if the bucket (or the configured S3_PUBLIC_BASE_URL) actually
    serves objects publicly — e.g. a Cloudflare R2 bucket with a custom
    domain or the r2.dev public access URL enabled. If your bucket is
    private, use generate_presigned_url() instead.
    """
    base_url = getattr(settings, "S3_PUBLIC_BASE_URL", "") or settings.S3_ENDPOINT_URL
    if not base_url:
        raise StorageError("No public base URL configured for storage")
    return f"{base_url.rstrip('/')}/{settings.S3_BUCKET_NAME}/{storage_key}"


def file_exists(storage_key: str) -> bool:
    client = get_s3_client()
    try:
        client.head_object(Bucket=settings.S3_BUCKET_NAME, Key=storage_key)
        return True
    except client.exceptions.ClientError:
        return False
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"Failed to check file existence: {exc}") from exc