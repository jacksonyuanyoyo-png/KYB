"""名单筛查写入、合规处置，以及送审前的筛查门禁。

抽取和语言模型接口不得调用 ``record_disposition``。处置只从 HTTP 路由进入。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from fcc_api.adapters.screening import ScreeningHit, get_screener, normalize_hit
from fcc_api.auth.actor import Actor, AuthFailure, Role, fail_auth
from fcc_api.auth.policy import require_case_visible
from fcc_api.db.models.cases import Case
from fcc_api.db.models.parties import Party
from fcc_api.errors import ApiError
from fcc_api.ids import new_id
from fcc_api.rules.domain import persons_to_identify
from fcc_api.rules.types import Party as RuleParty
from fcc_api.schemas.common import js_number
from fcc_api.services.case_write import CONFLICT_MESSAGE, CaseWriter

SCREENING_MESSAGE = "Name screening is incomplete."
_PARTY_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_SCREEN_ROLES = frozenset({Role.ADVISOR, Role.OPERATIONS, Role.ADMIN})
_SCREEN_STATUSES = frozenset({"BUILDING", "DOCS_REQUESTED", "RETURNED"})
_DISPOSITION_STATUSES = frozenset({
    "BUILDING",
    "DOCS_REQUESTED",
    "RETURNED",
    "READY_FOR_COMPLIANCE",
})
_DISPOSITIONS = frozenset({"MATCH_CONFIRMED", "FALSE_POSITIVE"})
_RECORD = "recordScreening"
_DISPOSE = "screeningDisposition"
_UPDATED_AT_FLOOR = datetime(1970, 1, 1, tzinfo=UTC)


def screening_required(settings: object | None = None) -> bool:
    """缺省和读不到配置时都是 false，避免改变现有送审。

    进程环境变量优先于已缓存的 Settings。这样测试可以在进程启动后打开门禁，
    而启动时没读到 ``SCREENING_REQUIRED`` 时仍保持关闭。
    """

    if "SCREENING_REQUIRED" in os.environ:
        return _as_bool(os.environ.get("SCREENING_REQUIRED"))
    source = settings if settings is not None else _loaded_settings()
    if source is not None and hasattr(source, "screening_required"):
        return _as_bool(getattr(source, "screening_required"))
    return False


def assert_screening_current(session: Session, case: Case) -> None:
    """``SCREENING_REQUIRED`` 未打开时直接返回，不读筛查表。"""

    if not screening_required():
        return
    rows = list(
        session.scalars(
            select(Party).where(Party.case_id == case.id).order_by(Party.position, Party.id)
        )
    )
    needed = persons_to_identify([_rule_party(row) for row in rows])
    if not needed:
        return
    updated = _party_updated_at(session, case.id, [party.id for party in needed])
    for party in needed:
        if not _party_is_screened(session, case.id, party.id, updated[party.id]):
            raise ApiError(422, "GATE_FAILED", SCREENING_MESSAGE, {"gate": "SCREENING"})


def record_screening(
    session: Session,
    actor: Actor,
    case_id: str,
    *,
    version: int,
    party_ids: list[str],
    request_id: str,
    correlation_id: str,
) -> Any:
    with _screening_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=version,
        request_id=request_id,
        correlation_id=correlation_id,
        authorize=_authorize_screening,
    ) as writer:
        parties = _parties_by_id(session, writer.case.id)
        selected = _people_to_screen(party_ids, parties)
        screener = get_screener()
        adapter = str(getattr(screener, "name", "noop") or "noop").strip()[:40] or "noop"
        recorded: list[dict[str, str]] = []
        inserts: list[dict[str, Any]] = []
        for party in selected:
            hit = normalize_hit(
                screener.screen(party_id=party.id, display_name=party.legal_name)
            )
            _reject_foreign_status(hit)
            run_id = new_id("scr")
            inserts.append({
                "id": run_id,
                "case_id": writer.case.id,
                "party_id": party.id,
                "adapter": adapter,
                "status": hit.status,
                "reference": hit.reference,
                "created_at": writer.started_at,
            })
            recorded.append({
                "partyId": party.id,
                "status": hit.status,
                "reference": hit.reference,
            })
        _insert_runs(session, inserts)
        count = len(recorded)
        writer.audit(
            action="SCREENING_RECORDED",
            summary=f"Recorded screening for {count} people",
            after=recorded,
        )
        writer.seal()
        detail = _case_detail(session, case_id)
    return detail


def record_disposition(
    session: Session,
    actor: Actor,
    case_id: str,
    run_id: str,
    *,
    version: int,
    disposition: str,
    request_id: str,
    correlation_id: str,
) -> Any:
    """仅合规人员。语言模型和抽取代码不能调用这个函数。"""

    if disposition not in _DISPOSITIONS:
        raise _invalid([{
            "path": "disposition",
            "message": "Disposition must be MATCH_CONFIRMED or FALSE_POSITIVE.",
        }])
    with _screening_write(
        session,
        actor=actor,
        case_id=case_id,
        client_version=version,
        request_id=request_id,
        correlation_id=correlation_id,
        authorize=_authorize_disposition,
    ) as writer:
        run = _load_run(session, writer.case.id, run_id)
        if _existing_disposition(session, writer.case.id, run_id):
            raise ApiError(
                422,
                "GATE_FAILED",
                "This screening result already has a disposition.",
                {"gate": "SCREENING"},
            )
        _store_disposition(session, run_id, disposition, writer.started_at, actor.id)
        if disposition == "MATCH_CONFIRMED":
            _confirm_match(session, writer, run, disposition)
        else:
            writer.audit(
                action="SCREENING_RECORDED",
                summary="Recorded a false-positive screening disposition",
                after={
                    "partyId": run["party_id"],
                    "runId": run_id,
                    "disposition": "FALSE_POSITIVE",
                    "status": run["status"],
                    "reference": run["reference"],
                    "recordedByRole": "COMPLIANCE",
                },
            )
        writer.seal()
        detail = _case_detail(session, case_id)
    return detail


def _loaded_settings() -> object | None:
    try:
        from fcc_api.config import get_settings
    except Exception:
        return None
    try:
        return get_settings()
    except Exception:
        return None


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _TRUE_VALUES


def _rule_party(row: Party) -> RuleParty:
    return RuleParty(
        id=row.id,
        parent_id=row.parent_id,
        kind=row.kind,
        legal_name=row.legal_name,
        ownership_percent=float(row.ownership_percent),
        is_controller=row.is_controller,
        is_signing_authority=row.is_signing_authority,
        is_us_person=row.is_us_person,
        is_pep_hio=row.is_pep_hio,
        entity_type=row.entity_type,
        country=row.country,
        title=row.title,
        us_tax_class=row.us_tax_class,
    )


def _party_updated_at(session: Session, case_id: str, party_ids: list[str]) -> dict[str, datetime]:
    found = {party_id: _UPDATED_AT_FLOOR for party_id in party_ids}
    if "updated_at" not in _columns(session, "parties"):
        return found
    rows = session.execute(
        text("SELECT id, updated_at FROM parties WHERE case_id = :case_id"),
        {"case_id": case_id},
    ).mappings()
    for row in rows:
        if row["id"] not in found:
            continue
        value = row["updated_at"]
        if isinstance(value, datetime):
            found[row["id"]] = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return found


def _party_is_screened(session: Session, case_id: str, party_id: str, updated_at: datetime) -> bool:
    if not _table_exists(session, "screening_runs"):
        return False
    clear = session.execute(
        text(
            """
            SELECT 1
            FROM screening_runs
            WHERE case_id = :case_id
              AND party_id = :party_id
              AND status = 'CLEAR'
              AND created_at > :updated_at
            LIMIT 1
            """
        ),
        {"case_id": case_id, "party_id": party_id, "updated_at": updated_at},
    ).first()
    if clear is not None:
        return True
    return _has_false_positive(session, case_id, party_id)


def _has_false_positive(session: Session, case_id: str, party_id: str) -> bool:
    columns = _columns(session, "screening_runs")
    if "disposition" in columns and "disposition_by" in columns:
        stored = session.execute(
            text(
                """
                SELECT 1
                FROM screening_runs AS runs
                JOIN users ON users.id = runs.disposition_by
                WHERE runs.case_id = :case_id
                  AND runs.party_id = :party_id
                  AND runs.disposition = 'FALSE_POSITIVE'
                  AND users.role = 'COMPLIANCE'
                LIMIT 1
                """
            ),
            {"case_id": case_id, "party_id": party_id},
        ).first()
        if stored is not None:
            return True
    recorded = session.execute(
        text(
            """
            SELECT 1
            FROM audit_events
            WHERE case_id = :case_id
              AND after_value->>'partyId' = :party_id
              AND after_value->>'disposition' = 'FALSE_POSITIVE'
              AND after_value->>'recordedByRole' = 'COMPLIANCE'
            LIMIT 1
            """
        ),
        {"case_id": case_id, "party_id": party_id},
    ).first()
    return recorded is not None


def _parties_by_id(session: Session, case_id: str) -> dict[str, Party]:
    rows = session.scalars(select(Party).where(Party.case_id == case_id))
    return {row.id: row for row in rows}


def _people_to_screen(party_ids: list[str], parties: dict[str, Party]) -> list[Party]:
    if not party_ids:
        raise _invalid([{"path": "partyIds", "message": "Select at least one person."}])
    if len(party_ids) > 500:
        raise _invalid([{"path": "partyIds", "message": "Select at most 500 people."}])
    selected: list[Party] = []
    seen: set[str] = set()
    for index, party_id in enumerate(party_ids):
        path = f"partyIds[{index}]"
        if not isinstance(party_id, str) or not _PARTY_ID.fullmatch(party_id):
            raise _invalid([{"path": path, "message": "Party id format is invalid."}])
        if party_id in seen:
            raise _invalid([{"path": path, "message": "Party ids must be unique."}])
        seen.add(party_id)
        party = parties.get(party_id)
        if party is None:
            raise _invalid([{"path": path, "message": "Party is not on this case."}])
        if party.kind != "PERSON":
            raise _invalid([{"path": path, "message": "Only a person can be screened."}])
        selected.append(party)
    return selected


def _reject_foreign_status(hit: ScreeningHit) -> None:
    if hit.status not in {"CLEAR", "POTENTIAL_MATCH", "ERROR"}:
        raise ApiError(503, "UNAVAILABLE", "Name screening is temporarily unavailable.", {})


def _insert_runs(session: Session, rows: list[dict[str, Any]]) -> None:
    if not _table_exists(session, "screening_runs"):
        raise ApiError(503, "UNAVAILABLE", "Name screening is temporarily unavailable.", {})
    statement = text(
        """
        INSERT INTO screening_runs (
            id, case_id, party_id, adapter, status, reference, created_at
        ) VALUES (
            :id, :case_id, :party_id, :adapter, :status, :reference, :created_at
        )
        """
    )
    for row in rows:
        session.execute(statement, row)


def _load_run(session: Session, case_id: str, run_id: str) -> dict[str, Any]:
    if not _table_exists(session, "screening_runs"):
        raise ApiError(404, "NOT_FOUND", "Screening run not found.", {"resource": "screeningRun", "id": run_id})
    row = session.execute(
        text(
            """
            SELECT id, case_id, party_id, status, reference
            FROM screening_runs
            WHERE id = :id AND case_id = :case_id
            """
        ),
        {"id": run_id, "case_id": case_id},
    ).mappings().first()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Screening run not found.", {"resource": "screeningRun", "id": run_id})
    return dict(row)


def _existing_disposition(session: Session, case_id: str, run_id: str) -> str | None:
    columns = _columns(session, "screening_runs")
    if "disposition" in columns:
        stored = session.execute(
            text(
                """
                SELECT disposition
                FROM screening_runs
                WHERE id = :id AND case_id = :case_id
                """
            ),
            {"id": run_id, "case_id": case_id},
        ).scalar()
        if stored:
            return str(stored)
    recorded = session.execute(
        text(
            """
            SELECT after_value->>'disposition'
            FROM audit_events
            WHERE case_id = :case_id
              AND after_value->>'runId' = :run_id
              AND after_value->>'disposition' IN ('MATCH_CONFIRMED', 'FALSE_POSITIVE')
            LIMIT 1
            """
        ),
        {"case_id": case_id, "run_id": run_id},
    ).scalar()
    if recorded:
        return str(recorded)
    return None


def _store_disposition(
    session: Session,
    run_id: str,
    disposition: str,
    at: datetime,
    actor_id: str,
) -> None:
    columns = _columns(session, "screening_runs")
    if "disposition" not in columns:
        return
    assignments = ["disposition = :disposition"]
    params: dict[str, Any] = {"id": run_id, "disposition": disposition}
    if "disposition_at" in columns:
        assignments.append("disposition_at = :disposition_at")
        params["disposition_at"] = at
    if "disposition_by" in columns:
        assignments.append("disposition_by = :disposition_by")
        params["disposition_by"] = actor_id
    session.execute(
        text(f"UPDATE screening_runs SET {', '.join(assignments)} WHERE id = :id"),
        params,
    )


def _confirm_match(session: Session, writer: CaseWriter, run: dict[str, Any], disposition: str) -> None:
    party = session.get(Party, (writer.case.id, run["party_id"]))
    if party is None:
        raise _invalid([{"path": "runId", "message": "Party is not on this case."}])
    before = _party_snapshot(party)
    if not party.is_pep_hio:
        party.is_pep_hio = True
        party.updated_at = datetime.now(UTC)
        session.flush()
    after_party = _party_snapshot(party)
    changes = None
    if before.get("isPepHio") is not True:
        changes = [{"field": "isPepHio", "from": "false", "to": "true"}]
    writer.audit(
        action="OWNERSHIP_UPDATED",
        summary="Confirmed screening match",
        changes=changes,
        before={"parties": [before]},
        after={
            "parties": [after_party],
            "partyId": party.id,
            "runId": run["id"],
            "disposition": disposition,
            "recordedByRole": "COMPLIANCE",
        },
    )


def _party_snapshot(row: Party) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": row.id,
        "parentId": row.parent_id,
        "kind": row.kind,
        "legalName": row.legal_name,
        "ownershipPercent": js_number(row.ownership_percent),
        "isController": row.is_controller,
        "isSigningAuthority": row.is_signing_authority,
        "isUsPerson": row.is_us_person,
        "isPepHio": row.is_pep_hio,
    }
    if row.entity_type is not None:
        payload["entityType"] = row.entity_type
    if row.country is not None:
        payload["country"] = row.country
    if row.title is not None:
        payload["title"] = row.title
    if row.us_tax_class is not None:
        payload["usTaxClass"] = row.us_tax_class
    return payload


def _case_detail(session: Session, case_id: str) -> Any:
    from fcc_api.services.cases import load_case_detail

    return load_case_detail(session, case_id)


def _table_exists(session: Session, table: str) -> bool:
    found = session.execute(
        text("SELECT to_regclass(:name)"),
        {"name": f"public.{table}"},
    ).scalar()
    return found is not None


def _columns(session: Session, table: str) -> set[str]:
    if not _table_exists(session, table):
        return set()
    rows = session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table
            """
        ),
        {"table": table},
    )
    return {str(row[0]) for row in rows}


