"""Pydantic 基类：JSON camelCase，时间与 JavaScript toISOString 一致。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel


def to_js_iso(value: datetime) -> str:
    """`Date.prototype.toISOString`：UTC，毫秒三位，后缀 Z。"""

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    millis = value.microsecond // 1000
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{millis:03d}Z"


def js_number(value: object) -> int | float:
    """整数不带小数，与 JavaScript JSON 数字一致。"""

    number = float(value)  # type: ignore[arg-type]
    if number.is_integer():
        return int(number)
    return number


JsDateTime = Annotated[
    datetime,
    PlainSerializer(to_js_iso, return_type=str, when_used="json"),
]


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


def drop_none(data: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key not in keys or value is not None}
