"""Document routes: upload slots, registration, assignment, preview, and extraction."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor
from fcc_api.auth.dependencies import get_actor
from fcc_api.services.case_write import request_ids
from fcc_api.schemas.documents import (
    AssignDocumentIn,
    CompleteUploadsIn,
    ContentUrlOut,
    CreateUploadsIn,
    CreateUploadsOut,
    ExtractionOut,
)
from fcc_api.services.documents import (
    assign_document,
    complete_uploads,
    create_upload_slots,
    get_document_extraction,
    issue_content_url,
)

router = APIRouter(prefix="/api/v1", tags=["documents"])


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    if value:
        return str(value)
    header = request.headers.get("x-request-id")
    return header or None


@router.post("/cases/{caseId}/uploads", status_code=201, response_model=CreateUploadsOut)
def create_uploads(
    caseId: str,
    body: CreateUploadsIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_db),
) -> CreateUploadsOut:
    request_id, _correlation_id = request_ids(request)
    return create_upload_slots(session, actor, caseId, body, request_id=request_id)


@router.post("/cases/{caseId}/documents", status_code=201)
def register_documents(
    caseId: str,
    body: CompleteUploadsIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_db),
):
    request_id, correlation_id = request_ids(request)
    return complete_uploads(
        session,
        actor,
        caseId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.put("/cases/{caseId}/documents/{documentId}/requirement")
def put_document_requirement(
    caseId: str,
    documentId: str,
    body: AssignDocumentIn,
    request: Request,
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_db),
):
    request_id, correlation_id = request_ids(request)
    return assign_document(
        session,
        actor,
        caseId,
        documentId,
        body,
        request_id=request_id,
        correlation_id=correlation_id,
    )


@router.get(
    "/cases/{caseId}/documents/{documentId}/content-url",
    response_model=ContentUrlOut,
)
def document_content_url(
    caseId: str,
    documentId: str,
    request: Request,
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_db),
) -> ContentUrlOut:
    return issue_content_url(session, actor, caseId, documentId, request_id=_request_id(request))


@router.get(
    "/cases/{caseId}/documents/{documentId}/extraction",
    response_model=ExtractionOut,
    response_model_exclude_none=True,
)
def document_extraction(
    caseId: str,
    documentId: str,
    include: str | None = None,
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_db),
) -> ExtractionOut:
    return get_document_extraction(
        session,
        actor,
        caseId,
        documentId,
        include_pages=include == "pages",
    )


@router.get("/documents")
def document_ledger(
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_db),
    filter_name: str = Query(default="ALL", alias="filter"),
):
    from fcc_api.services.queries import list_documents

    return list_documents(session, actor, filter=filter_name)
