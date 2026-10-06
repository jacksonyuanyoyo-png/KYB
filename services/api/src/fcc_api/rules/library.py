"""Extra rules and published-version snapshots.

rule_applies and library_requirements follow apps/web/lib/rules/library.ts.
adjustments_for keeps the snapshot pinned to a case. A newer publication does
not replace it. The demonstrator version demo-2026-10-04 always contributes
an empty adjustment.
"""

from collections.abc import Mapping

from fcc_api.rules.constants import RULE_VERSION
from fcc_api.rules.types import AccountCase, LibraryRule, Requirement, RuleAdjustments


def rule_applies(rule: LibraryRule, account: AccountCase) -> bool:
    if not rule.enabled:
        return False
    profile = account.profile
    trigger = rule.trigger
    kind = trigger.kind
    if kind == "ALWAYS":
        return True
    if kind == "ENTITY":
        return account.entity_type in (trigger.entity_types or [])
    if kind == "TAX":
        return bool(
            profile is not None and profile.tax_residency in (trigger.tax_residencies or [])
        )
    if kind == "FEATURE":
        return bool(trigger.feature and profile is not None and trigger.feature in profile.features)
    if kind == "PERSON":
        return any(party.kind == "PERSON" for party in account.parties)
    if kind == "PEP":
        return any(party.is_pep_hio for party in account.parties)
    if kind == "US_PERSON":
        return (profile is not None and profile.tax_residency == "US") or any(
            party.is_us_person for party in account.parties
        )
    if kind == "TRUSTED_CONTACT":
        if profile is None:
            return False
        return profile.trusted_contact
    return False


def library_requirements(rules: list[LibraryRule], account: AccountCase) -> list[Requirement]:
    requirements: list[Requirement] = []
    for rule in rules:
        if not rule_applies(rule, account):
            continue
        requirements.append(
            Requirement(
                id=rule.id,
                section=rule.section,
                name=rule.name,
                conditional=rule.conditional,
                source=rule.source,
                reason=rule.reason,
                party_ids=[],
            )
        )
    return requirements


def adjustments_for(
    rule_version: str,
    snapshots: Mapping[str, RuleAdjustments],
) -> RuleAdjustments:
    if rule_version == RULE_VERSION:
        return RuleAdjustments()
    snapshot = snapshots.get(rule_version)
    if snapshot is None:
        return RuleAdjustments()
    return snapshot
