"""Rule library request and response models. JSON keys are camelCase."""

from __future__ import annotations

from typing import Any, Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator
from pydantic.alias_generators import to_camel

ENTITY_TYPES = (
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
TAX_RESIDENCIES = ("CANADA", "US", "INTERNATIONAL", "MIXED")
ACCOUNT_FEATURES = ("MARGIN", "OPTIONS", "COD_DVP", "FPL")
SECTIONS = (
    "Entity Formation & Authorization",
    "Persons to Identify",
    "Account Features",
    "IRS / Withholding Tax",
    "FATCA / CRS",
)
TRIGGER_KINDS = (
    "ALWAYS",
    "ENTITY",
    "TAX",
    "FEATURE",
    "PERSON",
    "PEP",
    "US_PERSON",
    "TRUSTED_CONTACT",
)
RULE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
API_ERROR_CODES = frozenset(
    {
        "UNAUTHENTICATED",
        "FORBIDDEN",
        "NOT_FOUND",
        "VERSION_CONFLICT",
        "GATE_FAILED",
        "VALIDATION_FAILED",
    }
)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class RuleTriggerIn(ApiModel):
    kind: Literal[
        "ALWAYS",
        "ENTITY",
        "TAX",
        "FEATURE",
        "PERSON",
        "PEP",
        "US_PERSON",
        "TRUSTED_CONTACT",
    ]
    entity_types: list[str] | None = None
    tax_residencies: list[str] | None = None
    feature: str | None = None

    @model_validator(mode="after")
    def fields_match_kind(self) -> RuleTriggerIn:
        problems = trigger_problems(self.kind, self.entity_types, self.tax_residencies, self.feature)
        if problems:
            raise ValueError(problems[0])
        return self


class LibraryRuleIn(ApiModel):
    id: str = Field(pattern=RULE_ID_PATTERN)
    name: str
    section: str
    conditional: bool
    source: str = ""
    reason: str = ""
    enabled: bool
    trigger: RuleTriggerIn


class BuiltinOverrideIn(ApiModel):
    name: str
    section: str
    conditional: bool
    source: str
    reason: str


class VersionBody(ApiModel):
    version: int = Field(ge=1)


class SaveExtraBody(ApiModel):
    version: int = Field(ge=1)
    rule: LibraryRuleIn


class RetireBuiltinBody(ApiModel):
    version: int = Field(ge=1)
    retired: bool


class OverrideBuiltinBody(ApiModel):
    version: int = Field(ge=1)
    override: BuiltinOverrideIn


class PublishBody(ApiModel):
    version: int = Field(ge=1)
    approval_ids: list[str] | None = None


class EvaluateAccountIn(ApiModel):
    entity_type: str
    tax_residency: str
    features: list[str] = Field(default_factory=list)
    trusted_contact: bool = False
    us_person: bool = False
    pep: bool = False

    @field_validator("entity_type")
    @classmethod
    def known_entity(cls, value: str) -> str:
        if value not in ENTITY_TYPES:
            raise ValueError("entityType is not a known entity type")
        return value

    @field_validator("tax_residency")
    @classmethod
    def known_tax(cls, value: str) -> str:
        if value not in TAX_RESIDENCIES:
            raise ValueError("taxResidency is not a known tax residency")
        return value

    @field_validator("features")
    @classmethod
    def known_features(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("features must not contain duplicates")
        unknown = [item for item in value if item not in ACCOUNT_FEATURES]
        if unknown:
            raise ValueError("features contains an unknown account feature")
        return value


class EvaluateBody(ApiModel):
    target: Literal["PUBLISHED", "DRAFT"] = "PUBLISHED"
    account: EvaluateAccountIn = Field(alias="input")


class RuleTriggerOut(ApiModel):
    kind: str
    entity_types: list[str] | None = None
    tax_residencies: list[str] | None = None
    feature: str | None = None

    @model_serializer(mode="wrap")
    def drop_empty_trigger_fields(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        return {key: value for key, value in data.items() if value is not None}


class LibraryRuleOut(ApiModel):
    id: str
    name: str
    section: str
    conditional: bool
    source: str
    reason: str
    enabled: bool
    trigger: RuleTriggerOut


class BuiltinOverrideOut(ApiModel):
    name: str
    section: str
    conditional: bool
    source: str
    reason: str


class RuleDraftOut(ApiModel):
    version: str
    extras: list[LibraryRuleOut]
    disabled: list[str]
    overrides: dict[str, BuiltinOverrideOut]


class BuiltinRuleOut(ApiModel):
    id: str
    name: str
    section: str
    trigger: str
    source: str
    conditional: bool


class RuleLibraryResponse(ApiModel):
    version: int
    published_version: str
    published_extras: list[LibraryRuleOut]
    published_disabled: list[str]
    published_overrides: dict[str, BuiltinOverrideOut]
    draft: RuleDraftOut | None
    pinned_case_count: int
    builtins: list[BuiltinRuleOut]


class RequirementOut(ApiModel):
    id: str
    section: str
    name: str
    conditional: bool
    source: str
    reason: str
    party_ids: list[str]


class EvaluateResponse(ApiModel):
    rule_version: str
    requirements: list[RequirementOut]


def trigger_problems(
    kind: str,
    entity_types: list[str] | None,
    tax_residencies: list[str] | None,
    feature: str | None,
) -> list[str]:
    """Return human-readable trigger problems. None means the field was omitted."""
    problems: list[str] = []
    if kind not in TRIGGER_KINDS:
        return ["trigger.kind is not a known trigger"]
    if kind == "ENTITY":
        if not entity_types:
            problems.append("ENTITY triggers need a non-empty entityTypes list")
        elif any(item not in ENTITY_TYPES for item in entity_types):
            problems.append("entityTypes contains an unknown entity type")
        if tax_residencies is not None:
            problems.append("ENTITY triggers cannot include taxResidencies")
        if feature is not None:
            problems.append("ENTITY triggers cannot include feature")
    elif kind == "TAX":
        if not tax_residencies:
            problems.append("TAX triggers need a non-empty taxResidencies list")
        elif any(item not in TAX_RESIDENCIES for item in tax_residencies):
            problems.append("taxResidencies contains an unknown tax residency")
        if entity_types is not None:
            problems.append("TAX triggers cannot include entityTypes")
        if feature is not None:
            problems.append("TAX triggers cannot include feature")
    elif kind == "FEATURE":
        if feature is None:
            problems.append("FEATURE triggers need feature")
        elif feature not in ACCOUNT_FEATURES:
            problems.append("feature is not a known account feature")
        if entity_types is not None:
            problems.append("FEATURE triggers cannot include entityTypes")
        if tax_residencies is not None:
            problems.append("FEATURE triggers cannot include taxResidencies")
    else:
        if entity_types is not None:
            problems.append(f"{kind} triggers cannot include entityTypes")
        if tax_residencies is not None:
            problems.append(f"{kind} triggers cannot include taxResidencies")
        if feature is not None:
            problems.append(f"{kind} triggers cannot include feature")
    return problems


def error_response(exc: Exception, request_id: str) -> JSONResponse | None:
    """Render a service ApiError into the contract envelope, when it is one."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if code not in API_ERROR_CODES or not isinstance(status, int):
        return None
    message = getattr(exc, "message", None) or str(exc)
    details: Any = getattr(exc, "details", None) or {}
    if not isinstance(details, dict):
        details = {}
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message, "details": details},
            "requestId": request_id,
        },
    )


def request_id_of(request: Any) -> str:
    state = getattr(request, "state", None)
    existing = getattr(state, "request_id", None) if state is not None else None
    if existing:
        return str(existing)
    headers = getattr(request, "headers", None)
    if headers is not None:
        header = headers.get("X-Request-Id")
        if header:
            return str(header)
    return "req_local"


def correlation_id_of(request: Any, request_id: str) -> str:
    headers = getattr(request, "headers", None)
    if headers is not None:
        header = headers.get("X-Correlation-Id")
        if header:
            return str(header)
    return request_id
