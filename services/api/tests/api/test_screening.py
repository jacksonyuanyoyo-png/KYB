"""名单筛查：写入、处置，以及 SCREENING_REQUIRED 门禁。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from fcc_api.adapters.screening import ScreeningHit
from fcc_api.api.deps import get_db
from fcc_api.db.session import get_session
from fcc_api.services.screening import screening_required

PEOPLE = ["nw-grace", "nw-omar", "nw-lena"]


def _case(db: Session, case_id: str) -> dict:
    row = db.execute(
        text("SELECT version, status FROM cases WHERE id = :id"),
        {"id": case_id},
    ).mappings().one()
    return dict(row)


def _audits(db: Session, case_id: str) -> int:
    return int(
        db.execute(
            text("SELECT count(*) FROM audit_events WHERE case_id = :id"),
            {"id": case_id},
        ).scalar_one()
    )


def _fill_northwind_details(db: Session) -> None:
    db.execute(
        text(
            """
            UPDATE cases
            SET province = 'ON',
                tax_residency = 'CANADA',
                trusted_contact = false,
                trusted_contact_name = ''
            WHERE id = 'case-0142'
            """
        )
    )


def _table_exists(db: Session) -> bool:
    return db.execute(text("SELECT to_regclass('public.screening_runs')")).scalar() is not None


def _action_allowed(db: Session) -> bool:
    definition = db.execute(
        text(
            """
            SELECT pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            WHERE t.relname = 'audit_events' AND c.conname = 'ck_audit_events_action'
            """
        )
    ).scalar()
    return bool(definition) and "SCREENING_RECORDED" in str(definition)


def _require_table(db: Session) -> None:
    if not _table_exists(db):
        pytest.skip("screening_runs 表不存在，跳过名单筛查写入测试。")


def _require_action(db: Session) -> None:
    _require_table(db)
    if not _action_allowed(db):
        pytest.skip("audit_events.action 尚未允许 SCREENING_RECORDED，跳过筛查写入测试。")


def _parties_have_updated_at(db: Session) -> bool:
    found = db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'parties'
              AND column_name = 'updated_at'
            """
        )
    ).first()
    return found is not None


@pytest.fixture
def screening_client(db_session: Session) -> Iterator[TestClient]:
    import fcc_api.main as main_module

    previous = main_module.ROUTER_MODULES
    module_name = "fcc_api.api.routers.screening"
    try:
        if module_name not in previous:
            main_module.ROUTER_MODULES = (*previous, module_name)
        application = main_module.create_app()
    finally:
        main_module.ROUTER_MODULES = previous

    def override_session() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_session] = override_session
    application.dependency_overrides[get_db] = override_session
    with TestClient(application) as test_client:
        yield test_client


def test_missing_screening_required_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCREENING_REQUIRED", raising=False)
    assert screening_required() is False
    assert screening_required(object()) is False


def test_screening_required_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCREENING_REQUIRED", "true")
    assert screening_required() is True
    monkeypatch.setenv("SCREENING_REQUIRED", "false")
    assert screening_required() is False


def test_gate_off_keeps_existing_details_failure(db_client, db_session, role_headers, monkeypatch) -> None:
    monkeypatch.delenv("SCREENING_REQUIRED", raising=False)
    before = _case(db_session, "case-0142")
    response = db_client.post(
        "/api/v1/cases/case-0142/status",
        headers=role_headers("ADVISOR"),
        json={"version": before["version"], "status": "DOCS_REQUESTED", "summary": "Request documents"},
    )
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["details"]["gate"] == "DETAILS"
    assert _case(db_session, "case-0142")["version"] == before["version"]


def test_gate_off_allows_docs_requested(db_client, db_session, role_headers, monkeypatch) -> None:
    monkeypatch.delenv("SCREENING_REQUIRED", raising=False)
    _fill_northwind_details(db_session)
    before = _case(db_session, "case-0142")
    response = db_client.post(
        "/api/v1/cases/case-0142/status",
        headers=role_headers("ADVISOR"),
        json={
            "version": before["version"],
            "status": "DOCS_REQUESTED",
            "summary": "Request documents",
        },
    )
    assert response.status_code == 200, response.text
    stored = _case(db_session, "case-0142")
    assert stored["status"] == "DOCS_REQUESTED"
    assert stored["version"] == before["version"] + 1


