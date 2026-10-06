"""建案、推进状态、合规决定。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.schemas.cases import CaseDetailResponse, ChangeStatusIn, ComplianceDecisionIn, CreateCaseIn
from fcc_api.services.case_write import get_actor, request_ids
from fcc_api.services.cases import change_status, compliance_decision, create_case
from fcc_api.services.queries import (
    AuditItemsResponse,
    CaseDetailResponse as QueryCaseDetail,
    CaseListResponse,
    get_case_detail,
    list_case_audit,
    list_cases,
)

router = APIRouter(prefix="/api/v1", tags=["cases"])


@router.get("/cases", response_model=CaseListResponse)
def get_cases(
    filter: str = Query("ALL"),
    entityType: str | None = None,
    q: str | None = None,
    limit: int = Query(200),
    cursor: str | None = None,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseListResponse:
    return list_cases(
        session,
        actor,
        filter=filter,
        entity_type=entityType,
        q=q,
        limit=limit,
        cursor=cursor,
    )


@router.get("/cases/{caseId}", response_model=QueryCaseDetail)
def get_case(
    caseId: str,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> QueryCaseDetail:
    return get_case_detail(session, actor, caseId)


@router.get("/cases/{caseId}/audit", response_model=AuditItemsResponse)
def get_case_audit(
    caseId: str,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> AuditItemsResponse:
    return list_case_audit(session, actor, caseId)


@router.post("/cases", status_code=201, response_model=CaseDetailResponse)
def post_case(
    body: CreateCaseIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    detail = create_case(
        session,
        actor,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    response.headers["Location"] = f"/api/v1/cases/{detail.case.id}"
    return detail


@router.post("/cases/{caseId}/status", response_model=CaseDetailResponse)
def post_status(
    caseId: str,
    body: ChangeStatusIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return change_status(
        session,
        actor,
        caseId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.post("/cases/{caseId}/compliance-decision", response_model=CaseDetailResponse)
def post_compliance_decision(
    caseId: str,
    body: ComplianceDecisionIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return compliance_decision(
        session,
        actor,
        caseId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )
