"""股权差量更新、接受或拒绝 AI 建议。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fcc_api.auth.actor import Actor
from fcc_api.config import get_settings
from fcc_api.db.models.cases import Case
from fcc_api.db.models.parties import Party
from fcc_api.errors import ApiError
from fcc_api.rules.constants import ENTITY_TYPES, US_TAX_CLASSES
from fcc_api.schemas.cases import CaseDetailResponse
from fcc_api.schemas.common import js_number
from fcc_api.schemas.parties import ApplyAiPartiesIn, PartyIn, RejectAiIn, UpdatePartiesIn
from fcc_api.services.case_write import case_write
from fcc_api.services.cases import load_case_detail

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_QUANT = Decimal("0.0001")


def update_parties(
    session: Session,
    actor: Actor,
    case_id: str,
    body: UpdatePartiesIn,
    *,
    request_id: str,
    correlation_id: str,
) -> CaseDetailResponse:
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="updateParties",
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        before, after = _write_parties(session, writer.case, body.parties)
        writer.audit(
            action="OWNERSHIP_UPDATED",
            summary=body.summary.strip(),
            changes=_changes(body.changes),
            before={"parties": before},
            after={"parties": after},
        )
        writer.seal()
        detail = load_case_detail(session, case_id)
    return detail


def apply_ai_parties(
    session: Session,
    actor: Actor,
    case_id: str,
    body: ApplyAiPartiesIn,
    *,
    request_id: str,
    correlation_id: str,
) -> CaseDetailResponse:
    _known_model(body.model)
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="applyAiParties",
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        before, after = _write_parties(session, writer.case, body.parties)
        writer.audit(
            action="AI_SUGGESTION",
            summary=body.summary.strip(),
            before={"parties": before},
            after={"parties": after},
            ai_model=body.model,
            ai_accepted=True,
        )
        writer.seal()
        detail = load_case_detail(session, case_id)
    return detail


def record_ai_rejection(
    session: Session,
    actor: Actor,
    case_id: str,
    body: RejectAiIn,
    *,
    request_id: str,
    correlation_id: str,
) -> CaseDetailResponse:
    _known_model(body.model)
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="recordAiRejection",
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        writer.audit(
            action="AI_SUGGESTION",
            summary=body.summary.strip(),
            ai_model=body.model,
            ai_accepted=False,
        )
        writer.seal()
        detail = load_case_detail(session, case_id)
    return detail


def _write_parties(session: Session, case: Case, parties: list[PartyIn]) -> tuple[list[dict], list[dict]]:
    existing = list(
        session.scalars(select(Party).where(Party.case_id == case.id).order_by(Party.position))
    )
    by_id = {row.id: row for row in existing}
    _validate_tree(case, parties, by_id)
    incoming = {item.id: item for item in parties}
    before: list[dict] = []
    after: list[dict] = []
    for index, item in enumerate(parties):
        row = by_id.get(item.id)
        if row is None:
            created = _new_row(case.id, item, index)
            session.add(created)
            after.append(_payload_dict(item))
            continue
        if _changed(row, item, index):
            before.append(_row_dict(row))
            _assign(row, item, index)
            after.append(_payload_dict(item))
        elif row.position != index:
            row.position = index
    session.flush()
    for row in existing:
        if row.id not in incoming:
            before.append(_row_dict(row))
            session.delete(row)
    session.flush()
    return before, after


def _validate_tree(case: Case, parties: list[PartyIn], existing: dict[str, Party]) -> None:
    if len(parties) > 500:
        raise _invalid([{"path": "parties", "message": "A case can have at most 500 parties."}])
    roots = [item for item in parties if item.parent_id is None]
    if len(roots) != 1:
        raise _invalid([{"path": "parties", "message": "A case must have exactly one root entity."}])
    stored_root = next((row for row in existing.values() if row.parent_id is None), None)
    root = roots[0]
    if stored_root is None or root.id != stored_root.id:
        raise _invalid([
            {"path": "parties", "message": "The root entity cannot be replaced or removed."}
        ])
    if root.kind != "ENTITY":
        raise _invalid([{"path": "parties", "message": "The root party must be an entity."}])
    if root.entity_type != case.entity_type:
        raise _invalid([
            {"path": "parties", "message": "The root entity type must match the case."}
        ])
    seen: set[str] = set()
    by_request = {item.id: item for item in parties}
    for index, item in enumerate(parties):
        path = f"parties[{index}]"
        if not _ID.match(item.id):
            raise _invalid([{"path": f"{path}.id", "message": "Party id format is invalid."}])
        if item.id in seen:
            raise _invalid([{"path": f"{path}.id", "message": "Party ids must be unique."}])
        seen.add(item.id)
        legal_name = item.legal_name.strip()
        if not legal_name or len(legal_name) > 300:
            raise _invalid([
                {"path": f"{path}.legalName", "message": "Legal name must be 1–300 characters."}
            ])
        if item.kind == "PERSON":
            if item.entity_type is not None:
                raise _invalid([
                    {"path": f"{path}.entityType", "message": "entityType is only allowed on entities."}
                ])
            if item.us_tax_class is not None:
                raise _invalid([
                    {"path": f"{path}.usTaxClass", "message": "usTaxClass is only allowed on entities."}
                ])
        else:
            if item.entity_type not in ENTITY_TYPES:
                raise _invalid([
                    {"path": f"{path}.entityType", "message": "An entity must have an entityType."}
                ])
            if item.us_tax_class is not None and item.us_tax_class not in US_TAX_CLASSES:
                raise _invalid([{"path": f"{path}.usTaxClass", "message": "Value is not allowed."}])
        if item.parent_id is None:
            continue
        parent = by_request.get(item.parent_id)
        if parent is None or parent.kind != "ENTITY":
            message = (
                "A natural person cannot have owners or controllers under them."
                if parent is not None and parent.kind == "PERSON"
                else "Parent must be an entity in this case."
            )
            raise _invalid([{"path": f"{path}.parentId", "message": message}])
    if _cycle(parties):
        raise _invalid([{"path": "parties", "message": "Ownership links must not form a cycle."}])


def _cycle(parties: list[PartyIn]) -> bool:
    by_id = {item.id: item for item in parties}
    for item in parties:
        seen: set[str] = set()
        current = item
        while current.parent_id is not None:
            if current.id in seen:
                return True
            seen.add(current.id)
            parent = by_id.get(current.parent_id)
            if parent is None:
                break
            current = parent
    return False


def _changed(row: Party, item: PartyIn, index: int) -> bool:
    return (
        row.parent_id != item.parent_id
        or row.kind != item.kind
        or row.legal_name != item.legal_name.strip()
        or row.entity_type != (item.entity_type if item.kind == "ENTITY" else None)
        or row.country != item.country
        or row.title != item.title
        or row.us_tax_class != (item.us_tax_class if item.kind == "ENTITY" else None)
        or _money(row.ownership_percent) != _money(item.ownership_percent)
        or row.is_controller != item.is_controller
        or row.is_signing_authority != item.is_signing_authority
        or row.is_us_person != item.is_us_person
        or row.is_pep_hio != item.is_pep_hio
        or row.position != index
    )


def _assign(row: Party, item: PartyIn, index: int) -> None:
    row.parent_id = item.parent_id
    row.position = index
    row.kind = item.kind
    row.legal_name = item.legal_name.strip()
    row.entity_type = item.entity_type if item.kind == "ENTITY" else None
    row.country = item.country
    row.title = item.title
    row.us_tax_class = item.us_tax_class if item.kind == "ENTITY" else None
    row.ownership_percent = _money(item.ownership_percent)
    row.is_controller = item.is_controller
    row.is_signing_authority = item.is_signing_authority
    row.is_us_person = item.is_us_person
    row.is_pep_hio = item.is_pep_hio
    row.updated_at = datetime.now(timezone.utc)


def _new_row(case_id: str, item: PartyIn, index: int) -> Party:
    return Party(
        case_id=case_id,
        id=item.id,
        parent_id=item.parent_id,
        position=index,
        kind=item.kind,
        legal_name=item.legal_name.strip(),
        entity_type=item.entity_type if item.kind == "ENTITY" else None,
        country=item.country,
        title=item.title,
        us_tax_class=item.us_tax_class if item.kind == "ENTITY" else None,
        ownership_percent=_money(item.ownership_percent),
        is_controller=item.is_controller,
        is_signing_authority=item.is_signing_authority,
        is_us_person=item.is_us_person,
        is_pep_hio=item.is_pep_hio,
    )


def _row_dict(row: Party) -> dict:
    payload: dict = {
        "id": row.id,
        "parentId": row.parent_id,
        "kind": row.kind,
        "legalName": row.legal_name,
        "ownershipPercent": js_number(row.ownership_percent),
        "isController": row.is_controller,
        "isSigningAuthority": row.is_signing_authority,
        "isUsPerson": row.is_us_person,
        "isPepHio": row.is_pep_hio,
    }
    if row.entity_type is not None:
        payload["entityType"] = row.entity_type
    if row.country is not None:
        payload["country"] = row.country
    if row.title is not None:
        payload["title"] = row.title
    if row.us_tax_class is not None:
        payload["usTaxClass"] = row.us_tax_class
    return payload


def _payload_dict(item: PartyIn) -> dict:
    payload: dict = {
        "id": item.id,
        "parentId": item.parent_id,
        "kind": item.kind,
        "legalName": item.legal_name.strip(),
        "ownershipPercent": js_number(item.ownership_percent),
        "isController": item.is_controller,
        "isSigningAuthority": item.is_signing_authority,
        "isUsPerson": item.is_us_person,
        "isPepHio": item.is_pep_hio,
    }
    if item.kind == "ENTITY" and item.entity_type is not None:
        payload["entityType"] = item.entity_type
    if item.country is not None:
        payload["country"] = item.country
    if item.title is not None:
        payload["title"] = item.title
    if item.kind == "ENTITY" and item.us_tax_class is not None:
        payload["usTaxClass"] = item.us_tax_class
    return payload


def _money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(_QUANT)


def _changes(items: list | None) -> list[dict[str, str]] | None:
    if items is None:
        return None
    return [{"field": item.field, "from": item.from_, "to": item.to} for item in items]


def _known_model(model: str) -> None:
    if model not in get_settings().ai_models:
        raise _invalid([{"path": "model", "message": "Unknown model."}])


def _invalid(fields: list[dict[str, str]]) -> ApiError:
    return ApiError(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": fields})
