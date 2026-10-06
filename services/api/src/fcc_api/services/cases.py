"""建案、状态推进、合规决定，以及案件详情组装。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from fcc_api.auth.actor import Actor
from fcc_api.db.models.cases import Case
from fcc_api.db.models.checklist import ChecklistItem, CustomRequirement
from fcc_api.db.models.documents import CaseDocument
from fcc_api.db.models.parties import Party
from fcc_api.db.models.rules import RuleLibraryState, RuleVersion
from fcc_api.db.models.tasks import ReviewTask
from fcc_api.db.models.users import User
from fcc_api.errors import ApiError
from fcc_api.ids import new_id
from fcc_api.labels import STATUS_LABELS
from fcc_api.rules.constants import ENTITY_TYPES, RULE_VERSION
from fcc_api.rules.details import validate_details
from fcc_api.rules.domain import validate_ownership
from fcc_api.rules.insight import analyze
from fcc_api.rules.types import BuiltinOverride
from fcc_api.rules.types import CaseRecord as RuleCase
from fcc_api.rules.types import ChecklistItemState as RuleChecklist
from fcc_api.rules.types import CustomRequirement as RuleCustom
from fcc_api.rules.types import LibraryRule, Party as RuleParty, ProfileDraft, ReviewTask as RuleTask
from fcc_api.rules.types import RuleAdjustments, RuleTrigger
from fcc_api.schemas.cases import (
    CaseDetailResponse,
    CaseOut,
    ChangeStatusIn,
    ChecklistItemOut,
    ChecklistRowOut,
    ComplianceDecisionIn,
    CreateCaseIn,
    CustomRequirementOut,
    DetailsGapOut,
    DocumentOut,
    InsightOut,
    OwnershipIssueOut,
    ProfileOut,
)
from fcc_api.schemas.common import js_number, to_js_iso
from fcc_api.schemas.parties import PartyOut
from fcc_api.schemas.tasks import TaskOut
from fcc_api.services.case_write import (
    CONFLICT_MESSAGE,
    case_write,
    insert_audit,
    require_create_case,
)
from fcc_api.services.references import next_reference
from fcc_api.services.screening import assert_screening_current

OWNERSHIP_MESSAGE = "Ownership structure is incomplete."
DETAILS_MESSAGE = "Account details are incomplete."
CHECKLIST_MESSAGE = "All checklist items must be collected first."
TASKS_MESSAGE = "Resolve open follow-up tasks first."
STATUS_MESSAGE = "The case is already in this status."

_STATUS_TARGETS = frozenset({"DOCS_REQUESTED", "READY_FOR_COMPLIANCE"})
_OWNER_ROLES = frozenset({"ADVISOR", "OPERATIONS"})


@dataclass
class _Bundle:
    parties: list[Party]
    checklist: list[ChecklistItem]
    customs: list[CustomRequirement]
    documents: list[CaseDocument]
    tasks: list[ReviewTask]
    record: RuleCase
    snapshots: dict[str, RuleAdjustments]
    document_ids: dict[str, list[str]]


def create_case(
    session: Session,
    actor: Actor,
    body: CreateCaseIn,
    *,
    request_id: str,
    correlation_id: str,
) -> CaseDetailResponse:
    require_create_case(actor, request_id=request_id)
    legal_name = _bounded("legalName", body.legal_name, 1, 300)
    entity_type = _choice("entityType", body.entity_type, ENTITY_TYPES)
    jurisdiction = _bounded("jurisdiction", body.jurisdiction, 0, 200)
    registration = _bounded("registrationNumber", body.registration_number, 0, 200)
    _require_owner(session, body.owner_id)
    suggested: str | None = None
    accepted = False
    if body.ai_entity_type is not None:
        suggested = _choice("aiEntityType.suggested", body.ai_entity_type.suggested, ENTITY_TYPES)
        accepted = body.ai_entity_type.accepted
    now = datetime.now(UTC)
    case_id = new_id("case")
    try:
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        reference = next_reference(session, now)
        published = _published_version(session)
        case = Case(
            id=case_id,
            reference=reference,
            version=1,
            legal_name=legal_name,
            entity_type=entity_type,
            status="BUILDING",
            rule_version=published,
            owner_id=body.owner_id,
            created_by=actor.id,
            jurisdiction=jurisdiction,
            registration_number=registration,
            province="",
            tax_residency=None,
            features=[],
            trusted_contact=None,
            trusted_contact_name="",
            due_date=now + timedelta(days=10),
            submitted_at=None,
            created_at=now,
            updated_at=now,
        )
        root = Party(
            case_id=case_id,
            id=new_id("p"),
            parent_id=None,
            position=0,
            kind="ENTITY",
            legal_name=legal_name,
            entity_type=entity_type,
            country=None,
            title=None,
            us_tax_class=None,
            ownership_percent=Decimal("100"),
            is_controller=False,
            is_signing_authority=False,
            is_us_person=False,
            is_pep_hio=False,
        )
        session.add(case)
        session.add(root)
        session.flush()
        profile = {
            "province": "",
            "taxResidency": None,
            "features": [],
            "trustedContact": None,
            "trustedContactName": "",
        }
        insert_audit(
            session,
            case=case,
            actor=actor,
            at=now,
            version=1,
            request_id=request_id,
            correlation_id=correlation_id,
            action="CASE_CREATED",
            summary=f"Created case for {legal_name}",
            before=None,
            after={
                "id": case.id,
                "reference": case.reference,
                "version": 1,
                "legalName": legal_name,
                "entityType": entity_type,
                "status": "BUILDING",
                "ruleVersion": published,
                "ownerId": body.owner_id,
                "createdBy": actor.id,
                "jurisdiction": jurisdiction,
                "registrationNumber": registration,
                "dueDate": to_js_iso(case.due_date),
                "profile": profile,
                "root": _party_dict(root),
            },
        )
        if suggested is not None:
            label = "accepted" if accepted else "overridden"
            insert_audit(
                session,
                case=case,
                actor=actor,
                at=now,
                version=1,
                request_id=request_id,
                correlation_id=correlation_id,
                action="AI_SUGGESTION",
                summary=f"Entity type suggested: {suggested} ({label})",
                ai_model="entity-classifier-demo",
                ai_accepted=accepted,
            )
        detail = load_case_detail(session, case_id)
        session.commit()
    except OperationalError as exc:
        session.rollback()
        if _lock_timeout(exc):
            raise ApiError(
                409,
                "VERSION_CONFLICT",
                CONFLICT_MESSAGE,
                {"resource": "caseReference", "reason": "LOCK_TIMEOUT"},
            ) from exc
        raise
    except Exception:
        session.rollback()
        raise
    return detail


def change_status(
    session: Session,
    actor: Actor,
    case_id: str,
    body: ChangeStatusIn,
    *,
    request_id: str,
    correlation_id: str,
) -> CaseDetailResponse:
    if body.status not in _STATUS_TARGETS:
        raise _invalid([
            {
                "path": "status",
                "message": "Status must be DOCS_REQUESTED or READY_FOR_COMPLIANCE.",
            }
        ])
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="changeStatus",
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        case = writer.case
        if case.status == body.status:
            raise ApiError(422, "GATE_FAILED", STATUS_MESSAGE, {"gate": "STATUS"})
        _assert_gates(session, case, files=body.status == "READY_FOR_COMPLIANCE")
        assert_screening_current(session, case)
        previous = case.status
        case.status = body.status
        if body.status == "READY_FOR_COMPLIANCE":
            case.submitted_at = writer.started_at
        writer.audit(
            action="STATUS_CHANGED",
            summary=body.summary.strip(),
            changes=[{
                "field": "Status",
                "from": STATUS_LABELS[previous],
                "to": STATUS_LABELS[body.status],
            }],
            before={"status": previous},
            after={"status": body.status, "submittedAt": _optional_iso(case.submitted_at)},
        )
        writer.seal()
        detail = load_case_detail(session, case_id)
    return detail


def compliance_decision(
    session: Session,
    actor: Actor,
    case_id: str,
    body: ComplianceDecisionIn,
    *,
    request_id: str,
    correlation_id: str,
) -> CaseDetailResponse:
    if body.decision == "RETURN" and len(body.comments) < 1:
        raise _invalid([{"path": "comments", "message": "Return at least one comment."}])
    if body.decision == "APPROVE" and body.comments:
        raise _invalid([{"path": "comments", "message": "Approval cannot include comments."}])
    with case_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=body.version,
        operation="complianceDecision",
        request_id=request_id,
        correlation_id=correlation_id,
    ) as writer:
        case = writer.case
        if body.decision == "APPROVE":
            _assert_gates(session, case, files=True)
            detail = _approve(session, writer, case)
        else:
            detail = _return_case(session, writer, case, body)
    return detail


def load_case_detail(session: Session, case_id: str) -> CaseDetailResponse:
    case = session.get(Case, case_id)
    if case is None:
        raise ApiError(404, "NOT_FOUND", "Case not found.", {"resource": "case", "id": case_id})
    bundle = _bundle(session, case)
    insight = analyze(bundle.record, bundle.snapshots)
    return CaseDetailResponse(
        case=_case_out(case, bundle),
        insight=InsightOut(
            ownership_issues=[_issue_out(item) for item in insight.ownership_issues],
            details_gaps=[
                DetailsGapOut(field=item.field, message=item.message) for item in insight.details_gaps
            ],
            checklist=[
                ChecklistRowOut(
                    id=item.id,
                    section=item.section,
                    name=item.name,
                    conditional=item.conditional,
                    source=item.source,
                    reason=item.reason,
                    party_ids=list(item.party_ids),
                    custom=item.custom,
                )
                for item in insight.checklist
            ],
            collected=insight.collected,
            stage=insight.stage,
            blocker=insight.blocker,
            identify=[item.id for item in insight.identify],
            effective={key: js_number(value) for key, value in insight.effective.items()},
            open_tasks=insight.open_tasks,
        ),
    )


def _approve(session: Session, writer: Any, case: Case) -> CaseDetailResponse:
    rows = list(
        session.scalars(
            select(ChecklistItem).where(
                ChecklistItem.case_id == case.id,
                ChecklistItem.status == "RECEIVED",
            )
        )
    )
    before_items = {row.requirement_id: row.status for row in rows}
    for row in rows:
        row.status = "VERIFIED"
        row.updated_at = writer.started_at
        row.updated_by = writer.actor.id
    case.status = "APPROVED"
    after_items = {key: "VERIFIED" for key in before_items}
    writer.audit(
        action="COMPLIANCE_DECISION",
        summary="Approved by Compliance",
        changes=[{
            "field": "Status",
            "from": STATUS_LABELS["READY_FOR_COMPLIANCE"],
            "to": STATUS_LABELS["APPROVED"],
        }],
        before={"status": "READY_FOR_COMPLIANCE", "checklist": before_items},
        after={"status": "APPROVED", "checklist": after_items, "taskIds": []},
    )
    writer.seal()
    return load_case_detail(session, case.id)


def _return_case(
    session: Session,
    writer: Any,
    case: Case,
    body: ComplianceDecisionIn,
) -> CaseDetailResponse:
    known = set(
        session.scalars(select(Party.id).where(Party.case_id == case.id))
    )
    task_ids: list[str] = []
    created: list[dict[str, Any]] = []
    for index, comment in enumerate(body.comments):
        title = comment.title.strip()
        if not title or len(title) > 500:
            raise _invalid([{"path": f"comments[{index}].title", "message": "Title must be 1–500 characters."}])
        if comment.party_id is not None and comment.party_id not in known:
            raise _invalid([{"path": f"comments[{index}].partyId", "message": "Party is not on this case."}])
        task_id = new_id("task")
        task_ids.append(task_id)
        session.add(
            ReviewTask(
                id=task_id,
                case_id=case.id,
                title=title,
                party_id=comment.party_id,
                requirement_id=comment.requirement_id,
                done=False,
                source="COMPLIANCE",
                created_by=writer.actor.id,
                created_at=writer.started_at,
                done_by=None,
                done_at=None,
            )
        )
        created.append({
            "id": task_id,
            "title": title,
            "partyId": comment.party_id,
            "requirementId": comment.requirement_id,
            "done": False,
            "source": "COMPLIANCE",
            "createdBy": writer.actor.id,
            "createdAt": to_js_iso(writer.started_at),
        })
    count = len(task_ids)
    suffix = "" if count == 1 else "s"
    case.status = "RETURNED"
    writer.audit(
        action="COMPLIANCE_DECISION",
        summary=f"Returned to advisor with {count} comment{suffix}",
        changes=[{
            "field": "Status",
            "from": STATUS_LABELS["READY_FOR_COMPLIANCE"],
            "to": STATUS_LABELS["RETURNED"],
        }],
        before={"status": "READY_FOR_COMPLIANCE"},
        after={"status": "RETURNED", "taskIds": task_ids, "tasks": created},
    )
    writer.seal()
    return load_case_detail(session, case.id)


def _assert_gates(session: Session, case: Case, *, files: bool) -> None:
    bundle = _bundle(session, case)
    issues = validate_ownership(bundle.record)
    if issues:
        raise ApiError(
            422,
            "GATE_FAILED",
            OWNERSHIP_MESSAGE,
            {"gate": "OWNERSHIP", "issues": [_issue_dict(item) for item in issues]},
        )
    gaps = validate_details(bundle.record.profile)
    if gaps:
        raise ApiError(
            422,
            "GATE_FAILED",
            DETAILS_MESSAGE,
            {
                "gate": "DETAILS",
                "issues": [{"field": item.field, "message": item.message} for item in gaps],
            },
        )
    if not files:
        return
    insight = analyze(bundle.record, bundle.snapshots)
    if insight.collected < len(insight.checklist):
        raise ApiError(422, "GATE_FAILED", CHECKLIST_MESSAGE, {"gate": "CHECKLIST"})
    if insight.open_tasks:
        raise ApiError(422, "GATE_FAILED", TASKS_MESSAGE, {"gate": "TASKS"})


def _bundle(session: Session, case: Case) -> _Bundle:
    parties = list(
        session.scalars(
            select(Party).where(Party.case_id == case.id).order_by(Party.position, Party.id)
        )
    )
    checklist = list(
        session.scalars(select(ChecklistItem).where(ChecklistItem.case_id == case.id))
    )
    customs = list(
        session.scalars(
            select(CustomRequirement)
            .where(CustomRequirement.case_id == case.id)
            .order_by(CustomRequirement.position, CustomRequirement.id)
        )
    )
    documents = list(
        session.scalars(
            select(CaseDocument)
            .where(CaseDocument.case_id == case.id)
            .order_by(CaseDocument.uploaded_at, CaseDocument.id)
        )
    )
    tasks = list(
        session.scalars(
            select(ReviewTask)
            .where(ReviewTask.case_id == case.id)
            .order_by(ReviewTask.created_at, ReviewTask.id)
        )
    )
    document_ids: dict[str, list[str]] = {}
    for document in documents:
        if document.requirement_id:
            document_ids.setdefault(document.requirement_id, []).append(document.id)
    record = RuleCase(
        id=case.id,
        legal_name=case.legal_name,
        entity_type=case.entity_type,
        status=case.status,
        version=case.version,
        rule_version=case.rule_version,
        created_at=to_js_iso(case.created_at),
        updated_at=to_js_iso(case.updated_at),
        parties=[_rule_party(row) for row in parties],
        profile=ProfileDraft(
            province=case.province,
            tax_residency=case.tax_residency,
            features=list(case.features or []),
            trusted_contact=case.trusted_contact,
            trusted_contact_name=case.trusted_contact_name,
        ),
        checklist={
            row.requirement_id: RuleChecklist(
                status=row.status,
                document_ids=list(document_ids.get(row.requirement_id, [])),
            )
            for row in checklist
        },
        custom_requirements=[
            RuleCustom(id=row.id, name=row.name, created_by=row.created_by, created_at=to_js_iso(row.created_at))
            for row in customs
        ],
        tasks=[
            RuleTask(
                id=row.id,
                title=row.title,
                done=row.done,
                source=row.source,
                created_by=row.created_by,
                created_at=to_js_iso(row.created_at),
                party_id=row.party_id,
                requirement_id=row.requirement_id,
            )
            for row in tasks
        ],
    )
    return _Bundle(
        parties=parties,
        checklist=checklist,
        customs=customs,
        documents=documents,
        tasks=tasks,
        record=record,
        snapshots=_snapshots(session, case.rule_version),
        document_ids=document_ids,
    )


def _case_out(case: Case, bundle: _Bundle) -> CaseOut:
    checklist: dict[str, ChecklistItemOut] = {}
    for row in bundle.checklist:
        checklist[row.requirement_id] = ChecklistItemOut(
            status=row.status,
            document_ids=list(bundle.document_ids.get(row.requirement_id, [])),
            updated_at=row.updated_at,
            updated_by=row.updated_by,
        )
    return CaseOut(
        id=case.id,
        reference=case.reference,
        version=case.version,
        legal_name=case.legal_name,
        entity_type=case.entity_type,
        status=case.status,
        owner_id=case.owner_id,
        jurisdiction=case.jurisdiction,
        registration_number=case.registration_number,
        rule_version=case.rule_version,
        created_at=case.created_at,
        updated_at=case.updated_at,
        due_date=case.due_date,
        submitted_at=case.submitted_at,
        parties=[_party_out(row) for row in bundle.parties],
        profile=ProfileOut(
            province=case.province,
            tax_residency=case.tax_residency,
            features=list(case.features or []),
            trusted_contact=case.trusted_contact,
            trusted_contact_name=case.trusted_contact_name,
        ),
        checklist=checklist,
        custom_requirements=[
            CustomRequirementOut(
                id=row.id,
                name=row.name,
                created_by=row.created_by,
                created_at=row.created_at,
            )
            for row in bundle.customs
        ],
        documents=[_document_out(row) for row in bundle.documents],
        tasks=[
            TaskOut(
                id=row.id,
                title=row.title,
                done=row.done,
                source=row.source,
                created_by=row.created_by,
                created_at=row.created_at,
                party_id=row.party_id,
                requirement_id=row.requirement_id,
            )
            for row in bundle.tasks
        ],
    )


def _snapshots(session: Session, rule_version: str) -> dict[str, RuleAdjustments]:
    if rule_version == RULE_VERSION:
        return {}
    row = session.get(RuleVersion, rule_version)
    if row is None:
        return {}
    return {rule_version: _adjustments(row)}


def _adjustments(row: RuleVersion) -> RuleAdjustments:
    extras: list[LibraryRule] = []
    for item in row.extras or []:
        if isinstance(item, dict):
            extras.append(_library_rule(item))
    overrides: dict[str, BuiltinOverride] = {}
    raw_overrides = row.overrides or {}
    if isinstance(raw_overrides, dict):
        for key, value in raw_overrides.items():
            if isinstance(value, dict):
                overrides[str(key)] = BuiltinOverride(
                    name=str(value.get("name", "")),
                    section=str(value.get("section", "")),
                    conditional=bool(value.get("conditional", False)),
                    source=str(value.get("source", "")),
                    reason=str(value.get("reason", "")),
                )
    return RuleAdjustments(
        extras=extras,
        disabled=list(row.disabled or []),
        overrides=overrides,
    )


def _library_rule(item: dict[str, Any]) -> LibraryRule:
    trigger = item.get("trigger") or {}
    if not isinstance(trigger, dict):
        trigger = {}
    return LibraryRule(
        id=str(item.get("id", "")),
        name=str(item.get("name", "")),
        section=str(item.get("section", "")),
        conditional=bool(item.get("conditional", False)),
        source=str(item.get("source", "")),
        reason=str(item.get("reason", "")),
        enabled=bool(item.get("enabled", True)),
        trigger=RuleTrigger(
            kind=str(trigger.get("kind", "ALWAYS")),
            entity_types=_optional_list(trigger, "entityTypes", "entity_types"),
            tax_residencies=_optional_list(trigger, "taxResidencies", "tax_residencies"),
            feature=_optional_str(trigger.get("feature")),
        ),
    )


def _optional_list(payload: dict[str, Any], *names: str) -> list[str] | None:
    for name in names:
        value = payload.get(name)
        if isinstance(value, list):
            return [str(item) for item in value]
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _published_version(session: Session) -> str:
    state = session.get(RuleLibraryState, 1)
    if state is None or not state.published_version:
        raise ApiError(503, "UNAVAILABLE", "The rule library is not available.", {})
    return state.published_version


def _require_owner(session: Session, owner_id: str) -> None:
    owner = session.get(User, owner_id)
    role = owner.role if owner is not None else None
    if owner is None or not owner.active or role not in _OWNER_ROLES:
        raise _invalid([
            {"path": "ownerId", "message": "Owner must be an active advisor or operations user."}
        ])


def _rule_party(row: Party) -> RuleParty:
    return RuleParty(
        id=row.id,
        parent_id=row.parent_id,
        kind=row.kind,
        legal_name=row.legal_name,
        ownership_percent=float(row.ownership_percent),
        is_controller=row.is_controller,
        is_signing_authority=row.is_signing_authority,
        is_us_person=row.is_us_person,
        is_pep_hio=row.is_pep_hio,
        entity_type=row.entity_type,
        country=row.country,
        title=row.title,
        us_tax_class=row.us_tax_class,
    )


def _party_out(row: Party) -> PartyOut:
    return PartyOut(
        id=row.id,
        parent_id=row.parent_id,
        kind=row.kind,
        legal_name=row.legal_name,
        ownership_percent=js_number(row.ownership_percent),
        is_controller=row.is_controller,
        is_signing_authority=row.is_signing_authority,
        is_us_person=row.is_us_person,
        is_pep_hio=row.is_pep_hio,
        entity_type=row.entity_type,
        country=row.country,
        title=row.title,
        us_tax_class=row.us_tax_class,
    )


def _party_dict(row: Party) -> dict[str, Any]:
    payload = _party_out(row).model_dump(by_alias=True, exclude_none=False)
    return {key: value for key, value in payload.items() if value is not None or key == "parentId"}


def _document_out(row: CaseDocument) -> DocumentOut:
    sha = None if row.sha256 is None else str(row.sha256).strip() or None
    return DocumentOut(
        id=row.id,
        requirement_id=row.requirement_id,
        file_name=row.file_name,
        size_bytes=int(row.size_bytes),
        mime_type=row.mime_type,
        uploaded_by=row.uploaded_by,
        uploaded_at=row.uploaded_at,
        extraction=row.extraction,
        sha256=sha,
        storage_state=row.storage_state,
        scan_status=row.scan_status,
    )


def _issue_out(item: Any) -> OwnershipIssueOut:
    return OwnershipIssueOut(code=item.code, message=item.message, party_id=item.party_id)


def _issue_dict(item: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": item.code, "message": item.message}
    if item.party_id:
        payload["partyId"] = item.party_id
    return payload


def _choice(path: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise _invalid([{"path": path, "message": "Value is not allowed."}])
    return value


def _bounded(path: str, value: str, low: int, high: int) -> str:
    cleaned = value.strip()
    if len(cleaned) < low or len(cleaned) > high:
        raise _invalid([{"path": path, "message": f"Length must be {low}–{high} characters."}])
    return cleaned


def _invalid(fields: list[dict[str, str]]) -> ApiError:
    return ApiError(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": fields})


def _optional_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return to_js_iso(value)


def _lock_timeout(exc: BaseException) -> bool:  # noqa: PLR0911
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if sqlstate == "55P03" or "lock timeout" in str(current).lower():
            return True
        nested = current.__cause__ or getattr(current, "orig", None)
        current = nested if isinstance(nested, BaseException) else None
    return False
