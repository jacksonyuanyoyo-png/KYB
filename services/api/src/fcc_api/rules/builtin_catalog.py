"""Display metadata for the 16 builtin rules.

Copied from BUILTIN in apps/web/app/(workspace)/rules/page.tsx. These strings
are the rules-page labels. Checklist text comes from fcc_api.rules.domain.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinRule:
    id: str
    name: str
    section: str
    trigger: str
    source: str
    conditional: bool


BUILTIN: tuple[BuiltinRule, ...] = (
    BuiltinRule(
        "naaf",
        "New Account Application Form (NAAF)",
        "Entity Formation & Authorization",
        "Always",
        "FCC guide §5.2",
        False,
    ),
    BuiltinRule(
        "formation",
        "Articles / formation or governing document",
        "Entity Formation & Authorization",
        "Always",
        "FCC guide §3",
        False,
    ),
    BuiltinRule(
        "resolution",
        "Corporate resolution / signing authority evidence",
        "Entity Formation & Authorization",
        "Corporation, Condo, Charity, Association, First Nation",
        "FCC guide §5.2",
        False,
    ),
    BuiltinRule(
        "beneficial-owner",
        "Beneficial Owner Identification",
        "Entity Formation & Authorization",
        "Corporation, Partnership, Pooled Fund, Trust, IPP/RCA",
        "CIRO 3203–3204",
        False,
    ),
    BuiltinRule(
        "directors",
        "Director listing",
        "Entity Formation & Authorization",
        "Corporation, Charity, Condo",
        "FCC guide",
        False,
    ),
    BuiltinRule(
        "tcp",
        "Trusted Contact Person form",
        "Entity Formation & Authorization",
        "Trusted Contact designated",
        "Compliance approval required",
        True,
    ),
    BuiltinRule(
        "identity",
        "Identity verification for signers and controllers",
        "Persons to Identify",
        "Any natural person",
        "FINTRAC",
        False,
    ),
    BuiltinRule(
        "pep",
        "PEP / HIO enhanced review",
        "Persons to Identify",
        "Any person flagged PEP / HIO",
        "FINTRAC",
        True,
    ),
    BuiltinRule(
        "margin", "Margin agreement", "Account Features", "Margin selected", "FCC guide §5.2", True
    ),
    BuiltinRule(
        "options",
        "Options agreement and risk disclosure",
        "Account Features",
        "Options selected",
        "FCC guide §5.2",
        True,
    ),
    BuiltinRule(
        "cod-dvp",
        "COD / DVP settlement instructions",
        "Account Features",
        "COD / DVP selected",
        "FCC guide §5.2",
        True,
    ),
    BuiltinRule(
        "fpl",
        "Fully Paid Lending agreement and risk disclosure",
        "Account Features",
        "Fully Paid Lending selected",
        "FCC guide §5.2",
        True,
    ),
    BuiltinRule(
        "w9", "W-9", "IRS / Withholding Tax", "US residency or a US person", "FCC guide §5.2", True
    ),
    BuiltinRule(
        "w8",
        "W-8BEN-E or applicable treaty statement",
        "IRS / Withholding Tax",
        "International or mixed residency",
        "FCC guide §5.2",
        True,
    ),
    BuiltinRule(
        "rc519",
        "RC519 Declaration of Tax Residence",
        "FATCA / CRS",
        "US, international, or mixed residency",
        "FCC guide §5.2",
        True,
    ),
    BuiltinRule(
        "nffe",
        "Passive NFFE controlling-person certification",
        "FATCA / CRS",
        "International residency",
        "FCC guide",
        True,
    ),
)
