"""Coded demonstrator constants. Values match packages/domain/src/index.ts."""

RULE_VERSION = "demo-2026-10-04"
BENEFICIAL_OWNER_THRESHOLD = 25

ENTITY_TYPES: tuple[str, ...] = (
    "corporation",
    "charity",
    "trust",
    "ipp_rca",
    "partnership",
    "estate",
    "condo",
    "pooled_fund",
    "association",
    "first_nation",
)

ACCOUNT_FEATURES: tuple[str, ...] = ("MARGIN", "OPTIONS", "COD_DVP", "FPL")

US_TAX_CLASSES: tuple[str, ...] = ("complex", "simple", "unsure")