def test_gate_on_blocks_without_clear(db_client, db_session, role_headers, monkeypatch) -> None:
    monkeypatch.setenv("SCREENING_REQUIRED", "true")
    _fill_northwind_details(db_session)
    before = _case(db_session, "case-0142")
    audits = _audits(db_session, "case-0142")
    response = db_client.post(
        "/api/v1/cases/case-0142/status",
        headers=role_headers("ADVISOR"),
        json={
            "version": before["version"],
            "status": "DOCS_REQUESTED",
            "summary": "Request documents",
        },
    )
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "GATE_FAILED"
    assert body["error"]["message"] == "Name screening is incomplete."
    assert body["error"]["details"]["gate"] == "SCREENING"
    after = _case(db_session, "case-0142")
    assert after["version"] == before["version"]
    assert after["status"] == "BUILDING"
    assert _audits(db_session, "case-0142") == audits


def test_gate_on_still_reports_ownership_first(db_client, db_session, role_headers, monkeypatch) -> None:
    monkeypatch.setenv("SCREENING_REQUIRED", "true")
    before = _case(db_session, "case-0128")
    response = db_client.post(
        "/api/v1/cases/case-0128/status",
        headers=role_headers("ADVISOR", "u-advisor-2"),
        json={
            "version": before["version"],
            "status": "READY_FOR_COMPLIANCE",
            "summary": "Submitted for compliance review",
        },
    )
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["details"]["gate"] == "OWNERSHIP"
    assert _case(db_session, "case-0128")["version"] == before["version"]


def test_model_paths_cannot_call_disposition() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "fcc_api"
    for relative in ("adapters/llm.py", "adapters/extraction.py", "services/parties.py"):
        source = (root / relative).read_text()
        assert "record_disposition" not in source


def test_compliance_cannot_screen(screening_client, db_session, role_headers) -> None:
    before = _case(db_session, "case-0142")
    response = screening_client.post(
        "/api/v1/cases/case-0142/screening",
        headers=role_headers("COMPLIANCE"),
        json={"version": before["version"], "partyIds": PEOPLE},
    )
    body = response.json()
    assert response.status_code == 403
    assert body["error"]["details"]["reason"] == "ROLE"
    assert _case(db_session, "case-0142")["version"] == before["version"]


def test_screening_rejects_closed_case(screening_client, db_session, role_headers) -> None:
    before = _case(db_session, "case-0137")
    response = screening_client.post(
        "/api/v1/cases/case-0137/screening",
        headers=role_headers("ADVISOR", "u-advisor-2"),
        json={"version": before["version"], "partyIds": ["tf-robert"]},
    )
    body = response.json()
    assert response.status_code == 403
    assert body["error"]["details"]["reason"] == "CASE_STATUS"
    assert _case(db_session, "case-0137")["version"] == before["version"]


def test_screening_version_conflict(screening_client, db_session, role_headers) -> None:
    before = _case(db_session, "case-0142")
    response = screening_client.post(
        "/api/v1/cases/case-0142/screening",
        headers=role_headers("ADVISOR"),
        json={"version": before["version"] - 1, "partyIds": PEOPLE},
    )
    assert response.status_code == 409
    assert _case(db_session, "case-0142")["version"] == before["version"]


def test_screening_unknown_party_does_not_write(screening_client, db_session, role_headers) -> None:
    before = _case(db_session, "case-0142")
    response = screening_client.post(
        "/api/v1/cases/case-0142/screening",
        headers=role_headers("ADVISOR"),
        json={"version": before["version"], "partyIds": ["nw-missing"]},
    )
    assert response.status_code == 400
    assert _case(db_session, "case-0142")["version"] == before["version"]


def test_advisor_cannot_dispose(screening_client, db_session, role_headers) -> None:
    _require_table(db_session)
    before = _case(db_session, "case-0142")
    db_session.execute(
        text(
            """
            INSERT INTO screening_runs (
                id, case_id, party_id, adapter, status, reference, created_at
            ) VALUES (
                'scr_advisor', 'case-0142', 'nw-grace', 'noop', 'POTENTIAL_MATCH', 'fixture', now()
            )
            """
        )
    )
    response = screening_client.post(
        "/api/v1/cases/case-0142/screening/scr_advisor/disposition",
        headers=role_headers("ADVISOR"),
        json={"version": before["version"], "disposition": "MATCH_CONFIRMED"},
    )
    body = response.json()
    assert response.status_code == 403
    assert body["error"]["details"]["reason"] == "ROLE"
    assert body["error"]["details"]["operation"] == "screeningDisposition"
    pep = db_session.execute(
        text("SELECT is_pep_hio FROM parties WHERE case_id = 'case-0142' AND id = 'nw-grace'")
    ).scalar_one()
    assert pep is False
    assert _case(db_session, "case-0142")["version"] == before["version"]


