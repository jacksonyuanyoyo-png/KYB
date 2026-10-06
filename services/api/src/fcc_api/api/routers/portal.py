"""客户门户。会话令牌只在这些路由上读取，不充当员工身份头。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.errors import unauthenticated
from fcc_api.schemas.common import ApiModel
from fcc_api.schemas.documents import CreateUploadsIn, CreateUploadsOut
from fcc_api.services.case_write import get_actor, request_ids
from fcc_api.services.portal import (
    PortalPrincipal,
    create_portal_invite,
    load_portal_session,
    open_portal_session,
    portal_checklist,
    request_portal_upload,
)

router = APIRouter(prefix="/api/v1", tags=["portal"])


class PortalInviteIn(ApiModel):
    version: int
    email: str


class PortalInviteOut(ApiModel):
    invite_path: str
    expires_at: str


class PortalSessionIn(ApiModel):
    token: str


class PortalSessionOut(ApiModel):
    token: str
    expires_at: str


class PortalChecklistItem(ApiModel):
    id: str
    name: str
    status: str


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header:
        raise unauthenticated()
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise unauthenticated()
    return value.strip()


def require_portal_session(
    request: Request,
    session: Session = Depends(get_db),
) -> PortalPrincipal:
    return load_portal_session(session, _bearer(request))


@router.post("/cases/{caseId}/portal-invites", status_code=201, response_model=PortalInviteOut)
def post_portal_invite(
    caseId: str,
    body: PortalInviteIn,
    request: Request,
    session: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> PortalInviteOut:
    request_id, correlation_id = request_ids(request)
    created = create_portal_invite(
        session,
        actor,
        caseId,
        body.version,
        body.email,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    return PortalInviteOut(invite_path=created.invite_path, expires_at=created.expires_at)


@router.post("/portal/session", response_model=PortalSessionOut)
def post_portal_session(
    body: PortalSessionIn,
    session: Session = Depends(get_db),
) -> PortalSessionOut:
    opened = open_portal_session(session, body.token)
    return PortalSessionOut(token=opened.token, expires_at=opened.expires_at)


@router.get("/portal/checklist", response_model=list[PortalChecklistItem])
def get_portal_checklist(
    session: Session = Depends(get_db),
    principal: PortalPrincipal = Depends(require_portal_session),
) -> list[dict[str, str]]:
    return portal_checklist(session, principal)


@router.post("/portal/uploads", status_code=201, response_model=CreateUploadsOut)
def post_portal_uploads(
    body: CreateUploadsIn,
    request: Request,
    session: Session = Depends(get_db),
    principal: PortalPrincipal = Depends(require_portal_session),
) -> CreateUploadsOut:
    request_id, _correlation_id = request_ids(request)
    return request_portal_upload(session, principal, body, request_id=request_id)
