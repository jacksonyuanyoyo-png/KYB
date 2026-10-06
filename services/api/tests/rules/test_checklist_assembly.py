"""Checklist order: builtins, then extras, then case custom items."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from fcc_api.rules.insight import analyze, checklist_for
from fcc_api.rules.types import (
    BuiltinOverride,
    CaseRecord,
    ChecklistItemState,
    CustomRequirement,
    LibraryRule,
    Party,
    ProfileDraft,
    ReviewTask,
    RuleAdjustments,
    RuleTrigger,
)


def _record(**overrides: object) -> CaseRecord:
    values = dict(
        id="case-x",
        legal_name="Root",
        entity_type="corporation",
        status="BUILDING",
        rule_version="published-2026-10-06",
        parties=[
            Party(
                id="root",
                parent_id=None,
                kind="ENTITY",
                legal_name="Root Co",
                ownership_percent=100,
                entity_type="corporation",
            ),
            Party(
                id="alice",
                parent_id="root",
                kind="PERSON",
                legal_name="Alice",
                ownership_percent=100,
                is_controller=True,
                is_signing_authority=True,
            ),
        ],
        profile=ProfileDraft(
            province="ON",
            tax_residency="CANADA",
            features=[],
            trusted_contact=False,
            trusted_contact_name="",
        ),
    )
    values.update(overrides)
    return CaseRecord(**values)  # type: ignore[arg-type]


def test_disabled_override_extra_and_custom_keep_checklist_order() -> None:
    extra = LibraryRule(
        id="rule_e",
        name="Extra letter",
        section="Account Features",
        conditional=True,
        source="Staff",
        reason="Always ask",
        enabled=True,
        trigger=RuleTrigger(kind="ALWAYS"),
    )
    adjustments = RuleAdjustments(
        extras=[extra],
        disabled=["directors"],
        overrides={
            "naaf": BuiltinOverride(
                name="Custom NAAF",
                section="Entity Formation & Authorization",
                conditional=False,
                source="FCC guide §5.2",
                reason="Renamed",
            )
        },
    )
    record = _record(
        custom_requirements=[CustomRequirement(id="custom-1", name="Certified translation")]
    )
    rows = checklist_for(record, {"published-2026-10-06": adjustments})
    assert [row.id for row in rows] == [
        "naaf",
        "formation",
        "resolution",
        "beneficial-owner",
        "identity",
        "rule_e",
        "custom-1",
    ]
    naaf = rows[0]
    assert naaf.name == "Custom NAAF"
    assert naaf.reason == "Renamed"
    assert naaf.id == "naaf"
    assert naaf.party_ids == []
    assert naaf.custom is False
    resolution = next(row for row in rows if row.id == "resolution")
    assert resolution.party_ids == ["alice"]
    extra_row = next(row for row in rows if row.id == "rule_e")
    assert extra_row.party_ids == []
    assert extra_row.custom is False
    custom = rows[-1]
    assert custom.custom is True
    assert custom.section == "Additional Requirements"
    assert custom.conditional is True
    assert custom.source == "Added by staff"
    assert custom.reason == "Additional requirement recorded on this case."


def test_demonstrator_version_ignores_snapshots() -> None:
    record = _record(rule_version="demo-2026-10-04")
    extra = LibraryRule(
        id="rule_e",
        name="Extra letter",
        section="Account Features",
        conditional=True,
        source="Staff",
        reason="Always ask",
        enabled=True,
        trigger=RuleTrigger(kind="ALWAYS"),
    )
    rows = checklist_for(
        record, {"demo-2026-10-04": RuleAdjustments(extras=[extra], disabled=["directors"])}
    )
    ids = [row.id for row in rows]
    assert "directors" in ids
    assert "rule_e" not in ids


def test_singular_document_and_returned_task_blocker() -> None:
    record = _record(rule_version="demo-2026-10-04", status="DOCS_REQUESTED")
    rows = checklist_for(record)
    record.checklist = {
        row.id: ChecklistItemState(status="VERIFIED") for row in rows if row.id != "identity"
    }
    insight = analyze(record)
    assert insight.stage == "DOCUMENTS"
    assert insight.blocker == "1 document outstanding"

    returned = _record(
        rule_version="demo-2026-10-04",
        status="RETURNED",
        tasks=[ReviewTask(id="task-1", title="Fix it", done=False)],
    )
    returned_insight = analyze(returned)
    assert returned_insight.blocker == "1 compliance task open"
