"""案件上的 AI 建议。确认仍走已有的 accept / reject。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import Field, field_validator
from sqlalchemy.orm import Session

from fcc_api.ai import ensure_prompt_hashes
from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.schemas.common import ApiModel
from fcc_api.services.ai_suggestions import assistant, classify_entity, classify_name, extract_formation, pre_review
from fcc_api.services.case_write import get_actor, request_ids

router = APIRouter(prefix="/api/v1", tags=["ai"])


@router.post("/ai/classify-entity")
def post_classify_name(
    body: ClassifyEntityIn,
    session: Session = Depends(get_db),
    _actor: Actor = Depends(get_actor),
) -> dict:
    return classify_name(session, body.legal_name, body.notes)


class ExtractFormationIn(ApiModel):
    document_ids: list[str] = Field(min_length=1)


class ClassifyEntityIn(ApiModel):
    legal_name: str
    notes: str = ""

    @field_validator("legal_name")
    @classmethod
    def legal_name_length(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 300:
            raise ValueError("Legal name must be 1–300 characters.")
        return cleaned

    @field_validator("notes")
    @classmethod
    def notes_length(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) > 2000:
            raise ValueError("Notes must be at most 2000 characters.")
        return cleaned


class AssistantIn(ApiModel):
    message: str

    @field_validator("message")
    @classmethod
    def message_length(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 2000:
            raise ValueError("Message must be 1–2000 characters.")
        return cleaned


@router.post("/cases/{caseId}/ai/extract-formation")
def post_extract_formation(
    caseId: str,
    body: ExtractFormationIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict:
    request_id, correlation_id = request_ids(request)
    return extract_formation(
        session,
        actor,
        caseId,
        body.document_ids,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.post("/cases/{caseId}/ai/classify-entity")
def post_classify_entity(
    caseId: str,
    body: ClassifyEntityIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict:
    request_id, correlation_id = request_ids(request)
    return classify_entity(
        session,
        actor,
        caseId,
        legal_name=body.legal_name,
        notes=body.notes,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.post("/cases/{caseId}/ai/pre-review")
def post_pre_review(
    caseId: str,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict:
    request_id, correlation_id = request_ids(request)
    return pre_review(
        session,
        actor,
        caseId,
        request_id=request_id,
        correlation_id=correlation_id,
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _text_chunks(text: str, size: int = 24) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            space = text.rfind(" ", start + 1, end + 1)
            if space > start:
                end = space
        chunks.append(text[start:end])
        start = end
    return chunks


async def _assistant_events(payload: dict) -> AsyncIterator[str]:
    """先把可见回答拆成 delta，最后用 done 给出建议编号和草稿。"""

    for chunk in _text_chunks(str(payload.get("text") or "")):
        yield _sse("delta", {"text": chunk})
        await asyncio.sleep(0.02)
    yield _sse("done", payload)


@router.post("/cases/{caseId}/ai/assistant")
def post_assistant(
    caseId: str,
    body: AssistantIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> StreamingResponse:
    request_id, correlation_id = request_ids(request)
    payload = assistant(
        session,
        actor,
        caseId,
        body.message,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    return StreamingResponse(
        _assistant_events(payload),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


ensure_prompt_hashes()
