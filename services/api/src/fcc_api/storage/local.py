"""Local filesystem storage with HMAC-signed upload and download URLs."""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

from fcc_api.storage.base import ObjectStat, PayloadTooLarge, PresignedGet, PresignedPut


def sign_local_request(secret: str, method: str, resource_id: str, expires: int) -> str:
    message = f"{method}\n{resource_id}\n{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def local_signature_ok(secret: str, method: str, resource_id: str, expires: int, signature: str) -> bool:
    if not secret or not signature:
        return False
    expected = sign_local_request(secret, method, resource_id, expires)
    candidate = signature.strip().lower()
    if len(expected) != len(candidate):
        return False
    return hmac.compare_digest(expected, candidate)


def resolve_storage_root(settings: object) -> Path:
    raw = Path(str(getattr(settings, "local_storage_root", "./.local-storage"))).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    return (Path.cwd() / raw).resolve()


def _api_base(settings: object) -> str:
    explicit = getattr(settings, "api_base_url", None) or getattr(settings, "public_base_url", None)
    if explicit:
        return str(explicit).rstrip("/")
    host = getattr(settings, "api_host", "127.0.0.1")
    port = getattr(settings, "api_port", 8000)
    return f"http://{host}:{port}"


class LocalStorage:
    def __init__(self, settings: object) -> None:
        self._settings = settings
        self.root = resolve_storage_root(settings)
        self._secret = str(getattr(settings, "local_url_signing_secret", "") or "")
        self._base = _api_base(settings)

    def presign_put(
        self,
        *,
        upload_id: str,
        object_key: str,
        mime_type: str,
        size_bytes: int,
        expires_at: datetime,
    ) -> PresignedPut:
        del object_key, size_bytes
        expires = int(expires_at.timestamp())
        signature = sign_local_request(self._require_secret(), "PUT", upload_id, expires)
        url = f"{self._base}/api/v1/local-storage/uploads/{upload_id}?expires={expires}&sig={signature}"
        return PresignedPut(
            method="PUT",
            url=url,
            headers={"Content-Type": mime_type},
            expires_at=expires_at,
        )

    def presign_get(
        self,
        *,
        document_id: str,
        object_key: str,
        mime_type: str,
        file_name: str,
        expires_at: datetime,
    ) -> PresignedGet:
        del object_key, mime_type, file_name
        expires = int(expires_at.timestamp())
        signature = sign_local_request(self._require_secret(), "GET", document_id, expires)
        url = f"{self._base}/api/v1/local-storage/documents/{document_id}?expires={expires}&sig={signature}"
        return PresignedGet(url=url, expires_at=expires_at)

    def head(self, object_key: str, *, zone: str) -> int | None:
        path = self._path(zone, object_key)
        if not path.is_file():
            return None
        return path.stat().st_size

    def inspect(self, object_key: str, *, zone: str) -> ObjectStat | None:
        path = self._path(zone, object_key)
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        size = 0
        header = b""
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                if len(header) < 16:
                    header += chunk[: 16 - len(header)]
                digest.update(chunk)
                size += len(chunk)
        return ObjectStat(size=size, sha256=digest.hexdigest(), header=header)

    def open(self, object_key: str, *, zone: str):
        return self._path(zone, object_key).open("rb")

    async def write_quarantine(self, object_key: str, stream: AsyncIterator[bytes], max_bytes: int) -> int:
        destination = self._path("quarantine", object_key)
        temporary = destination.with_name(destination.name + ".partial")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with temporary.open("wb") as handle:
                async for chunk in stream:
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise PayloadTooLarge(max_bytes)
                    handle.write(chunk)
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return written

    def promote(self, object_key: str) -> None:
        source = self._path("quarantine", object_key)
        target = self._path("accepted", object_key)
        if not source.is_file():
            raise FileNotFoundError(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)

    def delete_quarantine(self, object_key: str) -> None:
        self._path("quarantine", object_key).unlink(missing_ok=True)
        self._path("quarantine", object_key).with_name(
            self._path("quarantine", object_key).name + ".partial"
        ).unlink(missing_ok=True)

    def accepted_ready(self, object_key: str) -> bool:
        return self._path("accepted", object_key).is_file()

    def accepted_path(self, object_key: str) -> Path:
        return self._path("accepted", object_key)

    def _require_secret(self) -> str:
        if not self._secret:
            raise RuntimeError("LOCAL_URL_SIGNING_SECRET is empty")
        return self._secret

    def _path(self, zone: str, object_key: str) -> Path:
        if zone not in {"quarantine", "accepted"}:
            raise ValueError("invalid storage zone")
        parts = Path(object_key).parts
        if not object_key or object_key.startswith(("/", "\\")) or ".." in parts:
            raise ValueError("invalid object key")
        root = (self.root / zone).resolve()
        path = (root / object_key).resolve()
        if path != root and root not in path.parents:
            raise ValueError("invalid object key")
        return path


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
