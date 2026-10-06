from collections.abc import Mapping, Sequence
from typing import Any


UNAUTHENTICATED = "UNAUTHENTICATED"
FORBIDDEN = "FORBIDDEN"
NOT_FOUND = "NOT_FOUND"
VERSION_CONFLICT = "VERSION_CONFLICT"
GATE_FAILED = "GATE_FAILED"
VALIDATION_FAILED = "VALIDATION_FAILED"
UNAVAILABLE = "UNAVAILABLE"

_STATUS_TO_CODE = {
    400: VALIDATION_FAILED,
    401: UNAUTHENTICATED,
    403: FORBIDDEN,
    404: NOT_FOUND,
    409: VERSION_CONFLICT,
    422: GATE_FAILED,
    503: UNAVAILABLE,
}


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def error_body(code: str, message: str, details: dict[str, Any] | None, request_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
        "requestId": request_id,
    }


def code_for_status(status_code: int) -> str:
    return _STATUS_TO_CODE.get(status_code, "INTERNAL_ERROR")


def unauthenticated(
    message: str = "Sign in to continue.",
    details: dict[str, Any] | None = None,
) -> ApiError:
    return ApiError(401, UNAUTHENTICATED, message, details)


def forbidden(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(403, FORBIDDEN, message, details)


def not_found(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(404, NOT_FOUND, message, details)


def version_conflict(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(409, VERSION_CONFLICT, message, details)


def gate_failed(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(422, GATE_FAILED, message, details)


def validation_failed(
    message: str = "Request body is invalid.",
    details: dict[str, Any] | None = None,
) -> ApiError:
    return ApiError(400, VALIDATION_FAILED, message, details)


def unavailable(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(503, UNAVAILABLE, message, details)


def _snake_to_camel(name: str) -> str:
    if "_" not in name:
        return name
    first, *rest = name.split("_")
    return first + "".join(piece[:1].upper() + piece[1:] for piece in rest if piece)


def format_error_path(location: Sequence[object]) -> str:
    parts: list[str] = []
    for item in location:
        if item in {"body", "query", "path", "header", "cookie"}:
            continue
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
            continue
        parts.append(_snake_to_camel(str(item)))
    return ".".join(parts)


def validation_error_body(errors: Sequence[Mapping[str, Any]], request_id: str) -> dict[str, Any]:
    fields = [
        {
            "path": format_error_path(tuple(item.get("loc", ()))),
            "message": str(item.get("msg", "Invalid value")),
        }
        for item in errors
    ]
    message = "Request is invalid."
    for item in errors:
        loc = item.get("loc", ())
        if loc and loc[0] == "body":
            message = "Request body is invalid."
            break
    return error_body(VALIDATION_FAILED, message, {"fields": fields}, request_id)
