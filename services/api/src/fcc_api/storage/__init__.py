"""Select the object storage implementation from settings. S3 is not contacted."""

from __future__ import annotations

from fcc_api.storage.base import ObjectStat, ObjectStorage, PayloadTooLarge, PresignedGet, PresignedPut
from fcc_api.storage.local import LocalStorage, local_signature_ok, resolve_storage_root, sign_local_request
from fcc_api.storage.s3 import S3Storage


def get_storage(settings: object | None = None) -> LocalStorage | S3Storage:
    if settings is None:
        from fcc_api.config import get_settings

        settings = get_settings()
    backend = str(getattr(settings, "storage_backend", "local") or "local").lower()
    if backend == "s3":
        return S3Storage(settings)
    return LocalStorage(settings)


__all__ = [
    "LocalStorage",
    "ObjectStat",
    "ObjectStorage",
    "PayloadTooLarge",
    "PresignedGet",
    "PresignedPut",
    "S3Storage",
    "get_storage",
    "local_signature_ok",
    "resolve_storage_root",
    "sign_local_request",
]
