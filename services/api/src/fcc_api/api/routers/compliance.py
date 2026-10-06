"""Compliance queue. Cases are ordered by submitted_at, then updated_at."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from fcc_api.api.deps import get_db
from fcc_api.schemas.rules import error_response, request_id_of
from fcc_api.services.case_write import get_actor
from fcc_api.services.queries import ComplianceQueueResponse, list_compliance_queue

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])


@router.get("/queue", response_model=ComplianceQueueResponse)
def get_queue(
    request: Request,
    status: str = Query("READY_FOR_COMPLIANCE"),
    session: Any = Depends(get_db),
    actor: Any = Depends(get_actor),
) -> Any:
    try:
        return list_compliance_queue(session, actor, status=status)
    except Exception as exc:
        rendered = error_response(exc, request_id_of(request))
        if rendered is not None:
            session.rollback()
            return rendered
        raise
