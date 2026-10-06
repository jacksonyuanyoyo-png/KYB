"""`GET /api/v1/me` 与 `GET /api/v1/users`。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from fcc_api.auth.actor import Actor, record_value
from fcc_api.auth.dependencies import get_actor, get_db_session, register_auth_exception_handler

router = APIRouter(prefix="/api/v1", tags=["identity"])


def mount_identity(app: Any) -> None:
    """挂上当前用户路由，并注册权限异常处理。不要再加一层 `/api/v1` 前缀。"""

    register_auth_exception_handler(app)
    app.include_router(router)


@router.get("/me")
def read_me(
    actor: Annotated[Actor, Depends(get_actor)],
    session: Annotated[Any, Depends(get_db_session)],
) -> dict[str, str]:
    user = _load_user(session, actor.id)
    if user is None:
        from fcc_api.auth.actor import SIGN_IN_MESSAGE, fail_auth

        fail_auth(
            status_code=401,
            code="UNAUTHENTICATED",
            message=SIGN_IN_MESSAGE,
            details={},
            reason="UNKNOWN_USER",
            user_id=actor.id,
            role=actor.role.value,
        )
    return _user_payload(user)


@router.get("/users")
def list_users(
    _actor: Annotated[Actor, Depends(get_actor)],
    session: Annotated[Any, Depends(get_db_session)],
) -> list[dict[str, str]]:
    from sqlalchemy import select

    model = _user_model()
    statement = select(model).where(model.active.is_(True)).order_by(model.name, model.id)
    return [_user_payload(row) for row in session.scalars(statement)]


def _user_model() -> Any:
    from fcc_api.db.models.users import User

    return User


def _load_user(session: Any, user_id: str) -> Any:
    getter = getattr(session, "get", None)
    if getter is None:
        return None
    return getter(_user_model(), user_id)


def _user_payload(user: Any) -> dict[str, str]:
    role = record_value(user, "role")
    role_text = role.value if hasattr(role, "value") else str(role)
    return {
        "id": str(record_value(user, "id")),
        "name": str(record_value(user, "name") or ""),
        "email": str(record_value(user, "email") or ""),
        "role": role_text,
        "team": str(record_value(user, "team") or ""),
    }
