"""Read models for lists and case detail.

Case list entry point for the cases router: ``list_cases``.
Also implemented here, and not mounted by this agent because those paths belong
to other routers: ``get_case_detail``, ``list_case_audit``, ``list_documents``,
``list_tasks``.
"""

from __future__ import annotations

import base64
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, false, func, or_, select, true
from sqlalchemy.orm import Session

from fcc_api.schemas.audit import AuditEventOut, AuditItemsResponse, AuditListResponse
from fcc_api.schemas.rules import ENTITY_TYPES, ApiModel
from fcc_api.services.rule_library import (
    _fail,
    _materialize_parties,
    _party_carrier,
    _plain,
    _public_adjustments,
    _table_model,
    assemble_requirements,
)

CASE_FILTERS = {"ALL", "MINE", "BUILDING", "DOCS_REQUESTED", "READY_FOR_COMPLIANCE", "RETURNED", "APPROVED"}
STATUS_FILTERS = {"BUILDING", "DOCS_REQUESTED", "READY_FOR_COMPLIANCE", "RETURNED", "APPROVED"}
COMPLIANCE_STATUSES = ("READY_FOR_COMPLIANCE", "RETURNED", "APPROVED")
ENTITY_KINDS = {"ALL", "PERSON", "ENTITY", "PEP", "US"}
DOCUMENT_FILTERS = {"ALL", "PROCESSING", "UNASSIGNED"}
AUDIT_ACTIONS = {
    "CASE_CREATED",
    "OWNERSHIP_UPDATED",
    "PROFILE_UPDATED",
    "DOCUMENT_UPLOADED",
    "CHECKLIST_UPDATED",
    "REQUIREMENT_ADDED",
    "STATUS_CHANGED",
    "COMPLIANCE_DECISION",
    "AI_SUGGESTION",
    "TASK_UPDATED",
    "RULE_LIBRARY",
}
COLLECTED = {"RECEIVED", "VERIFIED"}


class PartyOut(ApiModel):
    id: str
    parent_id: str | None
    kind: str
    legal_name: str
    entity_type: str | None = None
    country: str | None = None
    title: str | None = None
    us_tax_class: str | None = None
    ownership_percent: int | float
    is_controller: bool
    is_signing_authority: bool
    is_us_person: bool
    is_pep_hio: bool


class ProfileOut(ApiModel):
    province: str
    tax_residency: str | None
    features: list[str]
    trusted_contact: bool | None
    trusted_contact_name: str


class ChecklistStateOut(ApiModel):
    status: str
    document_ids: list[str]
    updated_at: str | None = None
    updated_by: str | None = None


class DocumentOut(ApiModel):
    id: str
    requirement_id: str | None
    file_name: str
    size_bytes: int
    mime_type: str
    uploaded_by: str
    uploaded_at: str
    extraction: str
    sha256: str | None = None
    storage_state: str
    scan_status: str


class TaskOut(ApiModel):
    id: str
    title: str
    party_id: str | None = None
    requirement_id: str | None = None
    done: bool
    source: str
    created_by: str
    created_at: str


class CustomRequirementOut(ApiModel):
    id: str
    name: str
    created_by: str
    created_at: str


class ListInsightOut(ApiModel):
    stage: str
    blocker: str | None
    collected: int
    checklist_total: int
    open_tasks: int
    identify_count: int
    has_pep: bool


class CaseListItem(ApiModel):
    id: str
    reference: str
    version: int
    legal_name: str
    entity_type: str
    status: str
    owner_id: str
    jurisdiction: str
    registration_number: str
    rule_version: str
    created_at: str
    updated_at: str
    due_date: str
    submitted_at: str | None
    document_count: int
    insight: ListInsightOut


class CaseListResponse(ApiModel):
    items: list[CaseListItem]
    counts: dict[str, int]
    next_cursor: str | None = None


class InsightOut(ApiModel):
    ownership_issues: list[dict[str, Any]]
    details_gaps: list[dict[str, Any]]
    checklist: list[dict[str, Any]]
    collected: int
    stage: str
    blocker: str | None
    identify: list[str]
    effective: dict[str, int | float]
    open_tasks: int


class CaseOut(ApiModel):
    id: str
    reference: str
    version: int
    legal_name: str
    entity_type: str
    status: str
    owner_id: str
    jurisdiction: str
    registration_number: str
    rule_version: str
    created_at: str
    updated_at: str
    due_date: str
    submitted_at: str | None
    parties: list[PartyOut]
    profile: ProfileOut
    checklist: dict[str, ChecklistStateOut]
    custom_requirements: list[CustomRequirementOut]
    documents: list[DocumentOut]
    tasks: list[TaskOut]


