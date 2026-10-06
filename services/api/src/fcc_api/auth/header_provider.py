"""本地请求头身份：`X-User-Id` 与 `X-User-Role`。"""

from __future__ import annotations

import os
from typing import Any

from fcc_api.auth.actor import (
    SIGN_IN_MESSAGE,
    ROLE_MISMATCH_MESSAGE,
    Actor,
    Role,
    fail_auth,
    record_value,
    role_name,
)

_KNOWN_ROLES = frozenset(role.value for role in Role)


def assert_auth_configuration(
    *,
    app_env: str | None = None,
    auth_mode: str | None = None,
) -> None:
    """生产环境只允许 `AUTH_MODE=entra`。`header` 在生产启动时拒绝。"""

    env = app_env if app_env is not None else os.environ.get("APP_ENV", "local")
    mode = auth_mode if auth_mode is not None else os.environ.get("AUTH_MODE", "header")
    if env == "production" and mode != "entra":
        raise SystemExit(
            f"Refusing to start: APP_ENV=production requires AUTH_MODE=entra, got {mode!r}."
        )


def authenticate_header_identity(
    user_id: str | None,
    role_header: str | None,
    user: Any,
    *,
    request_id: str | None = None,
) -> Actor:
    """校验两个身份头，并与 `users` 行对齐。

    缺头、用户不存在或 `active = false`：401，`message` 为 Sign in to continue.。
    角色头与 `users.role` 不一致：401，message 为
    Identity headers do not match a known user.。
    """

    cleaned_id = _clean(user_id)
    cleaned_role = _clean(role_header)
    if cleaned_id is None or cleaned_role is None:
        fail_auth(
            status_code=401,
            code="UNAUTHENTICATED",
            message=SIGN_IN_MESSAGE,
            details={},
            reason="MISSING_IDENTITY",
            user_id=_safe_user_id(cleaned_id),
            role=_safe_role(cleaned_role),
            request_id=request_id,
        )

    if user is None or record_value(user, "id") != cleaned_id:
        fail_auth(
            status_code=401,
            code="UNAUTHENTICATED",
            message=SIGN_IN_MESSAGE,
            details={},
            reason="UNKNOWN_USER",
            user_id=_safe_user_id(cleaned_id),
            role=_safe_role(cleaned_role),
            request_id=request_id,
        )

    if record_value(user, "active") is False:
        fail_auth(
            status_code=401,
            code="UNAUTHENTICATED",
            message=SIGN_IN_MESSAGE,
            details={},
            reason="INACTIVE_USER",
            user_id=_safe_user_id(cleaned_id),
            role=_safe_role(record_value(user, "role")),
            request_id=request_id,
        )

    stored_role = role_name(record_value(user, "role"))
    if stored_role is None or stored_role != cleaned_role:
        fail_auth(
            status_code=401,
            code="UNAUTHENTICATED",
            message=ROLE_MISMATCH_MESSAGE,
            details={},
            reason="ROLE_MISMATCH",
            user_id=_safe_user_id(cleaned_id),
            role=stored_role,
            request_id=request_id,
        )

    name = record_value(user, "name") or ""
    team = record_value(user, "team") or ""
    return Actor(id=cleaned_id, role=Role(stored_role), name=str(name), team=str(team))


def load_user(session: Any, user_id: str) -> Any:
    """按主键读取 `users`。会话还没有时返回 None。"""

    if session is None or not user_id:
        return None
    from fcc_api.db.models.users import User

    getter = getattr(session, "get", None)
    if getter is None:
        return None
    return getter(User, user_id)


def resolve_actor(request: Any, session: Any) -> Actor:
    """按 `AUTH_MODE` 解析调用者。`entra` 会抛出未配置错误。"""

    mode = os.environ.get("AUTH_MODE", "header").strip().lower()
    request_id = _request_id(request)
    if mode == "entra":
        from fcc_api.auth.entra_provider import authenticate_bearer

        authorization = _header(request, "authorization", "Authorization")
        return authenticate_bearer(authorization, session=session, request_id=request_id)
    if mode != "header":
        fail_auth(
            status_code=401,
            code="UNAUTHENTICATED",
            message=SIGN_IN_MESSAGE,
            details={},
            reason="UNSUPPORTED_AUTH_MODE",
            request_id=request_id,
        )

    user_id = _header(request, "x-user-id", "X-User-Id")
    role_header = _header(request, "x-user-role", "X-User-Role")
    user = None
    cleaned_id = _clean(user_id)
    if cleaned_id is not None:
        user = load_user(session, cleaned_id)
    return authenticate_header_identity(
        user_id,
        role_header,
        user,
        request_id=request_id,
    )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_user_id(value: str | None) -> str | None:
    if value is None or len(value) > 64 or any(character.isspace() for character in value):
        return None
    return value


def _safe_role(value: str | None) -> str | None:
    if value in _KNOWN_ROLES:
        return value
    return None


def _header(request: Any, *names: str) -> str | None:
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    for name in names:
        value = getter(name)
        if value is not None:
            return value
    return None


def _request_id(request: Any) -> str | None:
    state = getattr(request, "state", None)
    if state is not None:
        value = getattr(state, "request_id", None)
        if value:
            return str(value)
    header = _header(request, "x-request-id", "X-Request-Id")
    return str(header) if header else None
