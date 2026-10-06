import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = {
    "filename",
    "legalname",
    "trustedcontactname",
    "title",
    "registrationnumber",
    "sig",
    "expires",
    "url",
    "authorization",
    "localurlsigningsecret",
    "quote",
    "beforevalue",
    "aftervalue",
    "from",
    "to",
    "prompt",
    "text",
    "value",
    "accesstoken",
    "idtoken",
    "refreshtoken",
    "token",
    "password",
    "secret",
    "apikey",
    "filebytes",
    "awssecretaccesskey",
    "secretaccesskey",
}

_LOG_RECORD_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
    "asctime",
}

_QUERY_SECRET = re.compile(r"(?i)\b(sig|expires)=([^&\s]+)")
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_DB_URL = re.compile(r"(?i)\b((?:postgresql|postgres)(?:\+\w+)?://[^:\s/]+:)([^@\s]+)@")

_configured = False


def _normalize_key(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())


def is_sensitive_key(key: str) -> bool:
    return _normalize_key(key) in _SENSITIVE_KEYS


def redact_text(value: str) -> str:
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _DB_URL.sub(r"\1***@", value)
    return _QUERY_SECRET.sub(lambda match: f"{match.group(1)}={REDACTED}", value)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_key(key):
                cleaned[key] = REDACTED
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


class RedactionFilter(logging.Filter):
    """按键名把禁止入日志的字段替换成 [REDACTED]。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if isinstance(record.args, dict):
            record.args = redact(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(
                redact(item) if isinstance(item, (dict, list, str)) else item for item in record.args
            )
        for key, value in list(record.__dict__.items()):
            if key in _LOG_RECORD_ATTRS or key.startswith("_"):
                continue
            if is_sensitive_key(key):
                record.__dict__[key] = REDACTED
            else:
                record.__dict__[key] = redact(value)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        moment = datetime.now(UTC)
        timestamp = moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
        payload: dict[str, Any] = {
            "time": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _LOG_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(redact(payload), default=str, ensure_ascii=False)


def configure_logging(level: str) -> None:
    global _configured
    root = logging.getLogger()
    root.setLevel(level.upper())
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root.addHandler(handler)
    _configured = True
