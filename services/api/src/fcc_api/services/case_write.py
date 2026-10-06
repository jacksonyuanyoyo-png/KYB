"""案件写入的公共事务：加锁、比较 version、审计、提交。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, Request
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.actor import Actor, AuthFailure
from fcc_api.auth.header_provider import resolve_actor
from fcc_api.auth.policy import require_case_write, require_checklist_item_status, require_operation
from fcc_api.db.models.audit import AuditEvent
from fcc_api.db.models.cases import Case
from fcc_api.errors import ApiError
from fcc_api.ids import new_id

CONFLICT_MESSAGE = (
    "This case was changed by someone else. Reload to see the latest version before saving again."
)

@dataclass
class AuditSpec:
    action: str
    summary: str
    changes: list[dict[str, str]] | None = None
    before: Any = None
    after: Any = None
    ai_model: str | None = None
    ai_accepted: bool | None = None


@dataclass
class CaseWriter:
    session: Session
    case: Case
    actor: Actor
    started_at: datetime
    request_id: str
    correlation_id: str
    audits: list[AuditSpec] = field(default_factory=list)
    sealed: bool = False

    def audit(
        self,
        *,
        action: str,
        summary: str,
        changes: list[dict[str, str]] | None = None,
        before: Any = None,
        after: Any = None,
        ai_model: str | None = None,
        ai_accepted: bool | None = None,
    ) -> None:
        cleaned = summary.strip()
        if not cleaned or len(cleaned) > 500:
            raise ApiError(
                400,
                "VALIDATION_FAILED",
                "Request body is invalid.",
                {"fields": [{"path": "summary", "message": "Summary must be 1–500 characters."}]},
            )
        self.audits.append(
            AuditSpec(
                action=action,
                summary=cleaned,
                changes=changes,
                before=before,
                after=after,
                ai_model=ai_model,
                ai_accepted=ai_accepted,
            )
        )

    def seal(self) -> None:
        if self.sealed:
            return
        if not self.audits:
            raise RuntimeError("case write produced no audit event")
        self.case.version = self.case.version + 1
        self.case.updated_at = self.started_at
        for spec in self.audits:
            self.session.add(self._event(spec, self.case.version))
        self.session.flush()
        self.sealed = True

    def _event(self, spec: AuditSpec, version: int) -> AuditEvent:
        ai_model = spec.ai_model
        ai_accepted = spec.ai_accepted
        ai_rule_version = self.case.rule_version if ai_model is not None else None
        return AuditEvent(
            id=new_id("evt"),
            scope="CASE",
            case_id=self.case.id,
            actor_id=self.actor.id,
            action=spec.action,
            summary=spec.summary,
            at=self.started_at,
            version=version,
            changes=spec.changes,
            ai_model=ai_model,
            ai_accepted=ai_accepted,
            ai_rule_version=ai_rule_version,
            rule_version=self.case.rule_version,
            before_value=spec.before,
            after_value=spec.after,
            correlation_id=self.correlation_id,
            request_id=self.request_id,
        )


class _CaseWrite:
    def __init__(
        self,
        session: Session,
        *,
        actor: Actor,
        case_id: str,
        client_version: int,
        operation: str,
        request_id: str,
        correlation_id: str,
        target_status: str | None = None,
    ) -> None:
        self.session = session
        self.actor = actor
        self.case_id = case_id
        self.client_version = client_version
        self.operation = operation
        self.request_id = request_id
        self.correlation_id = correlation_id
        self.target_status = target_status
        self.writer: CaseWriter | None = None

    def __enter__(self) -> CaseWriter:
        try:
            case = self._lock_case()
            self._authorize(case)
            self._check_version(case)
        except Exception:
            self.session.rollback()
            raise
        started = datetime.now(UTC)
        self.writer = CaseWriter(
            session=self.session,
            case=case,
            actor=self.actor,
            started_at=started,
            request_id=self.request_id,
            correlation_id=self.correlation_id,
        )
        return self.writer

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object) -> bool:
        if exc_type is not None:
            self.session.rollback()
            return False
        assert self.writer is not None
        try:
            self.writer.seal()
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return False

    def _lock_case(self) -> Case | None:
        self.session.execute(text("SET LOCAL lock_timeout = '5s'"))
        try:
            return self.session.scalar(
                select(Case).where(Case.id == self.case_id).with_for_update()
            )
        except OperationalError as exc:
            if _is_lock_timeout(exc):
                raise _conflict(
                    self.case_id,
                    self.client_version,
                    current=None,
                    reason="LOCK_TIMEOUT",
                ) from exc
            raise

    def _authorize(self, case: Case | None) -> None:
        try:
            require_case_write(
                self.actor,
                case,
                self.operation,
                target_status=self.target_status,
                case_id=self.case_id,
                request_id=self.request_id,
            )
        except AuthFailure as exc:
            raise _api_error(exc) from exc

    def _check_version(self, case: Case | None) -> None:
        if case is None:
            return
        if case.version != self.client_version:
            raise _conflict(self.case_id, self.client_version, case.version, reason=None)


@contextmanager
def case_write(
    session: Session,
    *,
    actor: Actor,
    case_id: str,
    client_version: int,
    operation: str,
    request_id: str,
    correlation_id: str,
    target_status: str | None = None,
) -> Iterator[CaseWriter]:
    """锁住案件、比较 version，成功时 version+1、写审计并提交。"""

    with _CaseWrite(
        session,
        actor=actor,
        case_id=case_id,
        client_version=client_version,
        operation=operation,
        request_id=request_id,
        correlation_id=correlation_id,
        target_status=target_status,
    ) as writer:
        yield writer


def insert_audit(
    session: Session,
    *,
    case: Case,
    actor: Actor,
    at: datetime,
    version: int,
    request_id: str,
    correlation_id: str,
    action: str,
    summary: str,
    changes: list[dict[str, str]] | None = None,
    before: Any = None,
    after: Any = None,
    ai_model: str | None = None,
    ai_accepted: bool | None = None,
) -> None:
    session.add(
        AuditEvent(
            id=new_id("evt"),
            scope="CASE",
            case_id=case.id,
            actor_id=actor.id,
            action=action,
            summary=summary,
            at=at,
            version=version,
            changes=changes,
            ai_model=ai_model,
            ai_accepted=ai_accepted,
            ai_rule_version=case.rule_version if ai_model is not None else None,
            rule_version=case.rule_version,
            before_value=before,
            after_value=after,
            correlation_id=correlation_id,
            request_id=request_id,
        )
    )


def require_create_case(actor: Actor, *, request_id: str) -> None:
    try:
        require_operation(actor, "createCase", request_id=request_id)
    except AuthFailure as exc:
        raise _api_error(exc) from exc


def require_item_status(
    actor: Actor,
    current_status: str,
    target_status: str,
    *,
    case_id: str,
    request_id: str,
) -> None:
    try:
        require_checklist_item_status(
            actor,
            current_status,
            target_status,
            case_id=case_id,
            request_id=request_id,
        )
    except AuthFailure as exc:
        raise _api_error(exc) from exc


def request_ids(request: Request) -> tuple[str, str]:
    request_id = getattr(request.state, "request_id", None) or new_id("req")
    header = request.headers.get("x-correlation-id")
    correlation_id = header.strip() if header and header.strip() else str(request_id)
    return str(request_id), correlation_id


def get_actor(request: Request, session: Session = Depends(get_db)) -> Actor:
    return resolve_request_actor(request, session)


def resolve_request_actor(request: Request, session: Session) -> Actor:
    try:
        return resolve_actor(request, session)
    except AuthFailure as exc:
        raise _api_error(exc) from exc


def _api_error(exc: AuthFailure) -> ApiError:
    return ApiError(exc.status_code, exc.code, exc.message, dict(exc.details))


def _conflict(case_id: str, client_version: int, current: int | None, *, reason: str | None) -> ApiError:
    details: dict[str, Any] = {
        "resource": "case",
        "id": case_id,
        "clientVersion": client_version,
    }
    if current is not None:
        details["currentVersion"] = current
    if reason is not None:
        details["reason"] = reason
    return ApiError(409, "VERSION_CONFLICT", CONFLICT_MESSAGE, details)


def _is_lock_timeout(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if sqlstate == "55P03":
            return True
        if "lock timeout" in str(current).lower():
            return True
        nested = current.__cause__ or getattr(current, "orig", None)
        current = nested if isinstance(nested, BaseException) else None
    return False
