"""复核任务的请求与响应。"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from fcc_api.schemas.common import ApiModel, JsDateTime, drop_none

_TASK_OPTIONAL = {"partyId", "party_id", "requirementId", "requirement_id"}


class TaskDraftIn(ApiModel):
    title: str = Field(min_length=1, max_length=500)
    party_id: str | None = None
    requirement_id: str | None = None


class AddTasksIn(ApiModel):
    version: int = Field(ge=1)
    source: str = Field(min_length=1, max_length=32)
    tasks: list[TaskDraftIn] = Field(min_length=1, max_length=50)


class ToggleTaskIn(ApiModel):
    version: int = Field(ge=1)


class CommentIn(ApiModel):
    title: str = Field(min_length=1, max_length=500)
    party_id: str | None = None
    requirement_id: str | None = None


class TaskOut(ApiModel):
    id: str
    title: str
    done: bool
    source: str
    created_by: str
    created_at: JsDateTime
    party_id: str | None = None
    requirement_id: str | None = None

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        payload = super().model_dump(**kwargs)
        return drop_none(payload, _TASK_OPTIONAL)