def _authorize_screening(actor: Actor, case: Case, *, request_id: str) -> None:
    _authorize_visible(actor, case, request_id=request_id)
    if actor.role not in _SCREEN_ROLES:
        _forbid_role(actor, _RECORD, case_id=case.id, request_id=request_id, message=(
            "Your role cannot perform this operation."
        ))
    if case.status not in _SCREEN_STATUSES:
        _forbid_status(actor, _RECORD, status=case.status, case_id=case.id, request_id=request_id)


def _authorize_disposition(actor: Actor, case: Case, *, request_id: str) -> None:
    _authorize_visible(actor, case, request_id=request_id)
    if actor.role != Role.COMPLIANCE:
        _forbid_role(
            actor,
            _DISPOSE,
            case_id=case.id,
            request_id=request_id,
            message="Only Compliance can record a screening disposition.",
        )
    if case.status not in _DISPOSITION_STATUSES:
        _forbid_status(actor, _DISPOSE, status=case.status, case_id=case.id, request_id=request_id)


def _authorize_visible(actor: Actor, case: Case, *, request_id: str) -> None:
    try:
        require_case_visible(actor, case, case_id=case.id, request_id=request_id)
    except AuthFailure as exc:
        raise ApiError(exc.status_code, exc.code, exc.message, dict(exc.details)) from exc


