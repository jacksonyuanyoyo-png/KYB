"""股权节点的请求与响应。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from fcc_api.schemas.common import ApiModel, drop_none

_PARTY_OPTIONAL = {"entityType", "entity_type", "country", "title", "usTaxClass", "us_tax_class"}


class PartyIn(ApiModel):
    id: str
    parent_id: str | None = None
    kind: Literal["ENTITY", "PERSON"]
    legal_name: str
    entity_type: str | None = None
    country: str | None = None
    title: str | None = None
    us_tax_class: str | None = None
    ownership_percent: float = Field(ge=0, le=100)
    is_controller: bool
    is_signing_authority: bool
    is_us_person: bool
    is_pep_hio: bool


class PartyOut(ApiModel):
    id: str
    parent_id: str | None
    kind: str
    legal_name: str
    ownership_percent: int | float
    is_controller: bool
    is_signing_authority: bool
    is_us_person: bool
    is_pep_hio: bool
    entity_type: str | None = None
    country: str | None = None
    title: str | None = None
    us_tax_class: str | None = None

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        payload = super().model_dump(**kwargs)
        return drop_none(payload, _PARTY_OPTIONAL)


class AuditChangeIn(ApiModel):
    field: str = Field(min_length=1, max_length=200)
    from_: str = Field(alias="from", max_length=500)
    to: str = Field(max_length=500)


class UpdatePartiesIn(ApiModel):
    version: int = Field(ge=1)
    parties: list[PartyIn] = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=500)
    changes: list[AuditChangeIn] | None = None


class ApplyAiPartiesIn(ApiModel):
    version: int = Field(ge=1)
    parties: list[PartyIn] = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)


class RejectAiIn(ApiModel):
    version: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