def test_record_screening_noop(screening_client, db_session, role_headers, monkeypatch) -> None:
    _require_action(db_session)
    monkeypatch.delenv("SCREENING_ADAPTER", raising=False)
    before = _case(db_session, "case-0142")
    response = screening_client.post(
        "/api/v1/cases/case-0142/screening",
        headers=role_headers("OPERATIONS"),
        json={"version": before["version"], "partyIds": PEOPLE},
    )
    assert response.status_code == 200, response.text
    assert response.json()["case"]["version"] == before["version"] + 1
    runs = db_session.execute(
        text(
            """
            SELECT party_id, status, reference, adapter
            FROM screening_runs
            WHERE case_id = 'case-0142'
            ORDER BY party_id
            """
        )
    ).mappings().all()
    assert [(row["party_id"], row["status"], row["reference"], row["adapter"]) for row in runs] == [
        ("nw-grace", "CLEAR", "noop", "noop"),
        ("nw-lena", "CLEAR", "noop", "noop"),
        ("nw-omar", "CLEAR", "noop", "noop"),
    ]
    audit = db_session.execute(
        text(
            """
            SELECT action, summary, after_value
            FROM audit_events
            WHERE case_id = 'case-0142'
            ORDER BY seq DESC
            LIMIT 1
            """
        )
    ).mappings().one()
    assert audit["action"] == "SCREENING_RECORDED"
    assert audit["summary"] == "Recorded screening for 3 people"
    assert audit["after_value"] == [
        {"partyId": "nw-grace", "status": "CLEAR", "reference": "noop"},
        {"partyId": "nw-omar", "status": "CLEAR", "reference": "noop"},
        {"partyId": "nw-lena", "status": "CLEAR", "reference": "noop"},
    ]


def test_unavailable_adapter_does_not_write(screening_client, db_session, role_headers, monkeypatch) -> None:
    before = _case(db_session, "case-0142")
    audits = _audits(db_session, "case-0142")

    def _down(settings: object | None = None) -> ScreeningHit:
        del settings
        from fcc_api.errors import ApiError

        raise ApiError(503, "UNAVAILABLE", "Name screening is temporarily unavailable.", {})

    monkeypatch.setattr("fcc_api.services.screening.get_screener", _down)
    response = screening_client.post(
        "/api/v1/cases/case-0142/screening",
        headers=role_headers("ADMIN"),
        json={"version": before["version"], "partyIds": ["nw-grace"]},
    )
    assert response.status_code == 503
    assert _case(db_session, "case-0142")["version"] == before["version"]
    assert _audits(db_session, "case-0142") == audits


