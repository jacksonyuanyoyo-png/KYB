"""P-01 through P-09. Expected values come from packages/domain, not from this engine."""

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest

from fcc_api.rules.domain import (
    effective_ownership,
    generate_requirements,
    persons_to_identify,
    validate_ownership,
    validate_profile,
)
from fcc_api.rules.types import AccountCase, AccountProfile, Party, Requirement, ValidationIssue

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "domain_cases.json"
_DOCUMENT = json.loads(_FIXTURE.read_text())
_CASES = _DOCUMENT["cases"]


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


def _profile(data: dict | None) -> AccountProfile | None:
    if data is None:
        return None
    province = data.get("province")
    return AccountProfile(
        province="" if province is None else province,
        tax_residency=data["taxResidency"],
        features=list(data.get("features") or []),
        trusted_contact=bool(data.get("trustedContact", False)),
    )


def _account(data: dict) -> AccountCase:
    profile = _profile(data["profile"]) if "profile" in data else None
    return AccountCase(
        id=data.get("id", ""),
        version=int(data.get("version", 0)),
        legal_name=data.get("legalName", ""),
        entity_type=data.get("entityType", "corporation"),
        status=data.get("status", "BUILDING"),
        parties=[_party(item) for item in data.get("parties", [])],
        rule_version=data.get("ruleVersion", ""),
        created_at=data.get("createdAt", ""),
        updated_at=data.get("updatedAt", ""),
        profile=profile,
    )


def _issue(issue: ValidationIssue) -> dict:
    if issue.party_id is None:
        return {"code": issue.code, "message": issue.message}
    return {"code": issue.code, "partyId": issue.party_id, "message": issue.message}


def _requirement(item: Requirement) -> dict:
    return {
        "id": item.id,
        "section": item.section,
        "name": item.name,
        "conditional": item.conditional,
        "source": item.source,
        "reason": item.reason,
        "partyIds": list(item.party_ids),
    }


def _run(case: dict):
    kind = case["fn"]
    payload = case["input"]
    if kind == "validateOwnership":
        return [_issue(item) for item in validate_ownership(_account(payload))]
    if kind == "validateProfile":
        return [_issue(item) for item in validate_profile(_profile(payload["profile"]))]
    if kind == "personsToIdentify":
        return [
            party.id for party in persons_to_identify([_party(item) for item in payload["parties"]])
        ]
    if kind == "effectiveOwnership":
        return effective_ownership([_party(item) for item in payload["parties"]])
    if kind == "generateRequirements":
        return [_requirement(item) for item in generate_requirements(_account(payload))]
    if kind == "identityPartyIds":
        rows = generate_requirements(_account(payload))
        return next(item.party_ids for item in rows if item.id == "identity")
    raise AssertionError(f"unknown fn {kind}")


@pytest.mark.parametrize("case", _CASES, ids=[f"{case['id']} {case['name']}" for case in _CASES])
def test_domain_parity(case: dict) -> None:
    actual = _run(case)
    expected = case["expected"]
    if case["fn"] == "effectiveOwnership":
        assert list(actual) == list(expected), case["id"]
    assert actual == expected, case["id"]
