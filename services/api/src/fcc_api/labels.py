from typing import Literal

Tone = Literal["neutral", "blue", "amber", "green", "red", "violet"]

# 文案从 apps/web/lib/labels.ts 移植，供审计 changes 使用。不要改前端文件。

ENTITY_LABELS: dict[str, str] = {
    "corporation": "Corporation",
    "charity": "Non-Profit / Charity / Foundation",
    "trust": "Formal Trust",
    "ipp_rca": "IPP / RCA",
    "partnership": "Partnership / LP",
    "estate": "Estate",
    "condo": "Condo Corporation",
    "pooled_fund": "Pooled Fund",
    "association": "Association / Union",
    "first_nation": "First Nation / Indigenous Government",
}

ENTITY_DESCRIPTIONS: dict[str, str] = {
    "corporation": "Federally or provincially incorporated company, including holding companies.",
    "charity": "Registered charity, foundation or not-for-profit organization.",
    "trust": "Trust with a written trust deed, trustees and beneficiaries.",
    "ipp_rca": "Individual Pension Plan or Retirement Compensation Arrangement.",
    "partnership": "General or limited partnership acting through a general partner.",
    "estate": "Estate of a deceased person administered by an executor.",
    "condo": "Condominium corporation governed by a board of directors.",
    "pooled_fund": "Investment fund pooling assets from multiple investors.",
    "association": "Unincorporated association, union or club.",
    "first_nation": "Band council or Indigenous governing body, treated as a public body.",
}

STATUS_LABELS: dict[str, str] = {
    "BUILDING": "Building",
    "DOCS_REQUESTED": "Docs requested",
    "READY_FOR_COMPLIANCE": "Ready for compliance",
    "RETURNED": "Returned",
    "APPROVED": "Approved",
}

STATUS_TONES: dict[str, Tone] = {
    "BUILDING": "neutral",
    "DOCS_REQUESTED": "amber",
    "READY_FOR_COMPLIANCE": "blue",
    "RETURNED": "red",
    "APPROVED": "green",
}

TAX_LABELS: dict[str, dict[str, str]] = {
    "CANADA": {"label": "Canada", "description": "Resident in Canada only."},
    "US": {"label": "United States", "description": "US entity or US tax resident."},
    "INTERNATIONAL": {
        "label": "International",
        "description": "Resident outside Canada and the US.",
    },
    "MIXED": {"label": "Mixed", "description": "Multiple residencies, including Canada."},
}

FEATURE_LABELS: dict[str, dict[str, str]] = {
    "MARGIN": {"label": "Margin", "description": "Borrow against eligible securities."},
    "OPTIONS": {"label": "Options", "description": "Trade listed options; risk disclosure required."},
    "COD_DVP": {"label": "COD / DVP", "description": "Cash or delivery versus payment settlement."},
    "FPL": {"label": "Fully Paid Lending", "description": "Lend fully paid securities for a fee."},
}

DOC_STATUS_LABELS: dict[str, str] = {
    "MISSING": "Missing",
    "REQUESTED": "Requested",
    "RECEIVED": "Received",
    "VERIFIED": "Verified",
    "REJECTED": "Rejected",
}

DOC_STATUS_TONES: dict[str, Tone] = {
    "MISSING": "neutral",
    "REQUESTED": "amber",
    "RECEIVED": "blue",
    "VERIFIED": "green",
    "REJECTED": "red",
}

ROLE_LABELS: dict[str, str] = {
    "ADVISOR": "WI/PI Advisor",
    "OPERATIONS": "Operations",
    "COMPLIANCE": "Compliance",
    "ADMIN": "Admin",
}

PROVINCES: list[dict[str, str]] = [
    {"code": "AB", "name": "Alberta"},
    {"code": "BC", "name": "British Columbia"},
    {"code": "MB", "name": "Manitoba"},
    {"code": "NB", "name": "New Brunswick"},
    {"code": "NL", "name": "Newfoundland and Labrador"},
    {"code": "NS", "name": "Nova Scotia"},
    {"code": "NT", "name": "Northwest Territories"},
    {"code": "NU", "name": "Nunavut"},
    {"code": "ON", "name": "Ontario"},
    {"code": "PE", "name": "Prince Edward Island"},
    {"code": "QC", "name": "Quebec"},
    {"code": "SK", "name": "Saskatchewan"},
    {"code": "YT", "name": "Yukon"},
]

PARTY_TITLES: list[str] = [
    "Director",
    "Officer",
    "Trustee",
    "Executor",
    "General Partner",
    "Authorized Signatory",
    "Chief",
    "Councillor",
    "Beneficiary",
    "Shareholder",
]

RESIDENCE_COUNTRIES: tuple[str, ...] = (
    "Canada",
    "United States",
    "United Kingdom",
    "Australia",
    "Other",
)

US_TAX_LABELS: dict[str, dict[str, str]] = {
    "complex": {"label": "Complex", "hint": "Taxed at the entity level — W-8BEN-E."},
    "simple": {"label": "Simple / Grantor", "hint": "Flow-through — W-8IMY plus a withholding statement."},
    "unsure": {"label": "Not sure", "hint": "Both forms stay listed until this is confirmed."},
}

ENTITY_GUIDANCE: dict[str, str] = {
    "corporation": "Add every person owning 25% or more, plus all authorized signing officers. If a shareholder is itself a corporation, add it as an entity and look through it.",
    "charity": "Add all directors and authorized officers, and any person owning 25% or more. Look through any entity owners.",
    "trust": "Add all trustees (they control the trust), all beneficiaries, the settlor, and any protector. Give each person a role, and look through any entity party.",
    "ipp_rca": "Add all trustees. If a trustee is a corporation, add it as an entity and identify who controls that corporation.",
    "partnership": "Add all partners and members. Record their ownership percentages so they total 100%, and look through any entity partners.",
    "estate": "Add all executors (they have signing authority) and all beneficiaries. Look through any entity executor.",
    "condo": "Add authorized signing officers and the people who sign the strata or condo council resolution.",
    "pooled_fund": "Add the general partner, fund manager, or trustee — whoever controls the fund — then look through to the controlling natural persons.",
    "association": "Add all authorized signing officers. Look through any entity controller.",
    "first_nation": "Add the Chief and Council (or the equivalent governing body) and all authorized signing officers. Control decides who is identified, not an ownership percentage. Look-through stops at this governing body.",
}

NFFE_GUIDANCE = "A non-Canadian entity that is not a financial institution is an NFFE. It is passive when 50% or more of its income is passive, or 50% or more of its assets produce passive income. Each controlling person at or above 25% then completes an RC519 certification. An operating business under those thresholds is an active NFFE."