class CaseDetailResponse(ApiModel):
    case: CaseOut
    insight: InsightOut


class ComplianceItem(ApiModel):
    id: str
    reference: str
    legal_name: str
    entity_type: str
    status: str
    version: int
    owner_id: str
    due_date: str
    updated_at: str
    submitted_at: str | None
    waiting_since: str
    identify_count: int
    has_pep: bool
    collected: int
    checklist_total: int
    open_tasks: int


class ComplianceQueueResponse(ApiModel):
    items: list[ComplianceItem]
    counts: dict[str, int]


class EntityItem(ApiModel):
    case_id: str
    case_reference: str
    case_legal_name: str
    party: PartyOut
    parent_name: str | None
    identify: bool
    effective: int | float


class EntityListResponse(ApiModel):
    items: list[EntityItem]
    counts: dict[str, int]


class DocumentLedgerItem(ApiModel):
    document: DocumentOut
    case_id: str
    case_reference: str
    case_legal_name: str
    requirement_name: str | None


class DocumentListResponse(ApiModel):
    items: list[DocumentLedgerItem]
    counts: dict[str, int]


class TaskListItem(ApiModel):
    task: TaskOut
    case_id: str
    case_reference: str
    case_legal_name: str


class TaskListResponse(ApiModel):
    items: list[TaskListItem]
    total: int


class _Bundle:
    def __init__(self, case: Any, parties: list[Any], checklist: list[Any], customs: list[Any], documents: list[Any], tasks: list[Any], rule: Any | None) -> None:
        self.case = case
        self.parties = parties
        self.checklist = checklist
        self.customs = customs
        self.documents = documents
        self.tasks = tasks
        self.rule = rule


def list_cases(
    session: Session,
    actor: Any,
    *,
    filter: str = "ALL",
    entity_type: str | None = None,
    q: str | None = None,
    limit: int = 200,
    cursor: str | None = None,
) -> CaseListResponse:
    """GET /api/v1/cases. Mount this from the cases router; this module does not."""
    _check_choice("filter", filter, CASE_FILTERS)
    if entity_type is not None and entity_type not in ENTITY_TYPES:
        _invalid("entityType", "entityType is not a known entity type")
    _check_limit(limit, 500)
    case_model = _case_model()
    visibility = visible_case_clause(case_model, actor)
    counts = _case_counts(session, case_model, visibility, actor)
    query = select(case_model).where(visibility)
    if filter == "MINE":
        query = query.where(case_model.owner_id == actor.id)
    elif filter in STATUS_FILTERS:
        query = query.where(case_model.status == filter)
    if entity_type:
        query = query.where(case_model.entity_type == entity_type)
    if q:
        pattern = _contains(q)
        query = query.where(
            or_(
                case_model.legal_name.ilike(pattern, escape="\\"),
                case_model.reference.ilike(pattern, escape="\\"),
                case_model.registration_number.ilike(pattern, escape="\\"),
            )
        )
    if cursor:
        stamp, case_id = _decode_case_cursor(cursor)
        query = query.where(
            or_(
                case_model.updated_at < stamp,
                and_(case_model.updated_at == stamp, case_model.id < case_id),
            )
        )
    rows = session.scalars(query.order_by(case_model.updated_at.desc(), case_model.id.desc()).limit(limit + 1)).all()
    page = list(rows[:limit])
    bundles = _bundles(session, page)
    items = [_list_item(bundles[row.id]) for row in page]
    next_cursor = _encode_case_cursor(page[-1].updated_at, page[-1].id) if len(rows) > limit and page else None
    return CaseListResponse(items=items, counts=counts, next_cursor=next_cursor)


def get_case_detail(session: Session, actor: Any, case_id: str) -> CaseDetailResponse:
    """GET /api/v1/cases/{caseId}. Not mounted here."""
    bundle = _one_bundle(session, actor, case_id)
    return _detail(bundle)


def list_case_audit(session: Session, actor: Any, case_id: str) -> AuditItemsResponse:
    """GET /api/v1/cases/{caseId}/audit. Not mounted here."""
    _one_bundle(session, actor, case_id)
    audit_model = _table_model("fcc_api.db.models.audit", "audit_events")
    rows = session.scalars(
        select(audit_model).where(audit_model.case_id == case_id).order_by(audit_model.seq.desc())
    ).all()
    return AuditItemsResponse(items=[_audit_out(row) for row in rows])


