"""法律保全的建立与释放。仅 COMPLIANCE 与 ADMIN。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import Field, field_validator
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.schemas.rules import ApiModel, error_response, request_id_of
from fcc_api.services.case_write import get_actor
from fcc_api.services.legal_holds import LegalHoldOut, create_legal_hold, release_legal_hold

router = APIRouter(prefix="/api/v1/legal-holds", tags=["legal-holds"])


class LegalHoldIn(ApiModel):
    scope: str
    target_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("scope")
    @classmethod
    def scope_choice(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if cleaned not in {"CASE", "DOCUMENT"}:
            raise ValueError("scope must be CASE or DOCUMENT.")
        return cleaned

    @field_validator("reason")
    @classmethod
    def reason_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not 1 <= len(cleaned) <= 500:
            raise ValueError("Reason must be 1–500 characters.")
        return cleaned


@router.post("", status_code=201, response_model=LegalHoldOut)
def post_legal_hold(
    request: Request,
    body: LegalHoldIn,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> Any:
    try:
        return create_legal_hold(
            session,
            actor,
            scope=body.scope,
            target_id=body.target_id,
            reason=body.reason,
            request_id=request_id_of(request),
        )
    except Exception as exc:
        rendered = error_response(exc, request_id_of(request))
        if rendered is not None:
            session.rollback()
            return rendered
        raise


@router.post("/{holdId}/release", response_model=LegalHoldOut)
def post_release_legal_hold(
    holdId: str,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> Any:
    try:
        return release_legal_hold(
            session,
            actor,
            holdId,
            request_id=request_id_of(request),
        )
    except Exception as exc:
        rendered = error_response(exc, request_id_of(request))
        if rendered is not None:
            session.rollback()
            return rendered
        raise
