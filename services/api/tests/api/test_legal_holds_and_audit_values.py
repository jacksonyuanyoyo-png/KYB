"""T-LAUNCH-12、13、23、24 里当前库能执行的部分。

缺表或缺少 ``cases.approved_at`` 时，任务按规格直接返回；能在本事务里补上的结构会随测试回滚。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import pytest
from sqlalchemy import text

from fcc_api.api.routers.legal_holds import router as legal_holds_router
from fcc_api.config import get_settings
from fcc_api.ids import new_id
from fcc_api.jobs.audit_export import backfill_outbox, run_once as export_once
from fcc_api.jobs.retention import run_once as retain_once

ADMIN = {"X-User-Id": "u-admin", "X-User-Role": "ADMIN"}
ADVISOR = {"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"}
OPS = {"X-User-Id": "u-ops", "X-User-Role": "OPERATIONS"}
COMPLIANCE = {"X-User-Id": "u-compliance", "X-User-Role": "COMPLIANCE"}
SECRET_BEFORE = "SECRET-BEFORE-VALUE-9f3c"
SECRET_AFTER = "SECRET-AFTER-VALUE-9f3c"


@pytest.fixture
def api(db_client: Any) -> Any:
    paths = {getattr(route, "path", "") for route in db_client.app.routes}
    if "/api/v1/legal-holds" not in paths:
        db_client.app.include_router(legal_holds_router)
    return db_client


@pytest.fixture
def db(db_session: Any) -> Any:
    return db_session


def test_t_launch_13_audit_values_roles_and_list(api: Any, db: Any, caplog: pytest.LogCaptureFixture) -> None:
    event_id, before, after = _insert_valued_event(db)
    caplog.set_level(logging.INFO)
    advisor = api.get(f"/api/v1/audit/{event_id}/values", headers=ADVISOR)
    operations = api.get(f"/api/v1/audit/{event_id}/values", headers=OPS)
    assert advisor.status_code == 403, advisor.text
    assert operations.status_code == 403, operations.text
    assert advisor.json()["error"]["details"]["reason"] == "ROLE"
    assert operations.json()["error"]["details"]["reason"] == "ROLE"
    assert SECRET_BEFORE not in advisor.text
    assert SECRET_AFTER not in operations.text

    compliance = api.get(f"/api/v1/audit/{event_id}/values", headers=COMPLIANCE)
    admin = api.get(f"/api/v1/audit/{event_id}/values", headers=ADMIN)
    assert compliance.status_code == 200, compliance.text
    assert admin.status_code == 200, admin.text
    assert compliance.json() == {"beforeValue": before, "afterValue": after}
    assert admin.json()["beforeValue"] == before
    assert admin.json()["afterValue"] == after

    missing = api.get("/api/v1/audit/evt_missing_value/values", headers=COMPLIANCE)
    assert missing.status_code == 404, missing.text

    listed = api.get("/api/v1/audit", headers=ADMIN, params={"limit": 20})
    assert listed.status_code == 200, listed.text
    item = next(row for row in listed.json()["items"] if row["id"] == event_id)
    assert "beforeValue" not in item
    assert "afterValue" not in item
    assert SECRET_BEFORE not in listed.text

    from fcc_api.services import audit_export

    original = audit_export.case_visible
    audit_export.case_visible = lambda actor, case_row: False
    try:
        hidden = api.get(f"/api/v1/audit/{event_id}/values", headers=COMPLIANCE)
    finally:
        audit_export.case_visible = original
    assert hidden.status_code == 404, hidden.text

    logged = _log_blob(caplog)
    assert SECRET_BEFORE not in logged
    assert SECRET_AFTER not in logged
    assert '"event": "alert"' in logged or "'event': 'alert'" in logged or "alert" in logged
    alerts = [record for record in caplog.records if getattr(record, "event", None) == "alert"]
    assert alerts
    assert all(getattr(record, "type", None) == "AUDIT_VALUE_ACCESS" for record in alerts)
    assert all(not hasattr(record, "beforeValue") for record in alerts)
    assert all(not hasattr(record, "afterValue") for record in alerts)
    rows_after = db.execute(text("SELECT count(*) FROM audit_events WHERE id = :id"), {"id": event_id}).scalar()
    assert rows_after == 1


def test_t_launch_12_missing_outbox_returns(db: Any) -> None:
    hidden = False
    if _table_exists(db, "audit_outbox"):
        db.execute(text("ALTER TABLE audit_outbox RENAME TO audit_outbox_hidden"))
        hidden = True
    try:
        backfill_outbox(db)
        export_once(db)
    finally:
        if hidden:
            db.execute(text("ALTER TABLE audit_outbox_hidden RENAME TO audit_outbox"))


def test_t_launch_12_exports_before_value(db: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    if not _ensure_outbox(db):
        pytest.skip("无法在本事务创建 audit_outbox")
    monkeypatch.setattr(get_settings(), "local_storage_root", str(tmp_path))
    event_id, before, _after = _insert_valued_event(db, case_id="case-0142")
    backfill_outbox(db)
    export_once(db)
    exported_at = db.execute(
        text("SELECT exported_at FROM audit_outbox WHERE event_id = :id"),
        {"id": event_id},
    ).scalar()
    assert exported_at is not None
    seq = db.execute(text("SELECT seq FROM audit_events WHERE id = :id"), {"id": event_id}).scalar()
    matches = list(tmp_path.rglob(f"{int(seq)}.json"))
    assert len(matches) == 1
    payload = json.loads(matches[0].read_text(encoding="utf-8"))
    assert payload["beforeValue"] == before
    assert "audit" in matches[0].parts


def test_t_launch_23_missing_approved_at_returns(db: Any) -> None:
    before_name = _legal_name(db, "case-0119")
    before_count = _audit_count(db)
    hidden = False
    if _column_exists(db, "cases", "approved_at"):
        db.execute(text("ALTER TABLE cases RENAME COLUMN approved_at TO approved_at_hidden"))
        hidden = True
    try:
        retain_once(db, retention_days=0)
        assert _legal_name(db, "case-0119") == before_name
        assert _audit_count(db) == before_count
    finally:
        if hidden:
            db.execute(text("ALTER TABLE cases RENAME COLUMN approved_at_hidden TO approved_at"))


def test_t_launch_23_retention_deletes_expired_case(db: Any) -> None:
    if not _prepare_retention_schema(db):
        pytest.skip("无法在本事务准备保留期列或 RETENTION_DELETED 审计动作")
    case_id = "case-0119"
    original = db.execute(
        text(
            """
            SELECT legal_name, reference, status, version
            FROM cases WHERE id = :id
            """
        ),
        {"id": case_id},
    ).one()
    other_parties = _count_for_case(db, "parties", "case-0139")
    before_ids = set(db.execute(text("SELECT id FROM audit_events")).scalars().all())
    db.execute(
        text("UPDATE cases SET approved_at = now() - interval '2 days' WHERE id = :id"),
        {"id": case_id},
    )
    retain_once(db, retention_days=0)
    current = db.execute(
        text(
            """
            SELECT legal_name, reference, status, version, registration_number, jurisdiction
            FROM cases WHERE id = :id
            """
        ),
        {"id": case_id},
    ).one()
    assert current.legal_name == "[deleted]"
    assert current.reference == original.reference
    assert current.status == original.status
    assert current.version == original.version
    assert current.registration_number == ""
    assert current.jurisdiction == ""
    for table in ("parties", "case_documents", "checklist_items", "review_tasks", "upload_slots"):
        assert _count_for_case(db, table, case_id) == 0
    assert _count_for_case(db, "parties", "case-0139") == other_parties
    after_ids = set(db.execute(text("SELECT id FROM audit_events")).scalars().all())
    assert before_ids <= after_ids
    assert len(after_ids) == len(before_ids) + 1
    added = db.execute(
        text(
            """
            SELECT action, summary, before_value
            FROM audit_events
            WHERE case_id = :id AND action = 'RETENTION_DELETED'
            """
        ),
        {"id": case_id},
    ).one()
    assert added.summary == "Retention period elapsed; case contents deleted."
    assert isinstance(added.before_value, list)
    assert original.legal_name not in json.dumps(added.before_value)
    assert "legalName" in added.before_value


def test_t_launch_24_open_hold_keeps_case(db: Any) -> None:
    if not _prepare_retention_schema(db) or not _ensure_legal_holds(db):
        pytest.skip("无法在本事务准备 legal_holds 或保留期列")
    case_id = "case-0139"
    original_name = _legal_name(db, case_id)
    parties = _count_for_case(db, "parties", case_id)
    audits = _audit_count(db)
    db.execute(
        text(
            """
            UPDATE cases
            SET status = 'APPROVED', approved_at = now() - interval '2 days'
            WHERE id = :id
            """
        ),
        {"id": case_id},
    )
    db.execute(
        text(
            """
            INSERT INTO legal_holds (id, scope, target_id, reason, created_by, created_at)
            VALUES ('hld_test24', 'CASE', :case_id, 'Counsel preservation', 'u-compliance', now())
            """
        ),
        {"case_id": case_id},
    )
    retain_once(db, retention_days=0)
    assert _legal_name(db, case_id) == original_name
    assert _count_for_case(db, "parties", case_id) == parties
    assert _audit_count(db) == audits


def test_legal_hold_validation_and_roles(api: Any, db: Any) -> None:
    denied = api.post(
        "/api/v1/legal-holds",
        headers=ADVISOR,
        json={"scope": "CASE", "targetId": "case-0119", "reason": "Counsel preservation"},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["details"]["reason"] == "ROLE"
    operations = api.post(
        "/api/v1/legal-holds/hld_missing/release",
        headers=OPS,
    )
    assert operations.status_code == 403, operations.text
    assert operations.json()["error"]["details"]["reason"] == "ROLE"

    empty = api.post(
        "/api/v1/legal-holds",
        headers=COMPLIANCE,
        json={"scope": "CASE", "targetId": "case-0119", "reason": " "},
    )
    assert empty.status_code == 400, empty.text
    too_long = api.post(
        "/api/v1/legal-holds",
        headers=COMPLIANCE,
        json={"scope": "CASE", "targetId": "case-0119", "reason": "a" * 501},
    )
    assert too_long.status_code == 400, too_long.text
    missing = api.post(
        "/api/v1/legal-holds",
        headers=COMPLIANCE,
        json={"scope": "CASE", "targetId": "case-does-not-exist", "reason": "Counsel preservation"},
    )
    assert missing.status_code == 404, missing.text

    registration = db.execute(
        text("SELECT registration_number FROM cases WHERE id = 'case-0119'")
    ).scalar()
    if registration and str(registration).strip():
        leaked = api.post(
            "/api/v1/legal-holds",
            headers=COMPLIANCE,
            json={"scope": "CASE", "targetId": "case-0119", "reason": f"Hold {registration}"},
        )
        assert leaked.status_code == 400, leaked.text
        assert str(registration) not in leaked.text


def test_legal_hold_create_release_or_unavailable(api: Any, db: Any) -> None:
    if not _table_exists(db, "legal_holds") and not _ensure_legal_holds(db):
        response = api.post(
            "/api/v1/legal-holds",
            headers=COMPLIANCE,
            json={"scope": "CASE", "targetId": "case-0119", "reason": "Counsel preservation"},
        )
        assert response.status_code == 503, response.text
        pytest.skip("legal_holds 不存在，已确认接口返回 503")
    created = api.post(
        "/api/v1/legal-holds",
        headers=COMPLIANCE,
        json={"scope": "CASE", "targetId": "case-0119", "reason": "Counsel preservation"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["id"].startswith("hld_")
    assert body["scope"] == "CASE"
    assert body["targetId"] == "case-0119"
    assert body["createdBy"] == "u-compliance"
    assert body["releasedBy"] is None
    released = api.post(f"/api/v1/legal-holds/{body['id']}/release", headers=ADMIN)
    assert released.status_code == 200, released.text
    assert released.json()["releasedBy"] == "u-admin"
    assert released.json()["releasedAt"]
    row = db.execute(
        text("SELECT released_by, released_at FROM legal_holds WHERE id = :id"),
        {"id": body["id"]},
    ).one()
    assert row.released_by == "u-admin"
    assert row.released_at is not None


def _insert_valued_event(db: Any, case_id: str = "case-0139") -> tuple[str, dict[str, str], dict[str, str]]:
    rule_version = db.execute(
        text("SELECT rule_version FROM cases WHERE id = :id"),
        {"id": case_id},
    ).scalar()
    event_id = new_id("evt")
    before = {"marker": SECRET_BEFORE}
    after = {"marker": SECRET_AFTER}
    db.execute(
        text(
            """
            INSERT INTO audit_events (
                id, scope, case_id, actor_id, action, summary, at, version,
                before_value, after_value, correlation_id, request_id, rule_version
            ) VALUES (
                :id, 'CASE', :case_id, 'u-compliance', 'PROFILE_UPDATED',
                'Updated province', now(), 1,
                CAST(:before_value AS jsonb), CAST(:after_value AS jsonb),
                'test', 'test', :rule_version
            )
            """
        ),
        {
            "id": event_id,
            "case_id": case_id,
            "before_value": json.dumps(before),
            "after_value": json.dumps(after),
            "rule_version": rule_version,
        },
    )
    db.commit()
    return event_id, before, after


def _prepare_retention_schema(db: Any) -> bool:
    try:
        if not _column_exists(db, "cases", "approved_at"):
            db.execute(text("ALTER TABLE cases ADD COLUMN approved_at timestamptz"))
        _allow_retention_action(db)
        _ensure_legal_holds(db)
    except Exception:
        db.rollback()
        return False
    return True


def _allow_retention_action(db: Any) -> None:
    definition = db.execute(
        text(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'ck_audit_events_action'
            """
        )
    ).scalar() or ""
    actions = re.findall(r"'([A-Z_]+)'", str(definition))
    if "RETENTION_DELETED" in actions:
        return
    actions.append("RETENTION_DELETED")
    literals = ", ".join(f"'{action}'" for action in actions)
    db.execute(text("ALTER TABLE audit_events DROP CONSTRAINT ck_audit_events_action"))
    db.execute(
        text(
            f"ALTER TABLE audit_events ADD CONSTRAINT ck_audit_events_action "
            f"CHECK (action IN ({literals}))"
        )
    )


