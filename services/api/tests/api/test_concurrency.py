"""并发用例 T-CONC-01 到 T-CONC-06。"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.db.session import get_session
from fcc_api.main import create_app


def _case(db: Session, case_id: str) -> dict:
    return dict(
        db.execute(
            text("SELECT version, status, submitted_at, tax_residency, updated_at FROM cases WHERE id = :id"),
            {"id": case_id},
        ).mappings().one()
    )


def _audits(db: Session, case_id: str) -> int:
    return int(db.execute(text("SELECT count(*) FROM audit_events WHERE case_id = :id"), {"id": case_id}).scalar_one())


def _parties(db: Session, case_id: str) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT id, parent_id, kind, legal_name, entity_type, country, title,
                   us_tax_class, ownership_percent, is_controller, is_signing_authority,
                   is_us_person, is_pep_hio
            FROM parties WHERE case_id = :id ORDER BY position
            """
        ),
        {"id": case_id},
    ).mappings()
    payload = []
    for row in rows:
        item = {
            "id": row["id"],
            "parentId": row["parent_id"],
            "kind": row["kind"],
            "legalName": row["legal_name"],
            "ownershipPercent": float(row["ownership_percent"]),
            "isController": row["is_controller"],
            "isSigningAuthority": row["is_signing_authority"],
            "isUsPerson": row["is_us_person"],
            "isPepHio": row["is_pep_hio"],
        }
        if row["entity_type"] is not None:
            item["entityType"] = row["entity_type"]
        if row["country"] is not None:
            item["country"] = row["country"]
        if row["title"] is not None:
            item["title"] = row["title"]
        if row["us_tax_class"] is not None:
            item["usTaxClass"] = row["us_tax_class"]
        payload.append(item)
    return payload