def list_compliance_queue(session: Session, actor: Any, *, status: str = "READY_FOR_COMPLIANCE") -> ComplianceQueueResponse:
    _check_choice("status", status, set(COMPLIANCE_STATUSES))
    case_model = _case_model()
    visibility = visible_case_clause(case_model, actor)
    counts = {name: 0 for name in COMPLIANCE_STATUSES}
    grouped = session.execute(
        select(case_model.status, func.count())
        .where(visibility, case_model.status.in_(COMPLIANCE_STATUSES))
        .group_by(case_model.status)
    ).all()
    for name, count in grouped:
        counts[str(name)] = int(count)
    waiting = func.coalesce(case_model.submitted_at, case_model.updated_at)
    rows = session.scalars(
        select(case_model)
        .where(visibility, case_model.status == status)
        .order_by(waiting.asc(), case_model.id.asc())
    ).all()
    bundles = _bundles(session, list(rows))
    items = []
    for row in rows:
        view = _analyzed(bundles[row.id])
        waiting_at = row.submitted_at or row.updated_at
        items.append(
            ComplianceItem(
                id=row.id,
                reference=row.reference,
                legal_name=row.legal_name,
                entity_type=row.entity_type,
                status=row.status,
                version=int(row.version),
                owner_id=row.owner_id,
                due_date=_iso(row.due_date) or "",
                updated_at=_iso(row.updated_at) or "",
                submitted_at=_iso(row.submitted_at),
                waiting_since=_iso(waiting_at) or "",
                identify_count=view["identifyCount"],
                has_pep=view["hasPep"],
                collected=view["collected"],
                checklist_total=view["checklistTotal"],
                open_tasks=view["openTasks"],
            )
        )
    return ComplianceQueueResponse(items=items, counts=counts)


def list_entities(session: Session, actor: Any, *, kind: str = "ALL", q: str | None = None) -> EntityListResponse:
    _check_choice("kind", kind, ENTITY_KINDS)
    case_model = _case_model()
    party_model = _table_model("fcc_api.db.models.parties", "parties")
    visibility = visible_case_clause(case_model, actor)
    cases = session.scalars(select(case_model).where(visibility)).all()
    case_by_id = {row.id: row for row in cases}
    if not cases:
        return EntityListResponse(items=[], counts={name: 0 for name in ("ALL", "PERSON", "ENTITY", "PEP", "US")})
    parties = session.scalars(
        select(party_model).where(party_model.case_id.in_(list(case_by_id))).order_by(party_model.case_id, party_model.position)
    ).all()
    by_case: dict[str, list[Any]] = defaultdict(list)
    for party in parties:
        by_case[party.case_id].append(party)
    rows: list[tuple[Any, Any, str | None, bool, int | float]] = []
    for case in cases:
        group = by_case.get(case.id, [])
        identify_ids = set(_identify_ids(group))
        effective = _effective_map(group)
        names = {party.id: party.legal_name for party in group}
        for party in group:
            if party.parent_id is None:
                continue
            rows.append(
                (
                    case,
                    party,
                    names.get(party.parent_id),
                    party.id in identify_ids,
                    effective.get(party.id, 0),
                )
            )
    counts = {
        "ALL": len(rows),
        "PERSON": sum(1 for _, party, *_rest in rows if party.kind == "PERSON"),
        "ENTITY": sum(1 for _, party, *_rest in rows if party.kind == "ENTITY"),
        "PEP": sum(1 for _, party, *_rest in rows if party.is_pep_hio),
        "US": sum(1 for _, party, *_rest in rows if party.is_us_person),
    }
    needle = (q or "").strip().lower()
    items = []
    for case, party, parent_name, identify, effective in rows:
        if kind == "PERSON" and party.kind != "PERSON":
            continue
        if kind == "ENTITY" and party.kind != "ENTITY":
            continue
        if kind == "PEP" and not party.is_pep_hio:
            continue
        if kind == "US" and not party.is_us_person:
            continue
        if needle and needle not in party.legal_name.lower() and needle not in case.legal_name.lower():
            continue
        items.append(
            EntityItem(
                case_id=case.id,
                case_reference=case.reference,
                case_legal_name=case.legal_name,
                party=_party_out(party),
                parent_name=parent_name,
                identify=identify,
                effective=effective,
            )
        )
    items.sort(key=lambda item: (item.case_reference, item.party.id))
    return EntityListResponse(items=items, counts=counts)


