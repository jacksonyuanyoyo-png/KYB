from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from fcc_api.config import Settings, get_settings
from fcc_api.db.session import get_session
from fcc_api.ids import new_id


def get_db() -> Iterator[Session]:
    yield from get_session()


def get_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", "")
    if isinstance(request_id, str) and request_id:
        return request_id
    return new_id("req")


def get_current_settings() -> Settings:
    return get_settings()