CONFLICT = (
    "This case was changed by someone else. Reload to see the latest version before saving again."
)
PDF = b"%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_t_conc_01_second_write_conflicts(db_client, db_session, role_headers) -> None:
    ops = role_headers("OPERATIONS")
    opened = db_client.post(
        "/api/v1/cases/case-0139/uploads",
        headers=ops,
        json={
            "requirementId": "margin",
            "files": [{
                "fileName": "Margin_addendum.pdf",
                "sizeBytes": len(PDF),
                "mimeType": "application/pdf",
            }],
        },
    )
    assert opened.status_code == 201, opened.text
    slot = opened.json()["slots"][0]
    stored = db_client.put(slot["url"], headers=slot["headers"], content=PDF)
    assert stored.status_code in (200, 204)
    uploaded = db_client.post(
        "/api/v1/cases/case-0139/documents",
        headers=ops,
        json={
            "version": 9,
            "batchId": opened.json()["batchId"],
            "uploadIds": [slot["uploadId"]],
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["case"]["version"] == 10
    audits = _audits(db_session, "case-0139")
    profile = db_client.put(
        "/api/v1/cases/case-0139/profile",
        headers=role_headers("ADVISOR"),
        json={
            "version": 9,
            "profile": {
                "province": "ON",
                "taxResidency": "CANADA",
                "features": ["MARGIN", "OPTIONS"],
                "trustedContact": True,
                "trustedContactName": "Helen Chen",
            },
            "change": {"field": "Tax residency", "from": "Mixed", "to": "Canada"},
        },
    )
    body = profile.json()
    assert profile.status_code == 409
    assert body["error"]["code"] == "VERSION_CONFLICT"
    assert body["error"]["message"] == CONFLICT
    assert body["error"]["details"]["clientVersion"] == 9
    assert body["error"]["details"]["currentVersion"] == 10
    stored_case = _case(db_session, "case-0139")
    assert stored_case["version"] == 10
    assert stored_case["tax_residency"] == "MIXED"
    assert _audits(db_session, "case-0139") == audits


def _audits_action(db: Session, case_id: str, action: str) -> int:
    return int(
        db.execute(
            text(
                """
                SELECT count(*) FROM audit_events
                WHERE case_id = :id AND action = :action
                """
            ),
            {"id": case_id, "action": action},
        ).scalar_one()
    )


def test_t_conc_02_simultaneous_writers_one_wins(test_database, role_headers) -> None:
    engine, reason, _url = test_database
    if engine is None:
        pytest.skip(reason)
    application = create_app()

    def override_session():
        session = Session(bind=engine, autoflush=False, expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_session] = override_session
    application.dependency_overrides[get_db] = override_session
    parties = None
    with Session(bind=engine) as session:
        parties = _parties(session, "case-0142")
        original = _case(session, "case-0142")
        audits_before = _audits(session, "case-0142")
    parties.append({
        "id": "nw-extra",
        "parentId": "nw-root",
        "kind": "PERSON",
        "legalName": "Extra Director",
        "country": "Canada",
        "title": "Director",
        "ownershipPercent": 0,
        "isController": True,
        "isSigningAuthority": False,
        "isUsPerson": False,
        "isPepHio": False,
    })
    barrier = threading.Barrier(2)
    results: list[httpx.Response] = []

    def _call(method: str, path: str, headers: dict, payload: dict) -> None:
        barrier.wait(timeout=10)
        with TestClient(application) as client:
            response = client.request(method, path, headers=headers, json=payload)
            results.append(response)

    advisor = role_headers("ADVISOR")
    ops = role_headers("OPERATIONS")
    first = threading.Thread(
        target=_call,
        args=(
            "PUT",
            "/api/v1/cases/case-0142/parties",
            advisor,
            {"version": 4, "parties": parties, "summary": "Added a director"},
        ),
    )
    second = threading.Thread(
        target=_call,
        args=(
            "PUT",
            "/api/v1/cases/case-0142/profile",
            ops,
            {
                "version": 4,
                "profile": {
                    "province": "ON",
                    "taxResidency": "CANADA",
                    "features": [],
                    "trustedContact": False,
                    "trustedContactName": "",
                },
                "change": {"field": "Province", "from": "", "to": "Ontario"},
            },
        ),
    )
    first.start()
    second.start()
    first.join(timeout=20)
    second.join(timeout=20)
    try:
        codes = sorted(item.status_code for item in results)
        assert codes == [200, 409]
        assert any(item.status_code == 409 and item.json()["error"]["message"] == CONFLICT for item in results)
        with Session(bind=engine) as session:
            stored = _case(session, "case-0142")
            assert stored["version"] == 5
            assert _audits(session, "case-0142") == audits_before + 1
    finally:
        with Session(bind=engine) as session:
            with session.begin():
                session.execute(
                    text(
                        """
                        DELETE FROM parties
                        WHERE case_id = 'case-0142' AND id = 'nw-extra'
                        """
                    )
                )
                session.execute(
                    text(
                        """
                        UPDATE cases
                        SET version = :version,
                            province = '',
                            tax_residency = 'CANADA',
                            trusted_contact = NULL,
                            updated_at = :updated_at
                        WHERE id = 'case-0142'
                        """
                    ),
                    {"version": original["version"], "updated_at": original["updated_at"]},
                )


def test_t_conc_03_second_toggle_conflicts(db_client, db_session, role_headers) -> None:
    headers = role_headers("ADVISOR", "u-advisor-2")
    first = db_client.post(
        "/api/v1/cases/case-0128/tasks/task-1/toggle",
        headers=headers,
        json={"version": 17},
    )
    assert first.status_code == 200, first.text
    second = db_client.post(
        "/api/v1/cases/case-0128/tasks/task-1/toggle",
        headers=headers,
        json={"version": 17},
    )
    assert second.status_code == 409
    assert second.json()["error"]["message"] == CONFLICT
    done = db_session.execute(text("SELECT done FROM review_tasks WHERE id = 'task-1'")).scalar_one()
    assert done is True
    assert _case(db_session, "case-0128")["version"] == 18
    assert _audits_action(db_session, "case-0128", "TASK_UPDATED") == 1


def test_t_conc_04_rule_library_version(db_client, role_headers) -> None:
    headers = role_headers("ADMIN")
    first = db_client.post(
        "/api/v1/rule-library/draft/extras",
        headers=headers,
        json={
            "version": 1,
            "rule": {
                "id": "rule_conc_a",
                "name": "First extra",
                "section": "Entity Formation & Authorization",
                "conditional": True,
                "source": "Internal",
                "reason": "Needed for the concurrency test.",
                "enabled": True,
                "trigger": {"kind": "ALWAYS"},
            },
        },
    )
    second = db_client.post(
        "/api/v1/rule-library/draft/extras",
        headers=headers,
        json={
            "version": 1,
            "rule": {
                "id": "rule_conc_b",
                "name": "Second extra",
                "section": "Entity Formation & Authorization",
                "conditional": True,
                "source": "Internal",
                "reason": "Should lose the version race.",
                "enabled": True,
                "trigger": {"kind": "ALWAYS"},
            },
        },
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 409
    assert second.json()["error"]["details"]["resource"] == "ruleLibrary"


def test_t_conc_05_partial_upload_rejected(db_client, db_session, role_headers) -> None:
    headers = role_headers("OPERATIONS")
    before = _case(db_session, "case-0139")
    audits = _audits(db_session, "case-0139")
    opened = db_client.post(
        "/api/v1/cases/case-0139/uploads",
        headers=headers,
        json={
            "requirementId": "margin",
            "files": [
                {"fileName": "real.pdf", "sizeBytes": len(PDF), "mimeType": "application/pdf"},
                {"fileName": "fake.pdf", "sizeBytes": len(PNG), "mimeType": "application/pdf"},
            ],
        },
    )
    assert opened.status_code == 201, opened.text
    slots = opened.json()["slots"]
    for slot, content in zip(slots, (PDF, PNG), strict=True):
        stored = db_client.put(slot["url"], headers=slot["headers"], content=content)
        assert stored.status_code in (200, 204)
    finished = db_client.post(
        "/api/v1/cases/case-0139/documents",
        headers=headers,
        json={
            "version": 9,
            "batchId": opened.json()["batchId"],
            "uploadIds": [slot["uploadId"] for slot in slots],
        },
    )
    assert finished.status_code == 400
    files = finished.json()["error"]["details"]["files"]
    assert files == [{"uploadId": slots[1]["uploadId"], "reason": "TYPE_MISMATCH"}]
    assert _case(db_session, "case-0139")["version"] == before["version"]
    assert _audits(db_session, "case-0139") == audits


def test_t_conc_06_invalid_tree_does_not_write(db_client, db_session, role_headers) -> None:
    parties = _parties(db_session, "case-0139")
    parties.append({
        "id": "mr-other-root",
        "parentId": None,
        "kind": "ENTITY",
        "legalName": "Other Root",
        "entityType": "corporation",
        "ownershipPercent": 100,
        "isController": False,
        "isSigningAuthority": False,
        "isUsPerson": False,
        "isPepHio": False,
    })
    before = _case(db_session, "case-0139")
    count = db_session.execute(
        text("SELECT count(*) FROM parties WHERE case_id = 'case-0139'")
    ).scalar_one()
    response = db_client.put(
        "/api/v1/cases/case-0139/parties",
        headers=role_headers("ADVISOR"),
        json={"version": 9, "parties": parties, "summary": "Two roots"},
    )
    assert response.status_code == 400
    after = _case(db_session, "case-0139")
    assert after["version"] == before["version"]
    assert after["updated_at"] == before["updated_at"]
    assert db_session.execute(
        text("SELECT count(*) FROM parties WHERE case_id = 'case-0139'")
    ).scalar_one() == count

