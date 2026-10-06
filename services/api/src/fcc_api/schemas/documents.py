"""Request and response models for document upload, preview, and extraction."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


def format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class UploadFileIn(_CamelModel):
    file_name: str
    size_bytes: int
    mime_type: str


class CreateUploadsIn(_CamelModel):
    requirement_id: str | None = None
    files: list[UploadFileIn] = Field(min_length=1, max_length=20)


class UploadSlotOut(_CamelModel):
    upload_id: str
    file_name: str
    method: str
    url: str
    headers: dict[str, str]


class CreateUploadsOut(_CamelModel):
    batch_id: str
    expires_at: str
    slots: list[UploadSlotOut]


class CompleteUploadsIn(_CamelModel):
    version: int
    batch_id: str
    upload_ids: list[str] = Field(min_length=1, max_length=20)


class AssignDocumentIn(_CamelModel):
    version: int
    requirement_id: str


class ContentUrlOut(_CamelModel):
    url: str
    expires_at: str
    mime_type: str
    file_name: str


class ExtractionRunOut(_CamelModel):
    id: str
    status: str
    adapter: str
    model: str | None = None
    page_count: int | None = None
    started_at: str
    finished_at: str | None = None
    error_code: str | None = None


class ExtractionFieldOut(_CamelModel):
    group_key: str
    field_key: str
    value: str
    confidence: float
    page_no: int | None = None
    citation: str | None = None


class ExtractionPageOut(_CamelModel):
    page_no: int
    text: str


class ExtractionOut(_CamelModel):
    extraction: ExtractionRunOut | None
    fields: list[ExtractionFieldOut]
    pages: list[ExtractionPageOut] | None = None
