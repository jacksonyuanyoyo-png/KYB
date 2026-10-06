"""管理接口。季度访问复核只返回启用用户，不含案件内容。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from fcc_api.auth.actor import Actor, Role, fail_auth, record_value
from fcc_api.auth.dependencies import get_actor, get_db_session

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

ACCESS_REVIEW = "access_review"


@router.get("/access-review")
def read_access_review(
    request: Request,
    actor: Annotated[Actor, Depends(get_actor)],
    session: Annotated[Any, Depends(get_db_session)],
) -> JSONResponse:
    require_access_review(actor, request_id=_request_id(request))
    rows = list_access_review(session)
    return JSONResponse(content=rows, headers={"X-Export-Rows": str(len(rows))})


def require_access_review(actor: Actor, *, request_id: str | None = None) -> None:
    """走 policy 里的 access_review。策略还没有这个操作时，只放行 ADMIN。"""

    from fcc_api.auth.policy import canonical_operation, require_operation

    try:
        canonical_operation(ACCESS_REVIEW)
    except ValueError:
        if actor.role != Role.ADMIN:
            fail_auth(
                status_code=403,
                code="FORBIDDEN",
                message="Only an admin can review access.",
                details={"reason": "ROLE", "role": actor.role.value, "operation": ACCESS_REVIEW},
                reason="ROLE",
                operation=ACCESS_REVIEW,
                user_id=actor.id,
                role=actor.role.value,
                request_id=request_id,
            )
        return
    require_operation(actor, ACCESS_REVIEW, request_id=request_id)


def list_access_review(session: Any) -> list[dict[str, Any]]:
    """启用用户的身份字段。`session.list_active_users` 存在时供测试代替查询。"""

    users = [user for user in _load_users(session) if _is_active(user)]
    users.sort(
        key=lambda user: (
            str(record_value(user, "name") or ""),
            str(record_value(user, "id") or ""),
        )
    )
    return [_review_row(user) for user in users]


def _load_users(session: Any) -> list[Any]:
    hook = getattr(session, "list_active_users", None)
    if callable(hook):
        return list(hook())
    from sqlalchemy import select

    from fcc_api.db.models.users import User

    statement = select(User).where(User.active.is_(True)).order_by(User.name, User.id)
    return list(session.scalars(statement).all())


def _review_row(user: Any) -> dict[str, Any]:
    role = record_value(user, "role")
    role_text = role.value if hasattr(role, "value") else str(role or "")
    return {
        "id": str(record_value(user, "id") or ""),
        "name": str(record_value(user, "name") or ""),
        "email": str(record_value(user, "email") or ""),
        "role": role_text,
        "lastLoginAt": _timestamp(record_value(user, "last_login_at", "lastLoginAt")),
        "deactivatedAt": _timestamp(record_value(user, "deactivated_at", "deactivatedAt")),
    }


def _is_active(user: Any) -> bool:
    return record_value(user, "active") is True


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_id(request: Request) -> str | None:
    state = getattr(request, "state", None)
    value = getattr(state, "request_id", None) if state is not None else None
    if value:
        return str(value)
    header = request.headers.get("x-request-id")
    return str(header) if header else None