def _forbid_role(actor: Actor, operation: str, *, case_id: str, request_id: str, message: str) -> None:
    try:
        fail_auth(
            status_code=403,
            code="FORBIDDEN",
            message=message,
            details={"reason": "ROLE", "role": actor.role.value, "operation": operation},
            reason="ROLE",
            operation=operation,
            case_id=case_id,
            user_id=actor.id,
            role=actor.role.value,
            request_id=request_id,
        )
    except AuthFailure as exc:
        raise ApiError(exc.status_code, exc.code, exc.message, dict(exc.details)) from exc


def _forbid_status(
    actor: Actor,
    operation: str,
    *,
    status: str,
    case_id: str,
    request_id: str,
) -> None:
    try:
        fail_auth(
            status_code=403,
            code="FORBIDDEN",
            message=_status_message(status),
            details={"reason": "CASE_STATUS", "status": status, "operation": operation},
            reason="CASE_STATUS",
            operation=operation,
            case_id=case_id,
            user_id=actor.id,
            role=actor.role.value,
            request_id=request_id,
        )
    except AuthFailure as exc:
        raise ApiError(exc.status_code, exc.code, exc.message, dict(exc.details)) from exc


def _status_message(status: str) -> str:
    if status == "APPROVED":
        return "This case is APPROVED and can no longer be edited."
    return f"This case is {status} and can no longer be edited by your role."