def list_documents(session: Session, actor: Any, *, filter: str = "ALL") -> DocumentListResponse:
    """GET /api/v1/documents. Not mounted here."""
    _check_choice("filter", filter, DOCUMENT_FILTERS)
    case_model = _case_model()
    document_model = _table_model("fcc_api.db.models.documents", "case_documents")
    visibility = visible_case_clause(case_model, actor)
    cases = session.scalars(select(case_model).where(visibility)).all()
    case_by_id = {row.id: row for row in cases}
    empty = {"ALL": 0, "PROCESSING": 0, "UNASSIGNED": 0}
    if not cases:
        return DocumentListResponse(items=[], counts=empty)
    documents = session.scalars(
        select(document_model)
        .where(document_model.case_id.in_(list(case_by_id)))
        .order_by(document_model.uploaded_at.desc(), document_model.id.desc())
    ).all()
    counts = {
        "ALL": len(documents),
        "PROCESSING": sum(1 for row in documents if row.extraction == "PROCESSING"),
        "UNASSIGNED": sum(1 for row in documents if row.requirement_id is None),
    }
    selected = []
    for row in documents:
        if filter == "PROCESSING" and row.extraction != "PROCESSING":
            continue
        if filter == "UNASSIGNED" and row.requirement_id is not None:
            continue
        selected.append(row)
    bundles = _bundles(session, [case_by_id[row.case_id] for row in selected])
    names: dict[tuple[str, str], str] = {}
    for case_id, bundle in bundles.items():
        for item in _analyzed(bundle)["checklist"]:
            names[(case_id, item["id"])] = item["name"]
    items = []
    for row in selected:
        case = case_by_id[row.case_id]
        requirement_name = names.get((row.case_id, row.requirement_id)) if row.requirement_id else None
        items.append(
            DocumentLedgerItem(
                document=_document_out(row),
                case_id=case.id,
                case_reference=case.reference,
                case_legal_name=case.legal_name,
                requirement_name=requirement_name,
            )
        )
    return DocumentListResponse(items=items, counts=counts)


def list_audit_events(
    session: Session,
    actor: Any,
    *,
    action: str | None = None,
    case_id: str | None = None,
    actor_id: str | None = None,
    limit: int = 200,
    cursor: str | None = None,
) -> AuditListResponse:
    if action is not None and action not in AUDIT_ACTIONS:
        _invalid("action", "action is not a known audit action")
    _check_limit(limit, 500)
    audit_model = _table_model("fcc_api.db.models.audit", "audit_events")
    case_model = _case_model()
    query = select(audit_model)
    role = getattr(actor, "role", None)
    if role == "ADVISOR":
        visible_ids = select(case_model.id).where(visible_case_clause(case_model, actor))
        query = query.where(
            or_(
                audit_model.scope == "RULE_LIBRARY",
                and_(audit_model.scope == "CASE", audit_model.case_id.in_(visible_ids)),
            )
        )
    elif role not in {"OPERATIONS", "COMPLIANCE", "ADMIN"}:
        query = query.where(false())
    if action:
        query = query.where(audit_model.action == action)
    if case_id == "rule-library":
        query = query.where(audit_model.scope == "RULE_LIBRARY")
    elif case_id:
        query = query.where(audit_model.case_id == case_id)
    if actor_id:
        query = query.where(audit_model.actor_id == actor_id)
    if cursor:
        query = query.where(audit_model.seq < _decode_seq(cursor))
    rows = session.scalars(query.order_by(audit_model.seq.desc()).limit(limit + 1)).all()
    page = list(rows[:limit])
    next_cursor = _encode_seq(int(page[-1].seq)) if len(rows) > limit and page else None
    return AuditListResponse(items=[_audit_out(row) for row in page], next_cursor=next_cursor)


def list_tasks(session: Session, actor: Any, *, done: bool = False, limit: int = 5) -> TaskListResponse:
    """GET /api/v1/tasks. Not mounted here."""
    _check_limit(limit, 100)
    case_model = _case_model()
    task_model = _table_model("fcc_api.db.models.tasks", "review_tasks")
    visibility = visible_case_clause(case_model, actor)
    joined = (
        select(task_model, case_model)
        .join(case_model, case_model.id == task_model.case_id)
        .where(visibility, task_model.done == done)
        .order_by(task_model.created_at.desc(), task_model.id.desc())
    )
    total = session.scalar(
        select(func.count())
        .select_from(task_model)
        .join(case_model, case_model.id == task_model.case_id)
        .where(visibility, task_model.done == done)
    ) or 0
    rows = session.execute(joined.limit(limit)).all()
    items = [
        TaskListItem(
            task=_task_out(task),
            case_id=case.id,
            case_reference=case.reference,
            case_legal_name=case.legal_name,
        )
        for task, case in rows
    ]
    return TaskListResponse(items=items, total=int(total))


def visible_case_clause(case_model: Any, actor: Any) -> Any:
    """Advisor rows match owner or creator. ``case_owner_filter`` returns that id, or None for every case."""
    from fcc_api.auth.policy import case_owner_filter

    owner_id = case_owner_filter(actor)
    if owner_id is None:
        return true()
    return or_(case_model.owner_id == owner_id, case_model.created_by == owner_id)


def _case_counts(session: Session, case_model: Any, visibility: Any, actor: Any) -> dict[str, int]:
    grouped = session.execute(select(case_model.status, func.count()).where(visibility).group_by(case_model.status)).all()
    by_status = {str(status): int(count) for status, count in grouped}
    total = sum(by_status.values())
    mine = session.scalar(select(func.count()).select_from(case_model).where(visibility, case_model.owner_id == actor.id)) or 0
    return {
        "ALL": total,
        "MINE": int(mine),
        "BUILDING": by_status.get("BUILDING", 0),
        "DOCS_REQUESTED": by_status.get("DOCS_REQUESTED", 0),
        "READY_FOR_COMPLIANCE": by_status.get("READY_FOR_COMPLIANCE", 0),
        "RETURNED": by_status.get("RETURNED", 0),
        "APPROVED": by_status.get("APPROVED", 0),
    }


def _list_item(bundle: _Bundle) -> CaseListItem:
    case = bundle.case
    view = _analyzed(bundle)
    return CaseListItem(
        id=case.id,
        reference=case.reference,
        version=int(case.version),
        legal_name=case.legal_name,
        entity_type=case.entity_type,
        status=case.status,
        owner_id=case.owner_id,
        jurisdiction=case.jurisdiction or "",
        registration_number=case.registration_number or "",
        rule_version=case.rule_version,
        created_at=_iso(case.created_at) or "",
        updated_at=_iso(case.updated_at) or "",
        due_date=_iso(case.due_date) or "",
        submitted_at=_iso(case.submitted_at),
        document_count=len(bundle.documents),
        insight=ListInsightOut(
            stage=view["stage"],
            blocker=view["blocker"],
            collected=view["collected"],
            checklist_total=view["checklistTotal"],
            open_tasks=view["openTasks"],
            identify_count=view["identifyCount"],
            has_pep=view["hasPep"],
        ),
    )


def _detail(bundle: _Bundle) -> CaseDetailResponse:
    case = bundle.case
    view = _analyzed(bundle)
    documents = sorted(bundle.documents, key=lambda row: (row.uploaded_at, row.id))
    doc_ids: dict[str, list[str]] = defaultdict(list)
    for document in documents:
        if document.requirement_id:
            doc_ids[document.requirement_id].append(document.id)
    checklist = {
        row.requirement_id: ChecklistStateOut(
            status=row.status,
            document_ids=doc_ids.get(row.requirement_id, []),
            updated_at=_iso(row.updated_at),
            updated_by=row.updated_by,
        )
        for row in bundle.checklist
    }
    return CaseDetailResponse(
        case=CaseOut(
            id=case.id,
            reference=case.reference,
            version=int(case.version),
            legal_name=case.legal_name,
            entity_type=case.entity_type,
            status=case.status,
            owner_id=case.owner_id,
            jurisdiction=case.jurisdiction or "",
            registration_number=case.registration_number or "",
            rule_version=case.rule_version,
            created_at=_iso(case.created_at) or "",
            updated_at=_iso(case.updated_at) or "",
            due_date=_iso(case.due_date) or "",
            submitted_at=_iso(case.submitted_at),
            parties=[_party_out(row) for row in bundle.parties],
            profile=ProfileOut(
                province=case.province or "",
                tax_residency=case.tax_residency,
                features=list(case.features or []),
                trusted_contact=case.trusted_contact,
                trusted_contact_name=case.trusted_contact_name or "",
            ),
            checklist=checklist,
            custom_requirements=[
                CustomRequirementOut(id=row.id, name=row.name, created_by=row.created_by, created_at=_iso(row.created_at) or "")
                for row in bundle.customs
            ],
            documents=[_document_out(row) for row in documents],
            tasks=[_task_out(row) for row in sorted(bundle.tasks, key=lambda item: (item.created_at, item.id))],
        ),
        insight=InsightOut(
            ownership_issues=view["ownershipIssues"],
            details_gaps=view["detailsGaps"],
            checklist=view["checklist"],
            collected=view["collected"],
            stage=view["stage"],
            blocker=view["blocker"],
            identify=view["identify"],
            effective=view["effective"],
            open_tasks=view["openTasks"],
        ),
    )


def _analyzed(bundle: _Bundle) -> dict[str, Any]:
    case = bundle.case
    party_dicts = [_party_dict(row) for row in bundle.parties]
    stored_profile = {
        "province": case.province or "",
        "taxResidency": case.tax_residency,
        "features": list(case.features or []),
        "trustedContact": case.trusted_contact,
        "trustedContactName": case.trusted_contact_name or "",
    }
    rules_profile = {
        "province": stored_profile["province"],
        "taxResidency": stored_profile["taxResidency"] or "CANADA",
        "features": stored_profile["features"],
        "trustedContact": False if stored_profile["trustedContact"] is None else bool(stored_profile["trustedContact"]),
    }
    account = {
        "id": case.id,
        "version": int(case.version),
        "legalName": case.legal_name,
        "entityType": case.entity_type,
        "status": case.status,
        "ruleVersion": case.rule_version,
        "createdAt": _iso(case.created_at) or "",
        "updatedAt": _iso(case.updated_at) or "",
        "parties": party_dicts,
        "profile": rules_profile,
    }
    adjustments = _public_adjustments(bundle.rule) if bundle.rule is not None else {"extras": [], "disabled": [], "overrides": {}}
    builtin_version = str(bundle.rule.builtin_version) if bundle.rule is not None else case.rule_version
    checklist = assemble_requirements(account, adjustments, builtin_version)
    for item in checklist:
        item["custom"] = False
    for custom in bundle.customs:
        checklist.append(
            {
                "id": custom.id,
                "section": "Additional Requirements",
                "name": custom.name,
                "conditional": True,
                "source": "Added by staff",
                "reason": "Additional requirement recorded on this case.",
                "partyIds": [],
                "custom": True,
            }
        )
    status_by_id = {row.requirement_id: row.status for row in bundle.checklist}
    collected = sum(1 for item in checklist if status_by_id.get(item["id"]) in COLLECTED)
    ownership = _ownership_issues(party_dicts)
    gaps = _details_gaps(stored_profile)
    open_tasks = sum(1 for task in bundle.tasks if not task.done)
    identify = _identify_ids(bundle.parties)
    blocker: str | None = None
    if case.status == "APPROVED":
        stage = "DONE"
    elif case.status == "READY_FOR_COMPLIANCE":
        stage = "COMPLIANCE"
    elif ownership:
        stage = "OWNERSHIP"
        blocker = ownership[0].get("message")
    elif gaps:
        stage = "DETAILS"
        blocker = gaps[0].get("message")
    else:
        stage = "DOCUMENTS"
        remaining = len(checklist) - collected
        if remaining:
            blocker = f"{remaining} document{'' if remaining == 1 else 's'} outstanding"
    if case.status == "RETURNED" and open_tasks:
        blocker = f"{open_tasks} compliance task{'' if open_tasks == 1 else 's'} open"
    return {
        "ownershipIssues": ownership,
        "detailsGaps": gaps,
        "checklist": checklist,
        "collected": collected,
        "checklistTotal": len(checklist),
        "stage": stage,
        "blocker": blocker,
        "identify": identify,
        "identifyCount": len(identify),
        "effective": _effective_map(bundle.parties),
        "openTasks": open_tasks,
        "hasPep": any(bool(party.is_pep_hio) for party in bundle.parties),
    }


def _bundles(session: Session, cases: list[Any]) -> dict[str, _Bundle]:
    unique: list[Any] = []
    seen: set[str] = set()
    for case in cases:
        if case.id not in seen:
            seen.add(case.id)
            unique.append(case)
    if not unique:
        return {}
    ids = [case.id for case in unique]
    party_model = _table_model("fcc_api.db.models.parties", "parties")
    checklist_model = _table_model("fcc_api.db.models.checklist", "checklist_items")
    custom_model = _table_model("fcc_api.db.models.checklist", "custom_requirements")
    document_model = _table_model("fcc_api.db.models.documents", "case_documents")
    task_model = _table_model("fcc_api.db.models.tasks", "review_tasks")
    rule_model = _table_model("fcc_api.db.models.rules", "rule_versions")
    parties = session.scalars(select(party_model).where(party_model.case_id.in_(ids)).order_by(party_model.position)).all()
    checklist = session.scalars(select(checklist_model).where(checklist_model.case_id.in_(ids))).all()
    customs = session.scalars(select(custom_model).where(custom_model.case_id.in_(ids)).order_by(custom_model.position)).all()
    documents = session.scalars(select(document_model).where(document_model.case_id.in_(ids))).all()
    tasks = session.scalars(select(task_model).where(task_model.case_id.in_(ids))).all()
    versions = {case.rule_version for case in unique}
    rules = session.scalars(select(rule_model).where(rule_model.version.in_(versions))).all()
    rule_by_version = {row.version: row for row in rules}
    grouped: dict[str, dict[str, list[Any]]] = defaultdict(lambda: {"parties": [], "checklist": [], "customs": [], "documents": [], "tasks": []})
    for row in parties:
        grouped[row.case_id]["parties"].append(row)
    for row in checklist:
        grouped[row.case_id]["checklist"].append(row)
    for row in customs:
        grouped[row.case_id]["customs"].append(row)
    for row in documents:
        grouped[row.case_id]["documents"].append(row)
    for row in tasks:
        grouped[row.case_id]["tasks"].append(row)
    return {
        case.id: _Bundle(
            case,
            grouped[case.id]["parties"],
            grouped[case.id]["checklist"],
            grouped[case.id]["customs"],
            grouped[case.id]["documents"],
            grouped[case.id]["tasks"],
            rule_by_version.get(case.rule_version),
        )
        for case in unique
    }


def _one_bundle(session: Session, actor: Any, case_id: str) -> _Bundle:
    from fcc_api.auth.policy import require_case_visible

    case_model = _case_model()
    case = session.get(case_model, case_id)
    require_case_visible(actor, case, case_id=case_id)
    return _bundles(session, [case])[case.id]


def _ownership_issues(parties: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from fcc_api.rules.domain import validate_ownership

    materialized = _materialize_parties(parties)
    try:
        raw = validate_ownership(_party_carrier(materialized))
    except Exception:
        raw = validate_ownership(materialized)
    issues = []
    for item in _plain(raw) or []:
        data = item if isinstance(item, dict) else {}
        if not isinstance(item, dict):
            continue
        camel = { _camel_key(str(key)): value for key, value in data.items() }
        issue = {"code": camel.get("code"), "message": camel.get("message")}
        if camel.get("partyId"):
            issue["partyId"] = camel["partyId"]
        issues.append(issue)
    return issues


def _details_gaps(profile: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from fcc_api.rules.details import validate_details
        from fcc_api.rules.types import ProfileDraft
        from fcc_api.services.rule_library import _build

        raw = validate_details(_build(ProfileDraft, profile))
        gaps = []
        for item in _plain(raw) or []:
            if isinstance(item, dict):
                camel = {_camel_key(str(key)): value for key, value in item.items()}
                gaps.append({"field": camel.get("field"), "message": camel.get("message")})
        return gaps
    except ImportError:
        return _local_details(profile)


def _local_details(profile: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if not profile.get("province"):
        gaps.append({"field": "province", "message": "Select the province or territory of registration."})
    if not profile.get("taxResidency"):
        gaps.append({"field": "taxResidency", "message": "Select the entity's tax residency."})
    if profile.get("trustedContact") is None:
        gaps.append({"field": "trustedContact", "message": "Record whether a Trusted Contact Person is designated."})
    if profile.get("trustedContact") and not str(profile.get("trustedContactName") or "").strip():
        gaps.append({"field": "trustedContactName", "message": "Enter the Trusted Contact Person's name."})
    return gaps


def _identify_ids(parties: list[Any]) -> list[str]:
    from fcc_api.rules.domain import persons_to_identify
    from fcc_api.services.rule_library import _party_ids

    dicts = parties if parties and isinstance(parties[0], dict) else [_party_dict(row) for row in parties]
    return _party_ids(persons_to_identify(_materialize_parties(dicts)))


def _effective_map(parties: list[Any]) -> dict[str, int | float]:
    from fcc_api.rules.domain import effective_ownership

    dicts = parties if parties and isinstance(parties[0], dict) else [_party_dict(row) for row in parties]
    raw = _plain(effective_ownership(_materialize_parties(dicts)))
    if not isinstance(raw, dict):
        return {}
    return {str(key): _js_num(value) for key, value in raw.items()}


def _ai_decision(row: Any) -> str:
    """还没人接受或拒绝的建议，不能当成拒绝。"""

    after = row.after_value if isinstance(row.after_value, dict) else {}
    meta = after.get("ai") if isinstance(after, dict) else None
    if isinstance(meta, dict) and meta.get("stage") == "SUGGESTED":
        return "suggested"
    return "accepted" if row.ai_accepted else "rejected"


def _audit_out(row: Any) -> AuditEventOut:
    changes = None
    if row.changes:
        changes = [
            {"field": item.get("field"), "from": item.get("from"), "to": item.get("to")}
            for item in row.changes
            if isinstance(item, dict)
        ]
    ai = None
    if row.ai_model is not None:
        decision = _ai_decision(row)
        ai = {
            "model": row.ai_model,
            "accepted": None if decision == "suggested" else bool(row.ai_accepted),
            "decision": decision,
            "ruleVersion": row.ai_rule_version,
        }
    case_id = "rule-library" if row.scope == "RULE_LIBRARY" or row.case_id is None and row.action == "RULE_LIBRARY" else row.case_id
    return AuditEventOut.model_validate(
        {
            "id": row.id,
            "caseId": case_id,
            "actorId": row.actor_id,
            "action": row.action,
            "summary": row.summary,
            "at": _iso(row.at),
            "version": int(row.version),
            "changes": changes,
            "ai": ai,
            "ruleVersion": row.rule_version,
            "correlationId": row.correlation_id,
        }
    )


def _party_out(row: Any) -> PartyOut:
    return PartyOut(
        id=row.id,
        parent_id=row.parent_id,
        kind=row.kind,
        legal_name=row.legal_name,
        entity_type=row.entity_type,
        country=row.country,
        title=row.title,
        us_tax_class=row.us_tax_class,
        ownership_percent=_js_num(row.ownership_percent),
        is_controller=bool(row.is_controller),
        is_signing_authority=bool(row.is_signing_authority),
        is_us_person=bool(row.is_us_person),
        is_pep_hio=bool(row.is_pep_hio),
    )


def _party_dict(row: Any) -> dict[str, Any]:
    data = {
        "id": row.id,
        "parentId": row.parent_id,
        "kind": row.kind,
        "legalName": row.legal_name,
        "ownershipPercent": float(row.ownership_percent),
        "isController": bool(row.is_controller),
        "isSigningAuthority": bool(row.is_signing_authority),
        "isUsPerson": bool(row.is_us_person),
        "isPepHio": bool(row.is_pep_hio),
    }
    if row.entity_type:
        data["entityType"] = row.entity_type
    if row.country:
        data["country"] = row.country
    if row.title:
        data["title"] = row.title
    if row.us_tax_class:
        data["usTaxClass"] = row.us_tax_class
    return data


def _document_out(row: Any) -> DocumentOut:
    return DocumentOut(
        id=row.id,
        requirement_id=row.requirement_id,
        file_name=row.file_name,
        size_bytes=int(row.size_bytes),
        mime_type=row.mime_type,
        uploaded_by=row.uploaded_by,
        uploaded_at=_iso(row.uploaded_at) or "",
        extraction=row.extraction,
        sha256=row.sha256,
        storage_state=row.storage_state,
        scan_status=row.scan_status,
    )


def _task_out(row: Any) -> TaskOut:
    return TaskOut(
        id=row.id,
        title=row.title,
        party_id=row.party_id,
        requirement_id=row.requirement_id,
        done=bool(row.done),
        source=row.source,
        created_by=row.created_by,
        created_at=_iso(row.created_at) or "",
    )


def _case_model() -> Any:
    return _table_model("fcc_api.db.models.cases", "cases")


def _check_choice(path: str, value: str, allowed: set[str]) -> None:
    if value not in allowed:
        _invalid(path, f"{path} is not a supported value")


def _check_limit(limit: int, maximum: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= maximum:
        _invalid("limit", f"limit must be from 1 to {maximum}")


def _invalid(path: str, message: str) -> None:
    _fail(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": [{"path": path, "message": message}]})


def _contains(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _encode_case_cursor(updated_at: datetime, case_id: str) -> str:
    raw = f"{_aware(updated_at).isoformat()}|{case_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_case_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        stamp, case_id = raw.split("|", 1)
        return _aware(datetime.fromisoformat(stamp)), case_id
    except Exception:
        _invalid("cursor", "Cursor is invalid.")
        raise AssertionError("cursor")


def _encode_seq(seq: int) -> str:
    return base64.urlsafe_b64encode(str(seq).encode()).decode().rstrip("=")


def _decode_seq(cursor: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return int(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        _invalid("cursor", "Cursor is invalid.")
        raise AssertionError("cursor")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    current = _aware(value)
    millis = current.microsecond // 1000
    return current.strftime("%Y-%m-%dT%H:%M:%S.") + f"{millis:03d}Z"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _js_num(value: Any) -> int | float:
    number = float(value)
    if number.is_integer():
        return int(number)
    return number


def _camel_key(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:] if part)

