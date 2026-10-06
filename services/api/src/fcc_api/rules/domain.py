"""Builtin ownership checks and the 16 coded requirements.

Behavior matches packages/domain/src/index.ts for builtin version demo-2026-10-04.
"""

import math
from typing import Protocol, Sequence

from fcc_api.rules.constants import BENEFICIAL_OWNER_THRESHOLD, RULE_VERSION
from fcc_api.rules.types import AccountCase, AccountProfile, Party, Requirement, ValidationIssue


class _HasParties(Protocol):
    parties: Sequence[Party]


def js_number_str(value: int | float) -> str:
    """Format a number the way JavaScript String(number) does.

    Whole numbers stay free of a trailing ".0", so an ownership total of 60
    is rendered as "60%" rather than "60.0%".
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("js_number_str expects a real number")
    number = float(value)
    if math.isnan(number):
        return "NaN"
    if number == math.inf:
        return "Infinity"
    if number == -math.inf:
        return "-Infinity"
    if number == 0.0:
        return "0"
    sign = "-" if math.copysign(1.0, number) < 0 else ""
    digits, exponent = _es_digits(abs(number))
    width = len(digits)
    if width <= exponent <= 21:
        body = digits + ("0" * (exponent - width))
    elif 0 < exponent <= 21:
        body = digits[:exponent] + "." + digits[exponent:]
    elif -6 < exponent <= 0:
        body = "0." + ("0" * (-exponent)) + digits
    elif width == 1:
        body = digits + "e" + _js_exponent(exponent - 1)
    else:
        body = digits[0] + "." + digits[1:] + "e" + _js_exponent(exponent - 1)
    return sign + body


def _js_exponent(exponent: int) -> str:
    sign = "+" if exponent >= 0 else "-"
    return sign + str(abs(exponent))


def _es_digits(magnitude: float) -> tuple[str, int]:
    raw = repr(magnitude)
    if "e" in raw:
        mantissa, exp_text = raw.split("e")
        power = int(exp_text)
        if "." in mantissa:
            whole, frac = mantissa.split(".")
            digits = whole + frac
            power -= len(frac)
        else:
            digits = mantissa
    elif "." in raw:
        whole, frac = raw.split(".")
        digits = whole + frac
        power = -len(frac)
    else:
        digits = raw
        power = 0
    digits = digits.lstrip("0") or "0"
    trimmed = digits.rstrip("0") or "0"
    power += len(digits) - len(trimmed)
    digits = trimmed
    return digits, power + len(digits)


def validate_ownership(account: _HasParties) -> list[ValidationIssue]:
    parties = list(account.parties)
    root = next((party for party in parties if party.parent_id is None), None)
    if root is None:
        return [ValidationIssue(code="MISSING_ROOT", message="A root entity is required.")]
    issues: list[ValidationIssue] = []
    for entity in parties:
        if entity.kind != "ENTITY":
            continue
        children = [party for party in parties if party.parent_id == entity.id]
        if not children:
            code = "MISSING_OWNER" if entity.id == root.id else "ENTITY_LEAF"
            issues.append(
                ValidationIssue(
                    code=code,
                    party_id=entity.id,
                    message=f"{entity.legal_name} must disclose an owner or controller.",
                )
            )
            continue
        total = 0.0
        for party in children:
            total += float(party.ownership_percent)
        if total > 0 and abs(total - 100) > 0.01:
            issues.append(
                ValidationIssue(
                    code="OWNERSHIP_TOTAL",
                    party_id=entity.id,
                    message=f"{entity.legal_name} ownership totals {js_number_str(total)}%.",
                )
            )
    return issues


def validate_profile(profile: AccountProfile | None) -> list[ValidationIssue]:
    if profile is None or not profile.province:
        return [
            ValidationIssue(
                code="PROFILE_INCOMPLETE",
                message="Province or territory of registration is required.",
            )
        ]
    return []


def effective_ownership(parties: Sequence[Party]) -> dict[str, float]:
    by_id = {party.id: party for party in parties}
    result: dict[str, float] = {}

    def resolve(party: Party) -> float:
        if party.id in result:
            return result[party.id]
        parent = by_id.get(party.parent_id) if party.parent_id else None
        if parent is None:
            value = 100.0
        else:
            value = (resolve(parent) * float(party.ownership_percent)) / 100.0
        result[party.id] = value
        return value

    for party in parties:
        resolve(party)
    return result


def persons_to_identify(parties: Sequence[Party]) -> list[Party]:
    effective = effective_ownership(parties)
    identified: list[Party] = []
    for party in parties:
        if party.kind != "PERSON":
            continue
        owned = effective.get(party.id, 0)
        if owned >= BENEFICIAL_OWNER_THRESHOLD or party.is_controller or party.is_signing_authority:
            identified.append(party)
    return identified


def generate_requirements(
    account: AccountCase, builtin_version: str = RULE_VERSION
) -> list[Requirement]:
    if builtin_version != RULE_VERSION:
        raise ValueError(f"unknown builtin_version: {builtin_version}")
    return _generate_requirements_demo_2026_10_04(account)


def _generate_requirements_demo_2026_10_04(account: AccountCase) -> list[Requirement]:
    profile = account.profile
    people = [party for party in account.parties if party.kind == "PERSON"]
    identify = persons_to_identify(account.parties)
    peps = [party for party in people if party.is_pep_hio]
    us_persons = [party for party in account.parties if party.is_us_person]
    signers = [party for party in people if party.is_signing_authority or party.is_controller]
    tax = profile.tax_residency if profile is not None else None

    def ids(selected: list[Party]) -> list[str]:
        return [party.id for party in selected]

    def has_feature(feature: str) -> bool:
        return profile is not None and feature in profile.features

    rules: list[tuple[Requirement, bool]] = [
        (
            _requirement(
                "naaf",
                "Entity Formation & Authorization",
                "New Account Application Form (NAAF)",
                False,
                "FCC guide §5.2",
                "Required for every new account. Include the CRA Business Number when applicable.",
                [],
            ),
            True,
        ),
        (
            _requirement(
                "formation",
                "Entity Formation & Authorization",
                "Articles / formation or governing document",
                False,
                "FCC guide §3",
                "Confirms the entity exists and who governs it.",
                [],
            ),
            True,
        ),
        (
            _requirement(
                "resolution",
                "Entity Formation & Authorization",
                "Corporate resolution / signing authority evidence",
                False,
                "FCC guide §5.2",
                "Entity type acts through a board or governing body.",
                ids(signers),
            ),
            account.entity_type
            in ("corporation", "condo", "charity", "association", "first_nation"),
        ),
        (
            _requirement(
                "beneficial-owner",
                "Entity Formation & Authorization",
                "Beneficial Owner Identification",
                False,
                "CIRO 3203–3204; Compliance approval required",
                "CIRO/FINTRAC ownership and control record.",
                ids(identify),
            ),
            account.entity_type
            in ("corporation", "partnership", "pooled_fund", "trust", "ipp_rca"),
        ),
        (
            _requirement(
                "directors",
                "Entity Formation & Authorization",
                "Director listing",
                False,
                "FCC guide",
                "This entity type is governed by a board of directors.",
                [],
            ),
            account.entity_type in ("corporation", "charity", "condo"),
        ),
        (
            _requirement(
                "identity",
                "Persons to Identify",
                "Identity verification for signers and controllers",
                False,
                "FINTRAC; Compliance approval required",
                "Persons with ≥25% ownership, control or signing authority must be identified.",
                ids(identify),
            ),
            len(people) > 0,
        ),
        (
            _requirement(
                "pep",
                "Persons to Identify",
                "PEP / HIO enhanced review",
                True,
                "FINTRAC; Compliance approval required",
                "At least one person is flagged as PEP / HIO.",
                ids(peps),
            ),
            len(peps) > 0,
        ),
        (
            _requirement(
                "margin",
                "Account Features",
                "Margin agreement",
                True,
                "FCC guide §5.2",
                "Margin feature selected.",
                [],
            ),
            has_feature("MARGIN"),
        ),
        (
            _requirement(
                "options",
                "Account Features",
                "Options agreement and risk disclosure",
                True,
                "FCC guide §5.2",
                "Options feature selected.",
                [],
            ),
            has_feature("OPTIONS"),
        ),
        (
            _requirement(
                "cod-dvp",
                "Account Features",
                "COD / DVP settlement instructions",
                True,
                "FCC guide §5.2",
                "COD / DVP settlement selected.",
                [],
            ),
            has_feature("COD_DVP"),
        ),
        (
            _requirement(
                "fpl",
                "Account Features",
                "Fully Paid Lending agreement and risk disclosure",
                True,
                "FCC guide §5.2",
                "Fully paid securities lending selected.",
                [],
            ),
            has_feature("FPL"),
        ),
        (
            _requirement(
                "tcp",
                "Entity Formation & Authorization",
                "Trusted Contact Person form",
                True,
                "Compliance approval required",
                "A Trusted Contact Person was designated.",
                [],
            ),
            profile.trusted_contact if profile is not None else False,
        ),
        (
            _requirement(
                "w9",
                "IRS / Withholding Tax",
                "W-9",
                True,
                "FCC guide §5.2",
                "US tax residency or a US person in the structure.",
                ids(us_persons),
            ),
            tax == "US" or len(us_persons) > 0,
        ),
        (
            _requirement(
                "w8",
                "IRS / Withholding Tax",
                "W-8BEN-E or applicable treaty statement",
                True,
                "FCC guide §5.2",
                "International or mixed tax residency.",
                [],
            ),
            tax == "INTERNATIONAL" or tax == "MIXED",
        ),
        (
            _requirement(
                "rc519",
                "FATCA / CRS",
                "RC519 Declaration of Tax Residence",
                True,
                "FCC guide §5.2",
                "Tax residency is outside Canada.",
                [],
            ),
            tax == "US" or tax == "INTERNATIONAL" or tax == "MIXED",
        ),
        (
            _requirement(
                "nffe",
                "FATCA / CRS",
                "Passive NFFE controlling-person certification",
                True,
                "FCC guide",
                "International residency. If the entity is a passive NFFE, each controlling person at or above 25% certifies.",
                ids(identify),
            ),
            tax == "INTERNATIONAL",
        ),
    ]
    return [requirement for requirement, applies in rules if applies]


def _requirement(
    requirement_id: str,
    section: str,
    name: str,
    conditional: bool,
    source: str,
    reason: str,
    party_ids: list[str],
) -> Requirement:
    return Requirement(
        id=requirement_id,
        section=section,
        name=name,
        conditional=conditional,
        source=source,
        reason=reason,
        party_ids=party_ids,
    )
