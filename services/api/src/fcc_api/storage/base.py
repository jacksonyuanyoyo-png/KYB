"""Object storage protocol. Bytes stay on disk or in object storage, never in PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class PresignedPut:
    method: str
    url: str
    headers: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class PresignedGet:
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class ObjectStat:
    size: int
    sha256: str
    header: bytes


class PayloadTooLarge(Exception):
    """Raised when a write exceeds the caller's byte limit. The partial file is removed."""


class ObjectStorage(Protocol):
    def presign_put(
        self,
        *,
        upload_id: str,
        object_key: str,
        mime_type: str,
        size_bytes: int,
        expires_at: datetime,
    ) -> PresignedPut: ...

    def presign_get(
        self,
        *,
        document_id: str,
        object_key: str,
        mime_type: str,
        file_name: str,
        expires_at: datetime,
    ) -> PresignedGet: ...

    def head(self, object_key: str, *, zone: str) -> int | None: ...

    def inspect(self, object_key: str, *, zone: str) -> ObjectStat | None: ...

    def open(self, object_key: str, *, zone: str): ...

    async def write_quarantine(
        self,
        object_key: str,
        stream: AsyncIterator[bytes],
        max_bytes: int,
    ) -> int: ...

    def promote(self, object_key: str) -> None: ...

    def delete_quarantine(self, object_key: str) -> None: ...

    def accepted_ready(self, object_key: str) -> bool: ...
