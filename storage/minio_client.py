"""MinIO / S3-compatible object storage adapter."""
from __future__ import annotations

import io
from datetime import timedelta

from minio import Minio
from minio.error import S3Error


def build_minio_client(
    endpoint: str, access_key: str, secret_key: str, secure: bool, region: str | None = None,
) -> Minio:
    return Minio(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        region=region,
    )


def build_signing_client(storage) -> Minio | None:
    """Client that signs download links for the address *browsers* use.

    A presigned URL's signature covers the host, so the internal one
    (``minio:9000``, only resolvable inside Docker) cannot just be rewritten.
    With MINIO_PUBLIC_ENDPOINT set, links are signed for that host instead.
    The region is fixed so signing needs no network call (the public host is
    usually not reachable from inside the container).  None = not configured.
    """
    endpoint = (storage.public_endpoint or "").strip()
    if not endpoint:
        return None
    secure = storage.secure if storage.public_secure is None else storage.public_secure
    return build_minio_client(
        endpoint, storage.access_key, storage.secret_key.get_secret_value(), secure,
        region=storage.region,
    )


def ping(client: Minio, bucket: str) -> None:
    """Raise if MinIO is unreachable."""
    client.bucket_exists(bucket)


def ensure_bucket(client: Minio, bucket: str) -> None:
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    except S3Error as exc:
        raise RuntimeError(f"Cannot initialize bucket '{bucket}': {exc}") from exc


def upload_object(
    client: Minio,
    bucket: str,
    object_key: str,
    data: bytes,
    content_type: str,
) -> None:
    client.put_object(
        bucket_name=bucket,
        object_name=object_key,
        data=io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def get_presigned_url(
    client: Minio,
    bucket: str,
    object_key: str,
    expires_hours: int = 24,
) -> str:
    return client.presigned_get_object(
        bucket_name=bucket,
        object_name=object_key,
        expires=timedelta(hours=expires_hours),
    )


def download_object(client: Minio, bucket: str, object_key: str) -> bytes:
    response = client.get_object(bucket_name=bucket, object_name=object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
