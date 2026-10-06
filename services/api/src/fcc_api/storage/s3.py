"""S3 storage interface. This build does not open a client and never calls AWS."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from fcc_api.storage.base import ObjectStat, PresignedGet, PresignedPut

_UNAVAILABLE = "File storage is temporarily unavailable."


def _unavailable():
    from fcc_api.errors import ApiError

    try:
        return ApiError(503, "UNAVAILABLE", _UNAVAILABLE, {})
    except TypeError:
        return ApiError(status_code=503, code="UNAVAILABLE", message=_UNAVAILABLE, details={})


class S3Storage:
    """Presign, head, promote, and delete entry points.

    When the quarantine bucket, accepted bucket, KMS key, or region is missing,
    every method raises HTTP 503. Even when those settings are present, this
    module does not construct a network client.
    """

    def __init__(self, settings: object) -> None:
        self._settings = settings

    def presign_put(
        self,
        *,
        upload_id: str,
        object_key: str,
        mime_type: str,
        size_bytes: int,
        expires_at: datetime,
    ) -> PresignedPut:
        del upload_id, object_key, mime_type, size_bytes, expires_at
        raise self._reject()

    def presign_get(
        self,
        *,
        document_id: str,
        object_key: str,
        mime_type: str,
        file_name: str,
        expires_at: datetime,
    ) -> PresignedGet:
        del document_id, object_key, mime_type, file_name, expires_at
        raise self._reject()

    def head(self, object_key: str, *, zone: str) -> int | None:
        del object_key, zone
        raise self._reject()

    def inspect(self, object_key: str, *, zone: str) -> ObjectStat | None:
        del object_key, zone
        raise self._reject()

    def open(self, object_key: str, *, zone: str):
        del object_key, zone
        raise self._reject()

    async def write_quarantine(self, object_key: str, stream: AsyncIterator[bytes], max_bytes: int) -> int:
        del object_key, stream, max_bytes
        raise self._reject()

    def promote(self, object_key: str) -> None:
        del object_key
        raise self._reject()

    def delete_quarantine(self, object_key: str) -> None:
        del object_key
        raise self._reject()

    def accepted_ready(self, object_key: str) -> bool:
        del object_key
        raise self._reject()

    def _configured(self) -> bool:
        settings = self._settings
        required = (
            getattr(settings, "s3_bucket_quarantine", None),
            getattr(settings, "s3_bucket_accepted", None),
            getattr(settings, "s3_kms_key_id", None),
            getattr(settings, "aws_region", None),
        )
        return all(bool(value) for value in required)

    def _reject(self):
        if not self._configured():
            return _unavailable()
        return _unavailable()
