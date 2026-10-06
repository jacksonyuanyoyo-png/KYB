import importlib.util
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fcc_api.config import Settings, ensure_process_may_start, get_settings
from fcc_api.errors import ApiError, code_for_status, error_body, validation_error_body
from fcc_api.ids import new_id
from fcc_api.logging import configure_logging

logger = logging.getLogger("fcc_api")

ROUTER_MODULES = (
    "fcc_api.api.routers.health",
    "fcc_api.api.routers.me",
    "fcc_api.api.routers.cases",
    "fcc_api.api.routers.parties",
    "fcc_api.api.routers.profile",
    "fcc_api.api.routers.checklist",
    "fcc_api.api.routers.documents",
    "fcc_api.api.routers.local_storage",
    "fcc_api.api.routers.tasks",
    "fcc_api.api.routers.compliance",
    "fcc_api.api.routers.entities",
    "fcc_api.api.routers.audit",
    "fcc_api.api.routers.rule_library",
    "fcc_api.api.routers.admin",
    "fcc_api.api.routers.approvals",
    "fcc_api.api.routers.legal_holds",
    "fcc_api.api.routers.screening",
    "fcc_api.api.routers.ai",
    "fcc_api.api.routers.forms",
    "fcc_api.api.routers.submissions",
    "fcc_api.api.routers.portal",
)


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = new_id("req")
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = request_id
        status_code = 0

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)
        logger.info("%s %s %s", scope.get("method", ""), scope.get("path", ""), status_code)


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", "")
    if isinstance(request_id, str) and request_id:
        return request_id
    return new_id("req")


def include_existing_routers(app: FastAPI, settings: Settings) -> None:
    for module_name in ROUTER_MODULES:
        if module_name == "fcc_api.api.routers.local_storage" and settings.storage_backend != "local":
            continue
        try:
            spec = importlib.util.find_spec(module_name)
        except ModuleNotFoundError:
            continue
        if spec is None:
            continue
        module = importlib.import_module(module_name)
        if module_name == "fcc_api.api.routers.me":
            mount_identity = getattr(module, "mount_identity", None)
            if callable(mount_identity):
                mount_identity(app)
                continue
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details, _request_id(request)),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=validation_error_body(exc.errors(), _request_id(request)),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        response = JSONResponse(
            status_code=exc.status_code,
            content=error_body(code_for_status(exc.status_code), message, {}, _request_id(request)),
        )
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Something went wrong.", {}, _request_id(request)),
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings if settings is not None else get_settings()
    ensure_process_may_start(resolved)
    configure_logging(resolved.log_level)
    show_docs = resolved.app_env != "production"
    app = FastAPI(
        title="fcc-kyb-api",
        docs_url="/api/docs" if show_docs else None,
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = resolved
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    register_exception_handlers(app)
    include_existing_routers(app, resolved)
    return app


app = create_app()

# 启动 API 时必须关闭 uvicorn 默认 access log：
#   uv run uvicorn fcc_api.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log
# 默认访问日志会打印完整 URL，预签名查询参数（sig、expires）会进日志。
# 见 docs/backend/09-local-development.md 第 7 节与 docs/backend/05-documents-and-files.md 第 7 节。
# 这里显式 access_log=False，进程内不要打开 access log。
if __name__ == "__main__":
    import uvicorn

    runtime = get_settings()
    uvicorn.run(
        "fcc_api.main:app",
        host=runtime.api_host,
        port=runtime.api_port,
        access_log=False,
        reload=runtime.app_env == "local",
    )
