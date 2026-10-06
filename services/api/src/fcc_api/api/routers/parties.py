"""股权更新与 AI 建议的接受、拒绝。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.schemas.cases import CaseDetailResponse
from fcc_api.schemas.parties import ApplyAiPartiesIn, RejectAiIn, UpdatePartiesIn
from fcc_api.services.case_write import get_actor, request_ids
from fcc_api.services.parties import apply_ai_parties, record_ai_rejection, update_parties

router = APIRouter(prefix="/api/v1", tags=["parties"])


@router.put("/cases/{caseId}/parties", response_model=CaseDetailResponse)
def put_parties(
    caseId: str,
    body: UpdatePartiesIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return update_parties(
        session,
        actor,
        caseId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.post("/cases/{caseId}/ai-suggestions/accept", response_model=CaseDetailResponse)
def post_accept_ai(
    caseId: str,
    body: ApplyAiPartiesIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return apply_ai_parties(
        session,
        actor,
        caseId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.post("/cases/{caseId}/ai-suggestions/reject", response_model=CaseDetailResponse)
def post_reject_ai(
    caseId: str,
    body: RejectAiIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return record_ai_rejection(
        session,
        actor,
        caseId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )
