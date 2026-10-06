"""Downstream submission adapter. Unconfigured calls return 503 and write no row.

The http adapter does not open a network connection. Tests inject a fake with
``set_submitter`` when ``SUBMISSION_ADAPTER=http``.
"""

from __future__ import annotations

import os
from typing import Protocol

_UNAVAILABLE_MESSAGE = "Submission is not configured."


class Submitter(Protocol):
    name: str

    def submit(self, *, case_id: str, target: str, payload: dict[str, object]) -> str | None: ...


class UnconfiguredSubmitter:
    name = "unconfigured"

    def submit(self, *, case_id: str, target: str = "", payload: dict[str, object] | None = None) -> str | None:
        del case_id, target, payload
        from fcc_api.errors import ApiError

        message = _UNAVAILABLE_MESSAGE
        try:
            raise ApiError(503, "UNAVAILABLE", message, {})
        except TypeError:
            raise ApiError(status_code=503, code="UNAVAILABLE", message=message, details={}) from None


class HttpSubmitter:
    """Configured http adapter. It accepts the payload locally and does not call uDirect."""

    name = "http"

    def submit(self, *, case_id: str, target: str, payload: dict[str, object]) -> str | None:
        del case_id, target, payload
        return None


_submitter_override: Submitter | None = None


def set_submitter(submitter: Submitter | None) -> None:
    """Install a fake used only when the adapter mode is http."""

    global _submitter_override
    _submitter_override = submitter


def submission_adapter_name(settings: object | None = None) -> str:
    if "SUBMISSION_ADAPTER" in os.environ:
        raw = os.environ.get("SUBMISSION_ADAPTER", "")
    elif settings is not None and getattr(settings, "submission_adapter", None) is not None:
        raw = getattr(settings, "submission_adapter")
    else:
        raw = "unconfigured"
    text = str(raw or "").strip().lower()
    if text in {"", "unconfigured"}:
        return "unconfigured"
    return text


def callback_secret(settings: object | None = None) -> str:
    if "SUBMISSION_CALLBACK_SECRET" in os.environ:
        raw = os.environ.get("SUBMISSION_CALLBACK_SECRET", "")
    elif settings is not None:
        raw = getattr(settings, "submission_callback_secret", "")
    else:
        raw = ""
    return str(raw or "").strip()


def get_submitter(settings: object | None = None) -> Submitter:
    if submission_adapter_name(settings) == "http":
        if _submitter_override is not None:
            return _submitter_override
        return HttpSubmitter()
    return UnconfiguredSubmitter()
