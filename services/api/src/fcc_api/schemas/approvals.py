"""Rule approval requests and responses. JSON keys are camelCase."""

from __future__ import annotations

import re
from datetime import date

from pydantic import ConfigDict, Field, ValidationInfo, field_validator
from pydantic.alias_generators import to_camel

from fcc_api.schemas.common import ApiModel, JsDateTime

_RULE_ID = r"^[A-Za-z0-9_-]{1,64}$"
_TEST_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class ApprovalModel(ApiModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class SubmitRuleApprovalIn(ApprovalModel):
    rule_id: str = Field(pattern=_RULE_ID)
    builtin_version: str = Field(min_length=1, max_length=64)
    owner_name: str = Field(min_length=1, max_length=200)
    source_url: str = Field(min_length=1, max_length=500)
    effective_on: date
    review_due_on: date
    test_ids: list[str] = Field(min_length=1, max_length=50)
    approval_ticket: str = Field(min_length=1, max_length=200)

    @field_validator("builtin_version", "owner_name", "source_url", "approval_ticket")
    @classmethod
    def strip_required(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be blank")
        return text

    @field_validator("test_ids")
    @classmethod
    def clean_test_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text or len(text) > 64 or not _test_id_ok(text):
                raise ValueError("each test id must be 1–64 letters, digits, dots, underscores, or hyphens")
            cleaned.append(text)
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("testIds must not contain duplicates")
        return cleaned

    @field_validator("review_due_on")
    @classmethod
    def review_after_effective(cls, value: date, info: ValidationInfo) -> date:
        effective = info.data.get("effective_on")
        if isinstance(effective, date) and value <= effective:
            raise ValueError("must be later than effectiveOn")
        return value


class ApproveRuleApprovalIn(ApprovalModel):
    approval_ticket: str = Field(min_length=1, max_length=200)

    @field_validator("approval_ticket")
    @classmethod
    def strip_ticket(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be blank")
        return text


class RuleApprovalOut(ApprovalModel):
    id: str
    rule_id: str
    builtin_version: str
    owner_name: str
    source_url: str
    effective_on: date | None = None
    review_due_on: date | None = None
    test_ids: list[str]
    content_sha256: str | None = None
    approval_status: str
    submitted_by: str | None = None
    approved_by: str | None = None
    approval_ticket: str | None = None
    submitted_at: JsDateTime
    approved_at: JsDateTime | None = None


class RuleApprovalListOut(ApprovalModel):
    items: list[RuleApprovalOut]


def _test_id_ok(value: str) -> bool:
    return re.fullmatch(_TEST_ID, value) is not None