def _invalid(fields: list[dict[str, str]]) -> ApiError:
    return ApiError(400, "VALIDATION_FAILED", "Request body is invalid.", {"fields": fields})


class _ScreeningWrite:
    def __init__(
        self,
        session: Session,
        *,
        actor: Actor,
        case_id: str,
        client_version: int,
        request_id: str,
        correlation_id: str,
        authorize: Any,
    ) -> None:
        self.session = session
        self.actor = actor
        self.case_id = case_id
        self.client_version = client_version
        self.request_id = request_id
        self.correlation_id = correlation_id
        self.authorize = authorize
        self.writer: CaseWriter | None = None

    def __enter__(self) -> CaseWriter:
        try:
            case = self._lock_case()
            if case is None:
                self._missing()
            assert case is not None
            self.authorize(self.actor, case, request_id=self.request_id)
            self._check_version(case)
        except Exception:
            self.session.rollback()
            raise
        self.writer = CaseWriter(
            session=self.session,
            case=case,
            actor=self.actor,
            started_at=datetime.now(UTC),
            request_id=self.request_id,
            correlation_id=self.correlation_id,
        )
        return self.writer

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
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
            return self.session.scalar(select(Case).where(Case.id == self.case_id).with_for_update())
        except OperationalError as exc:
            if _lock_timeout(exc):
                raise _conflict(self.case_id, self.client_version, current=None, reason="LOCK_TIMEOUT") from exc
            raise

    def _missing(self) -> None:
        try:
            require_case_visible(self.actor, None, case_id=self.case_id, request_id=self.request_id)
        except AuthFailure as exc:
            raise ApiError(exc.status_code, exc.code, exc.message, dict(exc.details)) from exc

    def _check_version(self, case: Case) -> None:
        if case.version != self.client_version:
            raise _conflict(self.case_id, self.client_version, case.version, reason=None)


@contextmanager
def _screening_write(
    session: Session,
    *,
    actor: Actor,
    case_id: str,
    client_version: int,
    request_id: str,
    correlation_id: str,
    authorize: Any,
) -> Iterator[CaseWriter]:
    with _ScreeningWrite(
        session,
        actor=actor,
        case_id=case_id,
        client_version=client_version,
        request_id=request_id,
        correlation_id=correlation_id,
        authorize=authorize,
    ) as writer:
        yield writer


def _conflict(case_id: str, client_version: int, current: int | None, *, reason: str | None) -> ApiError:
    details: dict[str, Any] = {"resource": "case", "id": case_id, "clientVersion": client_version}
    if current is not None:
        details["currentVersion"] = current
    if reason is not None:
        details["reason"] = reason
    return ApiError(409, "VERSION_CONFLICT", CONFLICT_MESSAGE, details)


def _lock_timeout(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if sqlstate == "55P03" or "lock timeout" in str(current).lower():
            return True
        nested = current.__cause__ or getattr(current, "orig", None)
        current = nested if isinstance(nested, BaseException) else None
    return False
