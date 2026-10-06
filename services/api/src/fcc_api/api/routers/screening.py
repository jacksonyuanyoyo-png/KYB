"""名单筛查与合规处置。处置路由不向抽取或语言模型开放。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.schemas.cases import CaseDetailResponse
from fcc_api.schemas.common import ApiModel
from fcc_api.services.case_write import get_actor, request_ids
from fcc_api.services.screening import record_disposition, record_screening

router = APIRouter(prefix="/api/v1", tags=["screening"])


class RecordScreeningIn(ApiModel):
    version: int = Field(ge=1)
    party_ids: list[str] = Field(min_length=1, max_length=500)


class ScreeningDispositionIn(ApiModel):
    version: int = Field(ge=1)
    disposition: Literal["MATCH_CONFIRMED", "FALSE_POSITIVE"]


@router.post("/cases/{caseId}/screening", response_model=CaseDetailResponse)
def post_screening(
    caseId: str,
    body: RecordScreeningIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return record_screening(
        session,
        actor,
        caseId,
        version=body.version,
        party_ids=body.party_ids,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.post("/cases/{caseId}/screening/{runId}/disposition", response_model=CaseDetailResponse)
def post_screening_disposition(
    caseId: str,
    runId: str,
    body: ScreeningDispositionIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return record_disposition(
        session,
        actor,
        caseId,
        runId,
        version=body.version,
        disposition=body.disposition,
        request_id=request_id,
        correlation_id=correlation_id,
    )
