"""uDirect / uniFide submission and the vendor HMAC callback."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.auth.dependencies import get_actor
from fcc_api.schemas.common import ApiModel
from fcc_api.services.case_write import request_ids
from fcc_api.services.submissions import apply_callback, submit_case

router = APIRouter(prefix="/api/v1", tags=["submissions"])


class SubmitCaseIn(ApiModel):
    version: int = Field(ge=1)
    target: Literal["UDIRECT", "UNIFIDE"]


@router.post("/cases/{caseId}/submissions")
def post_submission(
    caseId: str,
    body: SubmitCaseIn,
    request: Request,
    response: Response,
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    request_id, _correlation_id = request_ids(request)
    status_code, payload = submit_case(
        session,
        actor,
        caseId,
        body.version,
        body.target,
        request_id=request_id,
    )
    response.status_code = status_code
    return payload


@router.post("/submissions/callback")
async def post_submission_callback(
    request: Request,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    raw = await request.body()
    signature = request.headers.get("x-submission-signature")
    return apply_callback(session, raw, signature)