def _ensure_legal_holds(db: Any) -> bool:
    if _table_exists(db, "legal_holds"):
        return True
    try:
        db.execute(
            text(
                """
                CREATE TABLE legal_holds (
                    id text PRIMARY KEY,
                    scope text NOT NULL,
                    target_id text NOT NULL,
                    reason text NOT NULL,
                    created_by text NOT NULL,
                    created_at timestamptz NOT NULL,
                    released_by text,
                    released_at timestamptz
                )
                """
            )
        )
    except Exception:
        db.rollback()
        return False
    return True


def _ensure_outbox(db: Any) -> bool:
    if _table_exists(db, "audit_outbox"):
        return True
    try:
        db.execute(
            text(
                """
                CREATE TABLE audit_outbox (
                    event_id text PRIMARY KEY,
                    payload jsonb NOT NULL,
                    exported_at timestamptz
                )
                """
            )
        )
    except Exception:
        db.rollback()
        return False
    return True


def _table_exists(db: Any, name: str) -> bool:
    found = db.execute(
        text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = :name
              AND table_schema = ANY (current_schemas(false))
            """
        ),
        {"name": name},
    ).first()
    return found is not None


def _column_exists(db: Any, table: str, column: str) -> bool:
    found = db.execute(
        text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = :table
              AND column_name = :column
              AND table_schema = ANY (current_schemas(false))
            """
        ),
        {"table": table, "column": column},
    ).first()
    return found is not None


def _legal_name(db: Any, case_id: str) -> str:
    return str(db.execute(text("SELECT legal_name FROM cases WHERE id = :id"), {"id": case_id}).scalar())


def _audit_count(db: Any) -> int:
    return int(db.execute(text("SELECT count(*) FROM audit_events")).scalar())


def _count_for_case(db: Any, table: str, case_id: str) -> int:
    if not re.fullmatch(r"[a-z_]+", table):
        raise AssertionError(table)
    return int(
        db.execute(
            text(f"SELECT count(*) FROM {table} WHERE case_id = :case_id"),
            {"case_id": case_id},
        ).scalar()
    )


def _log_blob(caplog: pytest.LogCaptureFixture) -> str:
    parts = [caplog.text]
    for record in caplog.records:
        parts.append(json.dumps(record.__dict__, default=str))
    return "\n".join(parts)
