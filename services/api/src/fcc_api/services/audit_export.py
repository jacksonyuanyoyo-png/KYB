"""审计发件箱与前后值调查。调查响应不进普通日志。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fcc_api.auth.actor import Role
from fcc_api.auth.policy import case_visible
from fcc_api.config import get_settings
from fcc_api.db.models.audit import AuditEvent
from fcc_api.db.models.cases import Case
from fcc_api.errors import ApiError
from fcc_api.schemas.rules import ApiModel
from fcc_api.storage.local import resolve_storage_root

_alert_log = logging.getLogger("fcc_api.alerts")
_VALUE_ROLES = frozenset({"COMPLIANCE", "ADMIN"})


class AuditValuesOut(ApiModel):
    before_value: Any = None
    after_value: Any = None


def get_audit_values(
    session: Session,
    actor: Any,
    event_id: str,
    request_id: str | None = None,
) -> AuditValuesOut:
    """返回前后值。调用方不得把这个结果写进应用日志。"""

    role = _role_name(actor)
    if role not in _VALUE_ROLES:
        raise ApiError(
            403,
            "FORBIDDEN",
            "Your role cannot read audit values.",
            {"reason": "ROLE", "role": role, "operation": "readAuditValues"},
        )
    row = session.get(AuditEvent, event_id)
    if row is None or not _event_visible(session, actor, row):
        raise ApiError(
            404,
            "NOT_FOUND",
            "Audit event not found.",
            {"resource": "auditEvent", "id": event_id},
        )
    _record_access(actor_id=str(actor.id), case_id=row.case_id, request_id=request_id)
    return AuditValuesOut(before_value=row.before_value, after_value=row.after_value)


def backfill_audit_outbox(session: Session) -> None:
    """把还没有发件箱行的 audit_events 补进 audit_outbox。表不存在则返回。"""

    try:
        with session.begin_nested():
            existing = set(session.execute(text("SELECT event_id FROM audit_outbox")).scalars().all())
    except SQLAlchemyError as exc:
        if _sqlstate(exc) == "42P01":
            return
        raise
    query = select(AuditEvent).order_by(AuditEvent.seq)
    if existing:
        query = query.where(AuditEvent.id.notin_(tuple(existing)))
    rows = list(session.scalars(query).all())
    if not rows:
        return
    with session.begin_nested():
        for row in rows:
            session.execute(
                text(
                    """
                    INSERT INTO audit_outbox (event_id, payload, exported_at)
                    SELECT :event_id, CAST(:payload AS jsonb), NULL
                    WHERE NOT EXISTS (
                        SELECT 1 FROM audit_outbox WHERE event_id = :event_id
                    )
                    """
                ),
                {
                    "event_id": row.id,
                    "payload": json.dumps(event_export_payload(row), ensure_ascii=False, default=str),
                },
            )
    session.commit()


def export_pending_outbox(session: Session) -> None:
    """把 exported_at 为空的发件箱行写到本地 audit 目录，成功后填写 exported_at。"""

    try:
        with session.begin_nested():
            pending = session.execute(
                text(
                    """
                    SELECT o.event_id, o.payload, e.seq, e.at
                    FROM audit_outbox o
                    JOIN audit_events e ON e.id = o.event_id
                    WHERE o.exported_at IS NULL
                    ORDER BY e.seq
                    """
                )
            ).all()
    except SQLAlchemyError as exc:
        if _sqlstate(exc) == "42P01":
            return
        raise
    if not pending:
        return
    root = resolve_storage_root(get_settings())
    exported_at = datetime.now(UTC)
    with session.begin_nested():
        for row in pending:
            payload = _payload_dict(row.payload)
            _write_export(root, at=row.at, seq=int(row.seq), payload=payload)
            session.execute(
                text(
                    """
                    UPDATE audit_outbox
                    SET exported_at = :exported_at
                    WHERE event_id = :event_id AND exported_at IS NULL
                    """
                ),
                {"event_id": row.event_id, "exported_at": exported_at},
            )
    session.commit()


def event_export_payload(row: Any) -> dict[str, Any]:
    """与 GET /audit 单条相同，并附上 beforeValue、afterValue。"""

    from fcc_api.services.queries import _audit_out

    payload = _audit_out(row).model_dump(mode="json", by_alias=True)
    payload["beforeValue"] = _json_ready(getattr(row, "before_value", None))
    payload["afterValue"] = _json_ready(getattr(row, "after_value", None))
    return payload


def _event_visible(session: Session, actor: Any, row: AuditEvent) -> bool:
    if row.scope != "CASE":
        return row.scope == "RULE_LIBRARY"
    case_row = session.get(Case, row.case_id) if row.case_id else None
    return case_row is not None and case_visible(actor, case_row)


def _record_access(*, actor_id: str, case_id: str | None, request_id: str | None) -> None:
    _alert_log.info(
        "alert",
        extra={
            "event": "alert",
            "type": "AUDIT_VALUE_ACCESS",
            "userId": actor_id,
            "caseId": case_id,
            "documentId": None,
            "count": 1,
            "requestId": request_id,
        },
    )


def _write_export(root: Path, *, at: datetime, seq: int, payload: dict[str, Any]) -> None:
    moment = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    path = root / "audit" / f"{moment:%Y}" / f"{moment:%m}" / f"{moment:%d}" / f"{seq}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    temporary.replace(path)


def _payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    if isinstance(value, dict):
        return value
    return {}


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.loads(json.dumps(value, default=str))


def _role_name(actor: Any) -> str:
    role = getattr(actor, "role", "")
    if isinstance(role, Role):
        return role.value
    return str(role)


def _sqlstate(exc: BaseException) -> str | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attr in ("sqlstate", "pgcode"):
            code = getattr(current, attr, None)
            if code:
                return str(code)
        nested = current.__cause__ or getattr(current, "orig", None)
        current = nested if isinstance(nested, BaseException) else None
    return None
