"""Scanner adapters. The noop scanner records CLEAN for the current registration."""

from __future__ import annotations

from typing import BinaryIO, Literal, Protocol

ScanStatus = Literal["CLEAN", "INFECTED", "ERROR"]


class Scanner(Protocol):
    name: str

    def scan(self, *, object_reader: BinaryIO, sha256: str) -> ScanStatus: ...


class NoopScanner:
    name = "noop"

    def scan(self, *, object_reader: BinaryIO, sha256: str) -> ScanStatus:
        if object_reader.closed or len(sha256) != 64:
            return "ERROR"
        return "CLEAN"


def get_scanner(settings: object | None = None) -> Scanner:
    if settings is None:
        from fcc_api.config import get_settings

        settings = get_settings()
    adapter = str(getattr(settings, "scan_adapter", "noop") or "noop")
    if adapter == "noop":
        return NoopScanner()
    from fcc_api.errors import ApiError

    message = "Malware scanning is temporarily unavailable."
    try:
        raise ApiError(503, "UNAVAILABLE", message, {})
    except TypeError:
        raise ApiError(status_code=503, code="UNAVAILABLE", message=message, details={}) from None