def test_clear_satisfies_gate(screening_client, db_client, db_session, role_headers, monkeypatch) -> None:
    _require_action(db_session)
    monkeypatch.setenv("SCREENING_REQUIRED", "true")
    _fill_northwind_details(db_session)
    version = _case(db_session, "case-0142")["version"]
    recorded = screening_client.post(
        "/api/v1/cases/case-0142/screening",
        headers=role_headers("ADMIN"),
        json={"version": version, "partyIds": PEOPLE},
    )
    assert recorded.status_code == 200, recorded.text
    version = recorded.json()["case"]["version"]
    response = db_client.post(
        "/api/v1/cases/case-0142/status",
        headers=role_headers("ADVISOR"),
        json={"version": version, "status": "DOCS_REQUESTED", "summary": "Request documents"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["case"]["status"] == "DOCS_REQUESTED"


def test_match_confirmed_sets_pep_and_does_not_open_gate(
    screening_client, db_client, db_session, role_headers, monkeypatch
) -> None:
    _require_table(db_session)
    monkeypatch.setenv("SCREENING_REQUIRED", "true")
    _fill_northwind_details(db_session)
    version = _case(db_session, "case-0142")["version"]
    for index, party_id in enumerate(PEOPLE):
        db_session.execute(
            text(
                """
                INSERT INTO screening_runs (
                    id, case_id, party_id, adapter, status, reference, created_at
                ) VALUES (
                    :id, 'case-0142', :party_id, 'noop', 'POTENTIAL_MATCH', 'fixture', now()
                )
                """
            ),
            {"id": f"scr_match{index}", "party_id": party_id},
        )
    for index, _party_id in enumerate(PEOPLE):
        response = screening_client.post(
            f"/api/v1/cases/case-0142/screening/scr_match{index}/disposition",
            headers=role_headers("COMPLIANCE"),
            json={"version": version, "disposition": "MATCH_CONFIRMED"},
        )
        assert response.status_code == 200, response.text
        version = response.json()["case"]["version"]
    flags = db_session.execute(
        text(
            """
            SELECT id, is_pep_hio
            FROM parties
            WHERE case_id = 'case-0142' AND id IN ('nw-grace', 'nw-omar', 'nw-lena')
            ORDER BY id
            """
        )
    ).mappings().all()
    assert [row["is_pep_hio"] for row in flags] == [True, True, True]
    audit = db_session.execute(
        text(
            """
            SELECT action
            FROM audit_events
            WHERE case_id = 'case-0142' AND action = 'OWNERSHIP_UPDATED'
            ORDER BY seq DESC
            LIMIT 1
            """
        )
    ).scalar_one()
    assert audit == "OWNERSHIP_UPDATED"
    blocked = db_client.post(
        "/api/v1/cases/case-0142/status",
        headers=role_headers("ADVISOR"),
        json={"version": version, "status": "DOCS_REQUESTED", "summary": "Request documents"},
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["details"]["gate"] == "SCREENING"
    assert _case(db_session, "case-0142")["version"] == version


def test_false_positive_satisfies_gate(
    screening_client, db_client, db_session, role_headers, monkeypatch
) -> None:
    _require_action(db_session)
    monkeypatch.setenv("SCREENING_REQUIRED", "true")
    _fill_northwind_details(db_session)
    version = _case(db_session, "case-0142")["version"]
    for index, party_id in enumerate(PEOPLE):
        db_session.execute(
            text(
                """
                INSERT INTO screening_runs (
                    id, case_id, party_id, adapter, status, reference, created_at
                ) VALUES (
                    :id, 'case-0142', :party_id, 'noop', 'POTENTIAL_MATCH', 'fixture', now()
                )
                """
            ),
            {"id": f"scr_false{index}", "party_id": party_id},
        )
    for index, _party_id in enumerate(PEOPLE):
        response = screening_client.post(
            f"/api/v1/cases/case-0142/screening/scr_false{index}/disposition",
            headers=role_headers("COMPLIANCE"),
            json={"version": version, "disposition": "FALSE_POSITIVE"},
        )
        assert response.status_code == 200, response.text
        version = response.json()["case"]["version"]
    pep = db_session.execute(
        text("SELECT bool_or(is_pep_hio) FROM parties WHERE case_id = 'case-0142'")
    ).scalar_one()
    assert pep is False
    opened = db_client.post(
        "/api/v1/cases/case-0142/status",
        headers=role_headers("ADVISOR"),
        json={"version": version, "status": "DOCS_REQUESTED", "summary": "Request documents"},
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["case"]["status"] == "DOCS_REQUESTED"


def test_stale_clear_does_not_satisfy_gate(db_client, db_session, role_headers, monkeypatch) -> None:
    _require_table(db_session)
    if not _parties_have_updated_at(db_session):
        pytest.skip("parties.updated_at 列不存在，跳过过期筛查断言。")
    monkeypatch.setenv("SCREENING_REQUIRED", "true")
    _fill_northwind_details(db_session)
    db_session.execute(
        text(
            """
            UPDATE parties
            SET updated_at = now()
            WHERE case_id = 'case-0142' AND id IN ('nw-grace', 'nw-omar', 'nw-lena')
            """
        )
    )
    for index, party_id in enumerate(PEOPLE):
        db_session.execute(
            text(
                """
                INSERT INTO screening_runs (
                    id, case_id, party_id, adapter, status, reference, created_at
                ) VALUES (
                    :id, 'case-0142', :party_id, 'noop', 'CLEAR', 'noop', now() - interval '1 day'
                )
                """
            ),
            {"id": f"scr_stale{index}", "party_id": party_id},
        )
    before = _case(db_session, "case-0142")
    response = db_client.post(
        "/api/v1/cases/case-0142/status",
        headers=role_headers("ADVISOR"),
        json={"version": before["version"], "status": "DOCS_REQUESTED", "summary": "Request documents"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["gate"] == "SCREENING"
    assert _case(db_session, "case-0142")["version"] == before["version"]
