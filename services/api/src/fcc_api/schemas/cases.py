"""案件详情与建案、状态、合规决定的请求。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_serializer

from fcc_api.schemas.common import ApiModel, JsDateTime, drop_none
from fcc_api.schemas.parties import PartyOut
from fcc_api.schemas.tasks import TaskOut

_ISSUE_OPTIONAL = {"partyId", "party_id"}


class AiEntityTypeIn(ApiModel):
    suggested: str
    accepted: bool


class CreateCaseIn(ApiModel):
    legal_name: str
    entity_type: str
    jurisdiction: str = ""
    registration_number: str = ""
    owner_id: str
    ai_entity_type: AiEntityTypeIn | None = None


class ProfileIn(ApiModel):
    province: str = ""
    tax_residency: Literal["CANADA", "US", "INTERNATIONAL", "MIXED"] | None = None
    features: list[str] = Field(default_factory=list)
    trusted_contact: bool | None = None
    trusted_contact_name: str = ""


class ProfileChangeIn(ApiModel):
    field: str = Field(min_length=1, max_length=200)
    from_: str = Field(alias="from", max_length=500)
    to: str = Field(max_length=500)


class UpdateProfileIn(ApiModel):
    version: int = Field(ge=1)
    profile: ProfileIn
    change: ProfileChangeIn


class ChecklistStatusIn(ApiModel):
    version: int = Field(ge=1)
    status: Literal["MISSING", "REQUESTED", "RECEIVED", "VERIFIED", "REJECTED"]


class CustomRequirementIn(ApiModel):
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)


class ChangeStatusIn(ApiModel):
    version: int = Field(ge=1)
    status: str
    summary: str = Field(min_length=1, max_length=500)


class ComplianceCommentIn(ApiModel):
    title: str = Field(min_length=1, max_length=500)
    party_id: str | None = None
    requirement_id: str | None = None


class ComplianceDecisionIn(ApiModel):
    version: int = Field(ge=1)
    decision: Literal["APPROVE", "RETURN"]
    comments: list[ComplianceCommentIn] = Field(default_factory=list)


class ProfileOut(ApiModel):
    province: str
    tax_residency: str | None
    features: list[str]
    trusted_contact: bool | None
    trusted_contact_name: str


class ChecklistItemOut(ApiModel):
    status: str
    document_ids: list[str]
    updated_at: JsDateTime | None = None
    updated_by: str | None = None


class CustomRequirementOut(ApiModel):
    id: str
    name: str
    created_by: str
    created_at: JsDateTime


class DocumentOut(ApiModel):
    id: str
    requirement_id: str | None
    file_name: str
    size_bytes: int
    mime_type: str
    uploaded_by: str
    uploaded_at: JsDateTime
    extraction: str
    sha256: str | None = None
    storage_state: str
    scan_status: str


class OwnershipIssueOut(ApiModel):
    code: str
    message: str
    party_id: str | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> dict[str, Any]:
        return drop_none(handler(self), _ISSUE_OPTIONAL)


class DetailsGapOut(ApiModel):
    field: str
    message: str


class ChecklistRowOut(ApiModel):
    id: str
    section: str
    name: str
    conditional: bool
    source: str
    reason: str
    party_ids: list[str]
    custom: bool


class InsightOut(ApiModel):
    ownership_issues: list[OwnershipIssueOut]
    details_gaps: list[DetailsGapOut]
    checklist: list[ChecklistRowOut]
    collected: int
    stage: str
    blocker: str | None
    identify: list[str]
    effective: dict[str, int | float]
    open_tasks: int


class CaseOut(ApiModel):
    id: str
    reference: str
    version: int
    legal_name: str
    entity_type: str
    status: str
    owner_id: str
    jurisdiction: str
    registration_number: str
    rule_version: str
    created_at: JsDateTime
    updated_at: JsDateTime
    due_date: JsDateTime
    submitted_at: JsDateTime | None
    parties: list[PartyOut]
    profile: ProfileOut
    checklist: dict[str, ChecklistItemOut]
    custom_requirements: list[CustomRequirementOut]
    documents: list[DocumentOut]
    tasks: list[TaskOut]


class CaseDetailResponse(ApiModel):
    case: CaseOut
    insight: InsightOut
