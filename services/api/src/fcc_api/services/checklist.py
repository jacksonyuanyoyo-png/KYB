"""清单状态与追加要求。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fcc_api.auth.actor import Actor
from fcc_api.db.models.checklist import ChecklistItem, CustomRequirement
from fcc_api.errors import ApiError
from fcc_api.ids import new_id
from fcc_api.schemas.cases import CaseDetailResponse, ChecklistStatusIn, CustomRequirementIn
from fcc_api.schemas.common import to_js_iso
from fcc_api.services.case_write import case_write, require_item_status
from fcc_api.services.cases import _bundle, load_case_detail


def set_checklist_status(
    session: Session,
    actor: Actor,
    case_id: str,
    requirement_id: str,
    body: ChecklistStatusIn,
    *,
    request_id: str,
    correlation_id: str,
) -> CaseDetailResponse:
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="setChecklistStatus",
        request_id=request_id,
        correlation_id=correlation_id,
        target_status=body.status,
    ) as writer:
        bundle = _bundle(session, writer.case)
        name = next((item.name for item in analyze_rows(bundle) if item.id == requirement_id), None)
        if name is None:
            raise ApiError(
                404,
                "NOT_FOUND",
                "Checklist item not found.",
                {"resource": "checklistItem", "id": requirement_id},
            )
        row = next((item for item in bundle.checklist if item.requirement_id == requirement_id), None)
        previous = row.status if row is not None else "MISSING"
        require_item_status(
            actor,
            previous,
            body.status,
            case_id=case_id,
            request_id=request_id,
        )
        document_ids = list(bundle.document_ids.get(requirement_id, []))
        before = (
            {"status": "MISSING"}
            if row is None
            else {
                "status": previous,
                "documentIds": document_ids,
                "updatedAt": to_js_iso(row.updated_at),
                "updatedBy": row.updated_by,
            }
        )
        if row is None:
            session.add(
                ChecklistItem(
                    case_id=case_id,
                    requirement_id=requirement_id,
                    status=body.status,
                    updated_at=writer.started_at,
                    updated_by=actor.id,
                )
            )
        else:
            row.status = body.status
            row.updated_at = writer.started_at
            row.updated_by = actor.id
        writer.audit(
            action="CHECKLIST_UPDATED",
            summary=f"{name}: {previous.lower()} → {body.status.lower()}",
            changes=[{"field": name, "from": previous, "to": body.status}],
            before=before,
            after={
                "status": body.status,
                "documentIds": document_ids,
                "updatedAt": to_js_iso(writer.started_at),
                "updatedBy": actor.id,
            },
        )
        writer.seal()
        detail = load_case_detail(session, case_id)
    return detail


def add_custom_requirement(
    session: Session,
    actor: Actor,
    case_id: str,
    body: CustomRequirementIn,
    *,
    request_id: str,
    correlation_id: str,
) -> CaseDetailResponse:
    name = body.name.strip()
    if not name or len(name) > 200:
        raise ApiError(
            400,
            "VALIDATION_FAILED",
            "Request body is invalid.",
            {"fields": [{"path": "name", "message": "Name must be 1–200 characters."}]},
        )
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="addCustomRequirement",
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        current = session.scalar(
            select(func.max(CustomRequirement.position)).where(CustomRequirement.case_id == case_id)
        )
        position = 0 if current is None else int(current) + 1
        requirement_id = new_id("req")
        session.add(
            CustomRequirement(
                id=requirement_id,
                case_id=case_id,
                name=name,
                position=position,
                created_by=actor.id,
                created_at=writer.started_at,
            )
        )
        writer.audit(
            action="REQUIREMENT_ADDED",
            summary=f"Added additional requirement “{name}”",
            before=None,
            after={
                "id": requirement_id,
                "name": name,
                "createdBy": actor.id,
                "createdAt": to_js_iso(writer.started_at),
            },
        )
        writer.seal()
        detail = load_case_detail(session, case_id)
    return detail


def analyze_rows(bundle: object) -> list:
    from fcc_api.rules.insight import analyze

    insight = analyze(bundle.record, bundle.snapshots)  # type: ignore[attr-defined]
    return insight.checklist
