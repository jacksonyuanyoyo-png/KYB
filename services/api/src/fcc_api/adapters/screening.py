"""Name-screening adapter. The noop result does not block a case and is not a PEP determination.

The adapter returns only CLEAR, POTENTIAL_MATCH, or ERROR. It never returns a
“not a PEP” determination. ``reference`` is a vendor receipt token, not the raw
vendor payload. This module does not open a network connection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

ScreenStatus = Literal["CLEAR", "POTENTIAL_MATCH", "ERROR"]
_STATUSES = frozenset({"CLEAR", "POTENTIAL_MATCH", "ERROR"})
_RECEIPT = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


@dataclass(frozen=True)
class ScreeningHit:
    status: ScreenStatus
    reference: str


def normalize_hit(hit: ScreeningHit) -> ScreeningHit:
    """Keep a receipt token. Drop anything that looks like a vendor payload."""

    status: ScreenStatus = hit.status if hit.status in _STATUSES else "ERROR"
    reference = str(hit.reference).strip()
    if not _RECEIPT.fullmatch(reference):
        reference = "unavailable"
    return ScreeningHit(status=status, reference=reference)


class Screener(Protocol):
    name: str

    def screen(self, *, party_id: str, display_name: str) -> ScreeningHit: ...


class NoopScreener:
    name = "noop"

    def screen(self, *, party_id: str, display_name: str) -> ScreeningHit:
        del display_name
        if not party_id:
            return ScreeningHit(status="ERROR", reference="noop")
        return ScreeningHit(status="CLEAR", reference="noop")


def get_screener(settings: object | None = None) -> Screener:
    if settings is None:
        from fcc_api.config import get_settings

        settings = get_settings()
    adapter = str(getattr(settings, "screening_adapter", "noop") or "noop")
    if adapter == "noop":
        return NoopScreener()
    from fcc_api.errors import ApiError

    message = "Name screening is temporarily unavailable."
    try:
        raise ApiError(503, "UNAVAILABLE", message, {})
    except TypeError:
        raise ApiError(status_code=503, code="UNAVAILABLE", message=message, details={}) from None
