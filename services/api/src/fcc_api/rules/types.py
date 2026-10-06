"""Dataclasses for the pure rules package."""

from dataclasses import dataclass, field


@dataclass
class Party:
    id: str
    parent_id: str | None
    kind: str
    legal_name: str
    ownership_percent: float
    is_controller: bool = False
    is_signing_authority: bool = False
    is_us_person: bool = False
    is_pep_hio: bool = False
    entity_type: str | None = None
    country: str | None = None
    title: str | None = None
    us_tax_class: str | None = None


@dataclass
class AccountProfile:
    province: str
    tax_residency: str
    features: list[str]
    trusted_contact: bool


@dataclass
class AccountCase:
    parties: list[Party]
    entity_type: str = "corporation"
    profile: AccountProfile | None = None
    id: str = ""
    version: int = 0
    legal_name: str = ""
    status: str = "BUILDING"
    rule_version: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ValidationIssue:
    code: str
    message: str
    party_id: str | None = None


@dataclass
class Requirement:
    id: str
    section: str
    name: str
    conditional: bool
    source: str
    reason: str
    party_ids: list[str] = field(default_factory=list)


@dataclass
class ChecklistRow(Requirement):
    custom: bool = False


@dataclass
class ProfileDraft:
    province: str
    tax_residency: str | None
    features: list[str]
    trusted_contact: bool | None
    trusted_contact_name: str


@dataclass
class ChecklistItemState:
    status: str
    document_ids: list[str] = field(default_factory=list)


@dataclass
class CustomRequirement:
    id: str
    name: str
    created_by: str = ""
    created_at: str = ""


@dataclass
class ReviewTask:
    id: str
    title: str
    done: bool
    source: str = "MANUAL"
    created_by: str = ""
    created_at: str = ""
    party_id: str | None = None
    requirement_id: str | None = None


@dataclass
class CaseRecord:
    id: str
    legal_name: str
    entity_type: str
    status: str
    parties: list[Party]
    profile: ProfileDraft
    rule_version: str
    version: int = 0
    created_at: str = ""
    updated_at: str = ""
    checklist: dict[str, ChecklistItemState] = field(default_factory=dict)
    custom_requirements: list[CustomRequirement] = field(default_factory=list)
    tasks: list[ReviewTask] = field(default_factory=list)


@dataclass
class RuleTrigger:
    kind: str
    entity_types: list[str] | None = None
    tax_residencies: list[str] | None = None
    feature: str | None = None


@dataclass
class LibraryRule:
    id: str
    name: str
    section: str
    conditional: bool
    source: str
    reason: str
    enabled: bool
    trigger: RuleTrigger


@dataclass
class BuiltinOverride:
    name: str
    section: str
    conditional: bool
    source: str
    reason: str


@dataclass
class RuleAdjustments:
    extras: list[LibraryRule] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    overrides: dict[str, BuiltinOverride] = field(default_factory=dict)
