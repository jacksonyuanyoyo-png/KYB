"""Entities and people visible to the caller."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from fcc_api.api.deps import get_db
from fcc_api.schemas.rules import error_response, request_id_of
from fcc_api.services.case_write import get_actor
from fcc_api.services.queries import EntityListResponse, list_entities

router = APIRouter(prefix="/api/v1/entities", tags=["entities"])


@router.get("", response_model=EntityListResponse)
def get_entities(
    request: Request,
    kind: str = Query("ALL"),
    q: str | None = Query(None),
    session: Any = Depends(get_db),
    actor: Any = Depends(get_actor),
) -> Any:
    try:
        return list_entities(session, actor, kind=kind, q=q)
    except Exception as exc:
        rendered = error_response(exc, request_id_of(request))
        if rendered is not None:
            session.rollback()
            return rendered
        raise
