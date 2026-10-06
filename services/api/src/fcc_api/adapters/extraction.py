"""Extraction adapters. The noop extractor records EXTRACTED with an empty result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class PageText:
    page_no: int
    text: str


@dataclass(frozen=True)
class ExtractedField:
    group_key: str
    field_key: str
    value: str
    confidence: float
    page_no: int | None
    citation: str | None
    bbox: dict | None = None


@dataclass(frozen=True)
class ExtractionResult:
    pages: list[PageText]
    fields: list[ExtractedField]
    model: str | None
    error_code: str | None


class Extractor(Protocol):
    name: str

    def extract(self, *, document_id: str, object_reader: BinaryIO, mime_type: str) -> ExtractionResult: ...


class NoopExtractor:
    name = "noop"

    def extract(self, *, document_id: str, object_reader: BinaryIO, mime_type: str) -> ExtractionResult:
        if object_reader.closed or not document_id or not mime_type:
            return ExtractionResult(pages=[], fields=[], model=None, error_code="VENDOR_ERROR")
        return ExtractionResult(pages=[], fields=[], model=None, error_code=None)


def get_extractor(settings: object | None = None) -> Extractor:
    if settings is None:
        from fcc_api.config import get_settings

        settings = get_settings()
    adapter = str(getattr(settings, "extraction_adapter", "noop") or "noop")
    if adapter == "noop":
        return NoopExtractor()
    from fcc_api.errors import ApiError

    message = "Document extraction is temporarily unavailable."
    try:
        raise ApiError(503, "UNAVAILABLE", message, {})
    except TypeError:
        raise ApiError(status_code=503, code="UNAVAILABLE", message=message, details={}) from None
