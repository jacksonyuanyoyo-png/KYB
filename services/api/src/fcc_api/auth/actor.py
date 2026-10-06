"""请求调用者，以及 401/403 使用的失败类型。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NoReturn

from fcc_api.errors import ApiError

logger = logging.getLogger("fcc_api.auth")

SIGN_IN_MESSAGE = "Sign in to continue."
ROLE_MISMATCH_MESSAGE = "Identity headers do not match a known user."


class Role(StrEnum):
    ADVISOR = "ADVISOR"
    OPERATIONS = "OPERATIONS"
    COMPLIANCE = "COMPLIANCE"
    ADMIN = "ADMIN"


@dataclass(frozen=True, slots=True)
class Actor:
    """通过身份校验后注入 service 的调用者。"""

    id: str
    role: Role
    name: str
    team: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role):
            object.__setattr__(self, "role", Role(self.role))


class AuthFailure(ApiError):
    """身份或权限失败。继承 `ApiError`，交给应用里已有的异常处理。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> None:
        super().__init__(status_code, code, message, details)
        self.request_id = request_id

    def body(self, request_id: str | None = None) -> dict[str, Any]:
        resolved = self.request_id if request_id is None else request_id
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
            "requestId": resolved or "",
        }


def record_value(row: Any, *names: str) -> Any:
    """读取对象属性或映射字段。名字按顺序，命中第一个存在的键。"""

    if row is None:
        return None
    if isinstance(row, Mapping):
        for name in names:
            if name in row:
                return row[name]
        return None
    for name in names:
        if hasattr(row, name):
            return getattr(row, name)
    return None


def role_name(role: Role | str | None) -> str | None:
    if role is None:
        return None
    if isinstance(role, Role):
        return role.value
    text = str(role)
    if text in Role._value2member_map_:
        return text
    return None


def fail_auth(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    reason: str,
    operation: str | None = None,
    case_id: str | None = None,
    user_id: str | None = None,
    role: str | None = None,
    request_id: str | None = None,
) -> NoReturn:
    """401 与 403 写一条结构化日志后抛出。日志不含请求体和令牌。"""

    if status_code in (401, 403):
        logger.warning(
            "authorization_failure",
            extra={
                "event": "authorization_failure",
                "request_id": request_id,
                "user_id": user_id,
                "role": role,
                "operation": operation,
                "case_id": case_id,
                "reason": reason,
            },
        )
    raise AuthFailure(
        status_code,
        code,
        message,
        dict(details or {}),
        request_id=request_id,
    )
