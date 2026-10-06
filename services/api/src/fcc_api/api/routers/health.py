import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from fcc_api.db.session import engine

SERVICE_NAME = "fcc-kyb-api"
logger = logging.getLogger("fcc_api.health")

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """存活探针：不查数据库，不要求身份。"""
    return {"status": "ok", "service": SERVICE_NAME}


@router.get("/health/ready")
def health_ready() -> JSONResponse:
    """就绪探针：执行 SELECT 1。数据库不可用时返回 503，不要求身份。"""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("readiness check failed", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "service": SERVICE_NAME, "database": "error"},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "service": SERVICE_NAME, "database": "ok"},
    )
