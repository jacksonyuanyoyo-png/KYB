"""FastAPI 依赖：解析 Actor，并把权限失败写成统一错误外形。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from fcc_api.auth.actor import Actor, AuthFailure
from fcc_api.auth.header_provider import resolve_actor


def get_db_session() -> Iterator[Any]:
    """与 `fcc_api.api.deps.get_db` 共用同一个会话依赖。"""

    from fcc_api.api.deps import get_db

    yield from get_db()


def get_actor(
    request: Request,
    session: Annotated[Any, Depends(get_db_session)],
) -> Actor:
    return resolve_actor(request, session)


def register_auth_exception_handler(app: Any) -> None:
    """在 `create_app` 里调用一次，使 401/403/404 使用契约里的 JSON。"""

    @app.exception_handler(AuthFailure)
    async def handle_auth_failure(request: Request, exc: AuthFailure) -> JSONResponse:
        request_id = exc.request_id
        if not request_id:
            request_id = getattr(request.state, "request_id", None) or request.headers.get("x-request-id")
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.body(None if request_id is None else str(request_id)),
        )
