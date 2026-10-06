"""Content disarm adapter. The noop implementation returns the original object key."""

from __future__ import annotations

from typing import Protocol


class ContentDisarmer(Protocol):
    name: str

    def disarm(self, *, bucket: str, key: str, sha256: str) -> str: ...


class NoopCdr:
    name = "noop"

    def disarm(self, *, bucket: str, key: str, sha256: str) -> str:
        del bucket
        if len(sha256) != 64 or not key:
            raise ValueError("invalid object")
        return key


def get_cdr(settings: object | None = None) -> ContentDisarmer:
    if settings is None:
        from fcc_api.config import get_settings

        settings = get_settings()
    adapter = str(getattr(settings, "cdr_adapter", "noop") or "noop")
    if adapter == "noop":
        return NoopCdr()
    from fcc_api.errors import ApiError

    message = "Content disarm is temporarily unavailable."
    try:
        raise ApiError(503, "UNAVAILABLE", message, {})
    except TypeError:
        raise ApiError(status_code=503, code="UNAVAILABLE", message=message, details={}) from None
