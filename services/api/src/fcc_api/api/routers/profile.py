"""账户详情写入。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.schemas.cases import CaseDetailResponse, UpdateProfileIn
from fcc_api.services.case_write import get_actor, request_ids
from fcc_api.services.profile import update_profile

router = APIRouter(prefix="/api/v1", tags=["profile"])


@router.put("/cases/{caseId}/profile", response_model=CaseDetailResponse)
def put_profile(
    caseId: str,
    body: UpdateProfileIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CaseDetailResponse:
    request_id, correlation_id = request_ids(request)
    return update_profile(
        session,
        actor,
        caseId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )
