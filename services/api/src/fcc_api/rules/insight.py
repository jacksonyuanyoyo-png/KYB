"""Checklist assembly and case stage, from apps/web/lib/insights.ts.

DETAILS gaps come from validate_details. The province-only validate_profile
function is not consulted.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from fcc_api.rules.details import DetailsGap, to_account_profile, validate_details
from fcc_api.rules.domain import (
    effective_ownership,
    generate_requirements,
    persons_to_identify,
    validate_ownership,
)
from fcc_api.rules.library import adjustments_for, library_requirements
from fcc_api.rules.types import (
    AccountCase,
    BuiltinOverride,
    CaseRecord,
    ChecklistRow,
    Party,
    Requirement,
    RuleAdjustments,
    ValidationIssue,
)


@dataclass
class CaseInsight:
    ownership_issues: list[ValidationIssue]
    details_gaps: list[DetailsGap]
    checklist: list[ChecklistRow]
    collected: int
    stage: str
    blocker: str | None
    identify: list[Party]
    effective: dict[str, float]
    open_tasks: int


def checklist_for(
    record: CaseRecord,
    snapshots: Mapping[str, RuleAdjustments] | None = None,
) -> list[ChecklistRow]:
    adjustments = adjustments_for(record.rule_version, snapshots or {})
    account = AccountCase(
        id=record.id,
        version=record.version,
        legal_name=record.legal_name,
        entity_type=record.entity_type,
        status=record.status,
        parties=list(record.parties),
        rule_version=record.rule_version,
        created_at=record.created_at,
        updated_at=record.updated_at,
        profile=to_account_profile(record.profile),
    )
    disabled = set(adjustments.disabled)
    rows: list[ChecklistRow] = []
    for item in generate_requirements(account):
        if item.id in disabled:
            continue
        rows.append(_with_override(item, adjustments.overrides.get(item.id)))
    for item in library_requirements(list(adjustments.extras), account):
        rows.append(_row_from_requirement(item, custom=False))
    for item in record.custom_requirements:
        rows.append(
            ChecklistRow(
                id=item.id,
                section="Additional Requirements",
                name=item.name,
                conditional=True,
                source="Added by staff",
                reason="Additional requirement recorded on this case.",
                party_ids=[],
                custom=True,
            )
        )
    return rows


def is_collected(record: CaseRecord, requirement_id: str) -> bool:
    state = record.checklist.get(requirement_id)
    if state is None:
        return False
    return state.status == "RECEIVED" or state.status == "VERIFIED"


def analyze(
    record: CaseRecord,
    snapshots: Mapping[str, RuleAdjustments] | None = None,
) -> CaseInsight:
    ownership_issues = validate_ownership(record)
    details_gaps = validate_details(record.profile)
    checklist = checklist_for(record, snapshots)
    collected = sum(1 for item in checklist if is_collected(record, item.id))
    open_tasks = sum(1 for task in record.tasks if not task.done)
    blocker: str | None = None
    if record.status == "APPROVED":
        stage = "DONE"
    elif record.status == "READY_FOR_COMPLIANCE":
        stage = "COMPLIANCE"
    elif ownership_issues:
        stage = "OWNERSHIP"
        blocker = ownership_issues[0].message
    elif details_gaps:
        stage = "DETAILS"
        blocker = details_gaps[0].message
    else:
        stage = "DOCUMENTS"
        outstanding = len(checklist) - collected
        if outstanding:
            suffix = "" if outstanding == 1 else "s"
            blocker = f"{outstanding} document{suffix} outstanding"
    if record.status == "RETURNED" and open_tasks:
        suffix = "" if open_tasks == 1 else "s"
        blocker = f"{open_tasks} compliance task{suffix} open"
    return CaseInsight(
        ownership_issues=ownership_issues,
        details_gaps=details_gaps,
        checklist=checklist,
        collected=collected,
        stage=stage,
        blocker=blocker,
        identify=persons_to_identify(record.parties),
        effective=effective_ownership(record.parties),
        open_tasks=open_tasks,
    )


def _with_override(item: Requirement, override: BuiltinOverride | None) -> ChecklistRow:
    if override is None:
        return _row_from_requirement(item, custom=False)
    return ChecklistRow(
        id=item.id,
        section=override.section,
        name=override.name,
        conditional=override.conditional,
        source=override.source,
        reason=override.reason,
        party_ids=list(item.party_ids),
        custom=False,
    )


def _row_from_requirement(item: Requirement, custom: bool) -> ChecklistRow:
    return ChecklistRow(
        id=item.id,
        section=item.section,
        name=item.name,
        conditional=item.conditional,
        source=item.source,
        reason=item.reason,
        party_ids=list(item.party_ids),
        custom=custom,
    )
