"""S-01 through S-07. Inputs are the seed.ts cases; nothing is read from a database."""

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest

from fcc_api.rules.insight import analyze
from fcc_api.rules.types import (
    CaseRecord,
    ChecklistItemState,
    ChecklistRow,
    CustomRequirement,
    Party,
    ProfileDraft,
    Requirement,
    ReviewTask,
    ValidationIssue,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "domain_cases.json"
_SEED_CASES = json.loads(_FIXTURE.read_text())["seedCases"]
_BY_ID = {case["id"]: case for case in _SEED_CASES}


def _party(data: dict) -> Party:
    return Party(
        id=data["id"],
        parent_id=data.get("parentId"),
        kind=data["kind"],
        legal_name=data["legalName"],
        ownership_percent=float(data.get("ownershipPercent", 0)),
        is_controller=bool(data.get("isController", False)),
        is_signing_authority=bool(data.get("isSigningAuthority", False)),
        is_us_person=bool(data.get("isUsPerson", False)),
        is_pep_hio=bool(data.get("isPepHio", False)),
        entity_type=data.get("entityType"),
        country=data.get("country"),
        title=data.get("title"),
        us_tax_class=data.get("usTaxClass"),
    )


def _draft(data: dict) -> ProfileDraft:
    province = data.get("province")
    name = data.get("trustedContactName")
    return ProfileDraft(
        province="" if province is None else province,
        tax_residency=data.get("taxResidency"),
        features=list(data.get("features") or []),
        trusted_contact=data["trustedContact"] if "trustedContact" in data else None,
        trusted_contact_name="" if name is None else name,
    )


def _record(data: dict) -> CaseRecord:
    return CaseRecord(
        id=data["id"],
        legal_name=data.get("legalName", ""),
        entity_type=data["entityType"],
        status=data["status"],
        parties=[_party(item) for item in data["parties"]],
        profile=_draft(data["profile"]),
        rule_version=data["ruleVersion"],
        checklist={
            key: ChecklistItemState(
                status=value["status"], document_ids=list(value.get("documentIds") or [])
            )
            for key, value in data.get("checklist", {}).items()
        },
        custom_requirements=[
            CustomRequirement(id=item["id"], name=item["name"])
            for item in data.get("customRequirements", [])
        ],
        tasks=[
            ReviewTask(
                id=item["id"],
                title=item.get("title", ""),
                done=bool(item["done"]),
                party_id=item.get("partyId"),
                requirement_id=item.get("requirementId"),
            )
            for item in data.get("tasks", [])
        ],
    )


def _issue(issue: ValidationIssue) -> dict:
    if issue.party_id is None:
        return {"code": issue.code, "message": issue.message}
    return {"code": issue.code, "partyId": issue.party_id, "message": issue.message}


def _requirement(item: Requirement | ChecklistRow) -> dict:
    payload = {
        "id": item.id,
        "section": item.section,
        "name": item.name,
        "conditional": item.conditional,
        "source": item.source,
        "reason": item.reason,
        "partyIds": list(item.party_ids),
    }
    if isinstance(item, ChecklistRow):
        payload["custom"] = item.custom
    return payload


def _insight(case: dict) -> dict:
    result = analyze(_record(case["input"]))
    return {
        "ownershipIssues": [_issue(item) for item in result.ownership_issues],
        "detailsGaps": [
            {"field": gap.field, "message": gap.message} for gap in result.details_gaps
        ],
        "checklist": [_requirement(item) for item in result.checklist],
        "collected": result.collected,
        "stage": result.stage,
        "blocker": result.blocker,
        "openTasks": result.open_tasks,
        "identify": [party.id for party in result.identify],
        "effective": result.effective,
    }


@pytest.mark.parametrize("case_id", ["S-01", "S-02", "S-03", "S-04", "S-05", "S-06", "S-07"])
def test_seed_insight(case_id: str) -> None:
    case = _BY_ID[case_id]
    actual = _insight(case)
    expected = case["expected"]
    assert list(actual["effective"]) == list(expected["effective"]), case_id
    assert actual == expected, case_id


def test_s01_party_links_and_effective_percentages() -> None:
    insight = analyze(_record(_BY_ID["S-01"]["input"]))
    by_id = {item.id: item.party_ids for item in insight.checklist}
    assert by_id["resolution"] == ["mr-alice"]
    assert by_id["beneficial-owner"] == ["mr-alice", "mr-david"]
    assert by_id["identity"] == ["mr-alice", "mr-david"]
    assert by_id["pep"] == ["mr-david"]
    assert by_id["w9"] == ["mr-mei"]
    assert insight.effective["mr-raj"] == 24.5
    assert insight.effective["mr-mei"] == 10.5
    assert insight.effective["mr-david"] == 25
    assert [item.id for item in insight.checklist] == [
        "naaf",
        "formation",
        "resolution",
        "beneficial-owner",
        "directors",
        "identity",
        "pep",
        "margin",
        "options",
        "tcp",
        "w9",
        "w8",
        "rc519",
    ]
    assert "w8" in by_id
    assert "nffe" not in by_id


def test_s02_zero_ownership_controllers_do_not_fail_the_total() -> None:
    insight = analyze(_record(_BY_ID["S-02"]["input"]))
    assert insight.ownership_issues == []
    assert [gap.field for gap in insight.details_gaps] == ["province", "trustedContact"]
    assert insight.blocker == "Select the province or territory of registration."


def test_s05_rejected_formation_is_not_collected_and_nffe_lists_persons() -> None:
    record = _record(_BY_ID["S-05"]["input"])
    insight = analyze(record)
    assert record.checklist["formation"].status == "REJECTED"
    assert insight.collected == 2
    assert insight.ownership_issues[0].message == "Pacific Rim Ventures LP ownership totals 91%."
    assert "91.0" not in insight.ownership_issues[0].message
    nffe = next(item for item in insight.checklist if item.id == "nffe")
    assert nffe.party_ids == ["pr-wei", "pr-sophie"]
    assert insight.blocker == "3 compliance tasks open"
    assert insight.stage == "OWNERSHIP"
