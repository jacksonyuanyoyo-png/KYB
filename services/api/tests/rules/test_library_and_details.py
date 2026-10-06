"""Pure tests for details, triggers, and pinned rule snapshots."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest

from fcc_api.rules.constants import RULE_VERSION
from fcc_api.rules.details import to_account_profile, validate_details
from fcc_api.rules.domain import generate_requirements, validate_ownership
from fcc_api.rules.library import adjustments_for, library_requirements, rule_applies
from fcc_api.rules.types import (
    AccountCase,
    AccountProfile,
    BuiltinOverride,
    LibraryRule,
    Party,
    ProfileDraft,
    RuleAdjustments,
    RuleTrigger,
)


def _person(party_id: str, **overrides: object) -> Party:
    values = dict(
        id=party_id,
        parent_id="root",
        kind="PERSON",
        legal_name=party_id,
        ownership_percent=0,
    )
    values.update(overrides)
    return Party(**values)  # type: ignore[arg-type]


def _account(**overrides: object) -> AccountCase:
    values = dict(
        parties=[
            Party(
                id="root",
                parent_id=None,
                kind="ENTITY",
                legal_name="Root",
                ownership_percent=100,
                entity_type="corporation",
            ),
            _person("alice", ownership_percent=100, is_controller=True),
        ],
        entity_type="corporation",
        profile=AccountProfile(
            province="ON", tax_residency="CANADA", features=[], trusted_contact=False
        ),
    )
    values.update(overrides)
    return AccountCase(**values)  # type: ignore[arg-type]


def _rule(kind: str, **trigger: object) -> LibraryRule:
    return LibraryRule(
        id="rule_extra",
        name="Extra",
        section="Account Features",
        conditional=True,
        source="Test",
        reason="Because",
        enabled=True,
        trigger=RuleTrigger(kind=kind, **trigger),  # type: ignore[arg-type]
    )


def test_validate_details_messages_and_blank_trusted_contact_name() -> None:
    empty = ProfileDraft(
        province="", tax_residency=None, features=[], trusted_contact=None, trusted_contact_name=""
    )
    assert [(gap.field, gap.message) for gap in validate_details(empty)] == [
        ("province", "Select the province or territory of registration."),
        ("taxResidency", "Select the entity's tax residency."),
        ("trustedContact", "Record whether a Trusted Contact Person is designated."),
    ]
    named = ProfileDraft(
        province="ON",
        tax_residency="CANADA",
        features=[],
        trusted_contact=True,
        trusted_contact_name="  ",
    )
    assert [(gap.field, gap.message) for gap in validate_details(named)] == [
        ("trustedContactName", "Enter the Trusted Contact Person's name."),
    ]
    complete = ProfileDraft(
        province="ON",
        tax_residency="CANADA",
        features=[],
        trusted_contact=False,
        trusted_contact_name="",
    )
    assert validate_details(complete) == []


def test_to_account_profile_defaults_match_the_workbench() -> None:
    profile = to_account_profile(
        ProfileDraft(
            province="",
            tax_residency=None,
            features=["MARGIN"],
            trusted_contact=None,
            trusted_contact_name="",
        )
    )
    assert profile.tax_residency == "CANADA"
    assert profile.trusted_contact is False
    assert profile.province == ""
    assert profile.features == ["MARGIN"]


def test_rule_applies_for_each_trigger() -> None:
    account = _account()
    assert rule_applies(_rule("ALWAYS"), account) is True
    assert rule_applies(_rule("ENTITY", entity_types=["trust"]), account) is False
    assert rule_applies(_rule("ENTITY", entity_types=["corporation"]), account) is True
    assert rule_applies(_rule("TAX", tax_residencies=["US"]), account) is False
    assert rule_applies(_rule("TAX", tax_residencies=["CANADA"]), account) is True
    assert rule_applies(_rule("FEATURE", feature="MARGIN"), account) is False
    assert rule_applies(_rule("FEATURE"), account) is False
    assert rule_applies(_rule("PERSON"), account) is True
    assert rule_applies(_rule("PEP"), account) is False
    assert rule_applies(_rule("US_PERSON"), account) is False
    assert rule_applies(_rule("TRUSTED_CONTACT"), account) is False
    assert rule_applies(_rule("UNKNOWN"), account) is False
    disabled = _rule("ALWAYS")
    disabled.enabled = False
    assert rule_applies(disabled, account) is False

    us_entity = _account(
        parties=[
            Party(
                id="root",
                parent_id=None,
                kind="ENTITY",
                legal_name="Root",
                ownership_percent=100,
                is_us_person=True,
            ),
        ]
    )
    assert rule_applies(_rule("US_PERSON"), us_entity) is True
    assert (
        rule_applies(
            _rule("PEP"),
            _account(
                parties=[
                    Party(
                        id="root",
                        parent_id=None,
                        kind="ENTITY",
                        legal_name="Root",
                        ownership_percent=100,
                        is_pep_hio=True,
                    ),
                ]
            ),
        )
        is True
    )
    no_profile = _account(profile=None)
    assert rule_applies(_rule("TAX", tax_residencies=["CANADA"]), no_profile) is False
    assert rule_applies(_rule("TRUSTED_CONTACT"), no_profile) is False
    assert rule_applies(_rule("FEATURE", feature="MARGIN"), no_profile) is False


def test_library_requirements_keep_order_and_empty_party_ids() -> None:
    first = _rule("ALWAYS")
    first.id = "rule_a"
    second = _rule("ENTITY", entity_types=["trust"])
    second.id = "rule_b"
    third = _rule("ALWAYS")
    third.id = "rule_c"
    third.enabled = False
    rows = library_requirements([first, second, third], _account())
    assert [row.id for row in rows] == ["rule_a"]
    assert rows[0].party_ids == []


def test_adjustments_follow_the_pinned_snapshot() -> None:
    extra = _rule("ALWAYS")
    extra.id = "rule_e"
    older = RuleAdjustments(extras=[extra], disabled=[], overrides={})
    newer = RuleAdjustments(extras=[], disabled=["directors"], overrides={})
    snapshots = {
        "published-2026-10-06": older,
        "published-2026-10-07": newer,
        RULE_VERSION: older,
    }
    pinned = adjustments_for("published-2026-10-06", snapshots)
    assert [rule.id for rule in pinned.extras] == ["rule_e"]
    latest = adjustments_for("published-2026-10-07", snapshots)
    assert latest.extras == []
    assert latest.disabled == ["directors"]
    assert adjustments_for(RULE_VERSION, snapshots).extras == []
    assert adjustments_for("published-missing", snapshots).extras == []


def test_unknown_builtin_version_is_rejected() -> None:
    with pytest.raises(ValueError):
        generate_requirements(_account(), builtin_version="demo-2026-11-01")


def test_zero_ownership_does_not_report_a_total_and_tolerance_accepts_a_cent() -> None:
    controllers = _account(
        parties=[
            Party(
                id="root", parent_id=None, kind="ENTITY", legal_name="Root", ownership_percent=100
            ),
            _person("a", is_controller=True, ownership_percent=0),
            _person("b", is_signing_authority=True, ownership_percent=0),
        ]
    )
    assert validate_ownership(controllers) == []
    split = _account(
        parties=[
            Party(
                id="root", parent_id=None, kind="ENTITY", legal_name="Root", ownership_percent=100
            ),
            _person("a", ownership_percent=33.33),
            _person("b", ownership_percent=33.33),
            _person("c", ownership_percent=33.34),
        ]
    )
    assert validate_ownership(split) == []


def test_missing_parent_is_treated_as_fully_owned_and_parents_insert_first() -> None:
    from fcc_api.rules.domain import effective_ownership

    orphan = _person("child", parent_id="missing", ownership_percent=40)
    assert effective_ownership([orphan])["child"] == 100
    child = _person("child", parent_id="root", ownership_percent=40)
    root = Party(id="root", parent_id=None, kind="ENTITY", legal_name="Root", ownership_percent=100)
    owned = effective_ownership([child, root])
    assert list(owned) == ["root", "child"]
    assert owned["child"] == 40


def test_override_shape_is_constructible() -> None:
    override = BuiltinOverride(
        name="Custom NAAF",
        section="Entity Formation & Authorization",
        conditional=False,
        source="FCC guide §5.2",
        reason="Changed",
    )
    assert override.name == "Custom NAAF"
