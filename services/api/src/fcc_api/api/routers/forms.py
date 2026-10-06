"""PDF form fill. Generated files are registered through the document upload flow."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.auth.dependencies import get_actor
from fcc_api.schemas.common import ApiModel
from fcc_api.services.case_write import request_ids
from fcc_api.services.forms import fill_form

router = APIRouter(prefix="/api/v1", tags=["forms"])


class FillFormIn(ApiModel):
    version: int = Field(ge=1)


@router.post("/cases/{caseId}/forms/{templateId}")
def post_form(
    caseId: str,
    templateId: str,
    body: FillFormIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    request_id, correlation_id = request_ids(request)
    return fill_form(
        session,
        actor,
        caseId,
        templateId,
        body.version,
        request_id=request_id,
        correlation_id=correlation_id,
    )
