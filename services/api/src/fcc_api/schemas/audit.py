"""Audit event responses. List payloads omit before_value and after_value."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, model_serializer
from pydantic.alias_generators import to_camel

from fcc_api.schemas.rules import ApiModel


class AuditChangeOut(ApiModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")
    field: str
    from_value: str = Field(alias="from", serialization_alias="from")
    to: str


class AuditAiOut(ApiModel):
    model: str
    accepted: bool | None = None
    decision: str
    rule_version: str


class AuditEventOut(ApiModel):
    """Frontend AuditEvent plus ruleVersion and correlationId.

    before_value and after_value stay in the database and are not fields here.
    """

    id: str
    case_id: str
    actor_id: str
    action: str
    summary: str
    at: str
    version: int
    changes: list[AuditChangeOut] | None = None
    ai: AuditAiOut | None = None
    rule_version: str | None = None
    correlation_id: str

    @model_serializer(mode="wrap")
    def omit_empty_optionals(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if data.get("changes") is None:
            data.pop("changes", None)
        if data.get("ai") is None:
            data.pop("ai", None)
        return data


class AuditListResponse(ApiModel):
    items: list[AuditEventOut]
    next_cursor: str | None = None


class AuditItemsResponse(ApiModel):
    items: list[AuditEventOut]
