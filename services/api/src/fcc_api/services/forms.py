"""Fill a PDF template and register it through the existing document upload flow."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from fcc_api.adapters.forms import get_form_filler
from fcc_api.auth.actor import Actor, fail_auth
from fcc_api.auth.policy import case_visible
from fcc_api.db.models.cases import Case
from fcc_api.db.models.parties import Party
from fcc_api.errors import ApiError, gate_failed
from fcc_api.schemas.documents import CompleteUploadsIn, CreateUploadsIn, UploadFileIn

_FORM_ROLES = frozenset({"ADVISOR", "OPERATIONS", "ADMIN"})
_WRITABLE = frozenset({"BUILDING", "DOCS_REQUESTED", "RETURNED"})
_REGISTRATION_MESSAGE = "Document registration is not available."


def fill_form(
    session: Session,
    actor: Actor,
    case_id: str,
    template_id: str,
    version: int,
    *,
    request_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    case = _visible_case(session, actor, case_id, request_id=request_id)
    _authorize_form(actor, case, case_id=case_id, request_id=request_id)
    parties = session.scalars(select(Party).where(Party.case_id == case_id).order_by(Party.position, Party.id)).all()
    filled = get_form_filler().fill(
        case_id=case_id,
        template_id=template_id,
        values=_source_values(case, parties),
    )
    registrar = registration_functions()
    if registrar is None:
        raise gate_failed(_REGISTRATION_MESSAGE, {"gate": "REGISTRATION_UNAVAILABLE"})
    create_slots, receive_upload, complete = registrar
    created = create_slots(
        session,
        actor,
        case_id,
        CreateUploadsIn(
            requirement_id=filled.requirement_id,
            files=[
                UploadFileIn(
                    file_name=filled.file_name,
                    size_bytes=len(filled.pdf_bytes),
                    mime_type="application/pdf",
                )
            ],
        ),
        request_id=request_id,
    )
    slot = created.slots[0]
    _receive(receive_upload, session, slot.url, filled.pdf_bytes)
    complete(
        session,
        actor,
        case_id,
        CompleteUploadsIn(version=version, batch_id=created.batch_id, upload_ids=[slot.upload_id]),
        request_id=request_id,
        correlation_id=correlation_id,
    )
    document_id = session.execute(
        select(document_model().id).where(
            document_model().case_id == case_id,
            document_model().upload_slot_id == slot.upload_id,
        )
    ).scalar_one()
    return {"documentId": document_id, "blanks": list(filled.blanks)}


def registration_functions() -> tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any]] | None:
    """Return the existing upload registration callables, or None when they are absent."""

    module = importlib.import_module("fcc_api.services.documents")
    create_slots = getattr(module, "create_upload_slots", None)
    receive_upload = getattr(module, "receive_local_upload", None)
    complete = getattr(module, "complete_uploads", None)
    if not all(callable(item) for item in (create_slots, receive_upload, complete)):
        return None
    return create_slots, receive_upload, complete


def _receive(receive_upload: Callable[..., Any], session: Session, url: str, payload: bytes) -> None:
    query = parse_qs(urlsplit(url).query)
    try:
        expires = int(query["expires"][0])
        signature = query["sig"][0]
        upload_id = urlsplit(url).path.rstrip("/").split("/")[-1]
    except (KeyError, IndexError, ValueError) as exc:
        raise gate_failed(_REGISTRATION_MESSAGE, {"gate": "REGISTRATION_UNAVAILABLE"}) from exc

    async def chunks() -> AsyncIterator[bytes]:
        yield payload

    asyncio.run(
        receive_upload(
            session,
            upload_id=upload_id,
            expires=expires,
            signature=signature,
            stream=chunks(),
        )
    )


def _source_values(case: Case, parties: list[Party]) -> dict[str, str]:
    return {
        "case.legalName": _text(case.legal_name),
        "case.reference": _text(case.reference),
        "case.province": _text(case.province),
        "case.entityType": _text(case.entity_type),
        "party.legalName": "; ".join(_text(row.legal_name) for row in parties if _text(row.legal_name)),
        "party.ownershipPercent": "; ".join(_percent(row.ownership_percent) for row in parties),
    }


def _visible_case(session: Session, actor: Actor, case_id: str, *, request_id: str) -> Case:
    case = session.get(Case, case_id)
    if case is None or not case_visible(actor, case):
        fail_auth(
            status_code=404,
            code="NOT_FOUND",
            message="Case not found.",
            details={"resource": "case", "id": case_id},
            reason="NOT_FOUND",
            case_id=case_id,
            user_id=actor.id,
            role=actor.role.value,
            request_id=request_id,
        )
    return case


def _authorize_form(actor: Actor, case: Case, *, case_id: str, request_id: str) -> None:
    if actor.role.value not in _FORM_ROLES:
        fail_auth(
            status_code=403,
            code="FORBIDDEN",
            message="Your role cannot perform this operation.",
            details={"reason": "ROLE", "role": actor.role.value, "operation": "fillForm"},
            reason="ROLE",
            operation="fillForm",
            case_id=case_id,
            user_id=actor.id,
            role=actor.role.value,
            request_id=request_id,
        )
    if case.status not in _WRITABLE:
        shown = str(case.status)
        message = (
            "This case is APPROVED and can no longer be edited."
            if shown == "APPROVED"
            else f"This case is {shown} and can no longer be edited by your role."
        )
        fail_auth(
            status_code=403,
            code="FORBIDDEN",
            message=message,
            details={"reason": "CASE_STATUS", "status": shown, "operation": "fillForm"},
            reason="CASE_STATUS",
            operation="fillForm",
            case_id=case_id,
            user_id=actor.id,
            role=actor.role.value,
            request_id=request_id,
        )


def document_model() -> Any:
    module = importlib.import_module("fcc_api.db.models.documents")
    model = getattr(module, "CaseDocument", None)
    if model is None:
        raise ApiError(422, "GATE_FAILED", _REGISTRATION_MESSAGE, {"gate": "REGISTRATION_UNAVAILABLE"})
    return model


def _text(value: object) -> str:
    return str(value or "").strip()


def _percent(value: Decimal | int | float | None) -> str:
    if value is None:
        return ""
    number = Decimal(str(value))
    if number == number.to_integral():
        return str(int(number))
    rendered = format(number.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
