"""清单状态与自定义要求。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.schemas.cases import CaseDetailResponse, ChecklistStatusIn, CustomRequirementIn
from fcc_api.services.case_write import get_actor, request_ids
from fcc_api.services.checklist import add_custom_requirement, set_checklist_status

router = APIRouter(prefix="/api/v1", tags=["checklist"])


@router.put("/cases/{caseId}/checklist/{requirementId}", response_model=CaseDetailResponse)
def put_checklist_status(
    caseId: str,
    requirementId: str,
    body: ChecklistStatusIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return set_checklist_status(
        session,
        actor,
        caseId,
        requirementId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.post("/cases/{caseId}/custom-requirements", status_code=201, response_model=CaseDetailResponse)
def post_custom_requirement(
    caseId: str,
    body: CustomRequirementIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return add_custom_requirement(
        session,
        actor,
        caseId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )
