"""Audit log. Items never include before_value or after_value."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from fcc_api.api.deps import get_db
from fcc_api.schemas.audit import AuditListResponse
from fcc_api.schemas.rules import error_response, request_id_of
from fcc_api.services.audit_export import AuditValuesOut, get_audit_values
from fcc_api.services.case_write import get_actor
from fcc_api.services.queries import list_audit_events

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=AuditListResponse)
def get_audit(
    request: Request,
    action: str | None = Query(None),
    caseId: str | None = Query(None),
    actorId: str | None = Query(None),
    limit: int = Query(200),
    cursor: str | None = Query(None),
    session: Any = Depends(get_db),
    actor: Any = Depends(get_actor),
) -> Any:
    try:
        return list_audit_events(
            session,
            actor,
            action=action,
            case_id=caseId,
            actor_id=actorId,
            limit=limit,
            cursor=cursor,
        )
    except Exception as exc:
        rendered = error_response(exc, request_id_of(request))
        if rendered is not None:
            session.rollback()
            return rendered
        raise


@router.get("/{eventId}/values", response_model=AuditValuesOut)
def get_audit_event_values(
    eventId: str,
    request: Request,
    session: Any = Depends(get_db),
    actor: Any = Depends(get_actor),
) -> Any:
    """前后值只出现在响应里。这里不记录响应体。"""

    try:
        return get_audit_values(session, actor, eventId, request_id_of(request))
    except Exception as exc:
        rendered = error_response(exc, request_id_of(request))
        if rendered is not None:
            return rendered
        raise
