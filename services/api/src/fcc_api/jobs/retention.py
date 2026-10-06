"""按保留期清空已批准案件的内容。不删除 audit_events。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from fcc_api.services.retention import delete_expired_cases


def run_once(session: Session, retention_days: int | None = None) -> None:
    delete_expired_cases(session, retention_days=retention_days)
