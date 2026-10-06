"""AI 建议接口。三张新表不存在时跳过依赖它们的用例。"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.db.session import get_session
from fcc_api.main import create_app

_TABLES = ("document_entities", "document_relations", "ai_suggestions")


def _assistant_done(response) -> dict:
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    done = None
    pieces: list[str] = []
    for frame in response.text.split("\n\n"):
        if not frame.strip():
            continue
        event = "message"
        data = ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        payload = json.loads(data)
        if event == "delta":
            pieces.append(payload["text"])
        elif event == "done":
            done = payload
    assert done is not None
    assert "".join(pieces) == done["text"]
    return done


def _present_tables(db: Session) -> set[str]:
    rows = db.execute(
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('document_entities', 'document_relations', 'ai_suggestions')
            """
        )
    ).scalars()
    return set(rows)


def _require_tables(db: Session) -> None:
    missing = [name for name in _TABLES if name not in _present_tables(db)]
    if missing:
        pytest.skip("缺少表 " + ", ".join(missing) + "，跳过依赖新表的接口测试。")


@pytest.fixture
def ai_client(db_session: Session) -> Iterator[TestClient]:
    application = create_app()

    def override_session() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_session] = override_session
    application.dependency_overrides[get_db] = override_session
    with TestClient(application) as test_client:
        yield test_client


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


def _parties(db: Session, case_id: str) -> int:
    return int(
        db.execute(
            text("SELECT count(*) FROM parties WHERE case_id = :id"),
            {"id": case_id},
        ).scalar_one()
    )


def test_classify_noop_is_unavailable(ai_client: TestClient, db_session: Session, role_headers) -> None:
    before = _case(db_session, "case-0139")
    audits = _audits(db_session, "case-0139")
    parties = _parties(db_session, "case-0139")
    response = ai_client.post(
        "/api/v1/cases/case-0139/ai/classify-entity",
        headers=role_headers("ADVISOR"),
        json={"legalName": "Maple Ridge Holdings Inc.", "notes": ""},
    )
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "UNAVAILABLE"
    assert body["error"]["message"] == "The assistant is not configured."
    assert _case(db_session, "case-0139")["version"] == before["version"]
    assert _audits(db_session, "case-0139") == audits
    assert _parties(db_session, "case-0139") == parties


def test_entity_type_question_does_not_call_a_missing_model(
    ai_client: TestClient,
    db_session: Session,
    role_headers,
) -> None:
    before = _case(db_session, "case-0139")
    response = ai_client.post(
        "/api/v1/cases/case-0139/ai/assistant",
        headers=role_headers("ADVISOR"),
        json={"message": "Which entity type should this be?"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UNAVAILABLE"
    assert _case(db_session, "case-0139")["version"] == before["version"]


def test_assistant_why_uses_the_rule_engine(
    ai_client: TestClient,
    db_session: Session,
    role_headers,
) -> None:
    _require_tables(db_session)
    before = _case(db_session, "case-0128")
    parties = _parties(db_session, "case-0128")
    response = ai_client.post(
        "/api/v1/cases/case-0128/ai/assistant",
        headers=role_headers("OPERATIONS"),
        json={"message": "Why can't I continue?"},
    )
    body = _assistant_done(response)
    assert body["model"] == "rule-engine"
    assert body["kind"] == "ANSWER"
    assert body["proposal"] is None
    assert "91%" in body["text"]
    assert _case(db_session, "case-0128")["version"] == before["version"]
    assert _parties(db_session, "case-0128") == parties
    stored = db_session.execute(
        text("SELECT stage, prompt_version, model FROM ai_suggestions WHERE id = :id"),
        {"id": body["suggestionId"]},
    ).mappings().one()
    assert stored["stage"] == "SUGGESTED"
    assert stored["model"] == "rule-engine"
    assert stored["prompt_version"] is None


def test_assistant_parse_instruction_does_not_save_parties(
    ai_client: TestClient,
    db_session: Session,
    role_headers,
) -> None:
    _require_tables(db_session)
    before = _case(db_session, "case-0139")
    parties = _parties(db_session, "case-0139")
    response = ai_client.post(
        "/api/v1/cases/case-0139/ai/assistant",
        headers=role_headers("ADVISOR"),
        json={"message": "Alice Chen holds 60% and is a director"},
    )
    body = _assistant_done(response)
    assert body["model"] == "rule-engine"
    assert body["kind"] == "PROPOSAL"
    assert body["proposal"]["legalName"] == "Alice Chen"
    assert body["proposal"]["ownershipPercent"] == 60
    assert body["proposal"]["parentName"] == "Maple Ridge Holdings Inc."
    assert _case(db_session, "case-0139")["version"] == before["version"]
    assert _parties(db_session, "case-0139") == parties


def test_assistant_approve_case_has_empty_proposal(
    ai_client: TestClient,
    db_session: Session,
    role_headers,
) -> None:
    _require_tables(db_session)
    before = _case(db_session, "case-0139")
    response = ai_client.post(
        "/api/v1/cases/case-0139/ai/assistant",
        headers=role_headers("ADVISOR"),
        json={"message": "Approve this case and clear PEP for Alice"},
    )
    body = _assistant_done(response)
    assert body["proposal"] is None
    assert body["model"] == "rule-engine"
    assert _case(db_session, "case-0139")["version"] == before["version"]


def test_compliance_cannot_extract_formation(
    ai_client: TestClient,
    role_headers,
) -> None:
    response = ai_client.post(
        "/api/v1/cases/case-0139/ai/extract-formation",
        headers=role_headers("COMPLIANCE"),
        json={"documentIds": ["d-2"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "ROLE"


def test_extract_formation_pending_is_incomplete(
    ai_client: TestClient,
    db_session: Session,
    role_headers,
) -> None:
    _require_tables(db_session)
    before = _case(db_session, "case-0139")
    suggestions = int(
        db_session.execute(
            text("SELECT count(*) FROM ai_suggestions WHERE case_id = 'case-0139'")
        ).scalar_one()
    )
    db_session.execute(
        text(
            """
            UPDATE case_documents
            SET extraction = 'EXTRACTED', relation_status = 'PENDING'
            WHERE id = 'd-2'
            """
        )
    )
    db_session.flush()
    response = ai_client.post(
        "/api/v1/cases/case-0139/ai/extract-formation",
        headers=role_headers("ADVISOR"),
        json={"documentIds": ["d-2"]},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "GATE_FAILED"
    assert body["error"]["details"]["gate"] == "EXTRACTION_INCOMPLETE"
    assert body["error"]["message"] == "Document reading has not finished."
    assert _case(db_session, "case-0139")["version"] == before["version"]
    assert int(
        db_session.execute(
            text("SELECT count(*) FROM ai_suggestions WHERE case_id = 'case-0139'")
        ).scalar_one()
    ) == suggestions


def test_pre_review_keeps_checklist_status(
    ai_client: TestClient,
    db_session: Session,
    role_headers,
) -> None:
    _require_tables(db_session)
    before = db_session.execute(
        text(
            """
            SELECT requirement_id, status
            FROM checklist_items
            WHERE case_id = 'case-0139'
            ORDER BY requirement_id
            """
        )
    ).mappings().all()
    version = _case(db_session, "case-0139")["version"]
    response = ai_client.post(
        "/api/v1/cases/case-0139/ai/pre-review",
        headers=role_headers("ADVISOR"),
    )
    assert response.status_code == 200
    findings = response.json()["findings"]
    assert findings
    assert all(item["severity"] in {"high", "medium", "low"} for item in findings)
    after = db_session.execute(
        text(
            """
            SELECT requirement_id, status
            FROM checklist_items
            WHERE case_id = 'case-0139'
            ORDER BY requirement_id
            """
        )
    ).mappings().all()
    assert [dict(row) for row in after] == [dict(row) for row in before]
    assert _case(db_session, "case-0139")["version"] == version


def test_extract_formation_reads_ready_relations_without_changing_parties(
    ai_client: TestClient,
    db_session: Session,
    role_headers,
) -> None:
    _require_tables(db_session)
    before = _case(db_session, "case-0139")
    parties = _parties(db_session, "case-0139")
    db_session.execute(
        text(
            """
            INSERT INTO document_extractions (
                id, document_id, status, adapter, started_at, finished_at,
                relation_prompt_version, relation_model
            ) VALUES (
                'ext_ai_test', 'd-2', 'EXTRACTED', 'test', now(), now(),
                'relations.v1', 'doc-extract-demo'
            )
            """
        )
    )
    db_session.execute(
        text(
            """
            INSERT INTO document_entities (
                id, extraction_id, document_id, temp_key, kind, legal_name,
                title, country, confidence, page_no, citation
            ) VALUES
            (
                'ent_ai_high', 'ext_ai_test', 'd-2', 'e1', 'PERSON', 'Jordan Blake',
                'Director', 'Canada', 0.950, 1,
                'Articles_of_Incorporation_2748113.pdf · p.1 · Jordan Blake owns 60%'
            ),
            (
                'ent_ai_low', 'ext_ai_test', 'd-2', 'e2', 'PERSON', 'Mira Shah',
                'Authorized Signatory', 'Canada', 0.720, 1,
                'Articles_of_Incorporation_2748113.pdf · p.1 · Mira Shah'
            )
            """
        )
    )
    db_session.execute(
        text(
            """
            INSERT INTO document_relations (
                id, extraction_id, relation_type, from_temp_key, to_temp_key,
                ownership_percent, confidence, page_no, citation
            ) VALUES (
                'rel_ai_owns', 'ext_ai_test', 'OWNS', 'e1', 'CASE_ROOT',
                60, 0.950, 1,
                'Articles_of_Incorporation_2748113.pdf · p.1 · Jordan Blake owns 60%'
            ), (
                'rel_ai_dir', 'ext_ai_test', 'DIRECTOR_OF', 'e1', 'CASE_ROOT',
                NULL, 0.900, 1,
                'Articles_of_Incorporation_2748113.pdf · p.1 · director'
            )
            """
        )
    )
    db_session.execute(
        text(
            """
            UPDATE case_documents
            SET extraction = 'EXTRACTED', relation_status = 'READY'
            WHERE id = 'd-2'
            """
        )
    )
    db_session.flush()
    response = ai_client.post(
        "/api/v1/cases/case-0139/ai/extract-formation",
        headers=role_headers("ADVISOR"),
        json={"documentIds": ["d-2"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "doc-extract-demo"
    by_name = {item["legalName"]: item for item in body["entities"]}
    assert by_name["Jordan Blake"]["ownershipPercent"] == 60
    assert by_name["Jordan Blake"]["isController"] is True
    assert by_name["Jordan Blake"]["includeByDefault"] is True
    assert "isPepHio" not in by_name["Jordan Blake"]
    assert by_name["Mira Shah"]["includeByDefault"] is False
    assert _case(db_session, "case-0139")["version"] == before["version"]
    assert _parties(db_session, "case-0139") == parties
    stored = db_session.execute(
        text(
            """
            SELECT ai_accepted, ai_model, version, after_value
            FROM audit_events
            WHERE case_id = 'case-0139' AND action = 'AI_SUGGESTION'
            ORDER BY seq DESC
            LIMIT 1
            """
        )
    ).mappings().one()
    assert stored["ai_accepted"] is False
    assert stored["ai_model"] == "doc-extract-demo"
    assert stored["version"] == before["version"]
    assert stored["after_value"]["ai"]["stage"] == "SUGGESTED"
    assert stored["after_value"]["suggestion"]["suggestionId"] == body["suggestionId"]


def test_relation_job_noop_does_not_change_version(
    db_session: Session,
) -> None:
    _require_tables(db_session)
    column = db_session.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'case_documents' AND column_name = 'relation_status'
            """
        )
    ).first()
    if column is None:
        pytest.skip("case_documents.relation_status 不存在。")
    from fcc_api.jobs.relations import run_once

    before = _case(db_session, "case-0139")
    db_session.execute(
        text(
            """
            UPDATE case_documents
            SET relation_status = 'PENDING', requirement_id = 'formation'
            WHERE id = 'd-2'
            """
        )
    )
    db_session.execute(
        text(
            """
            UPDATE case_documents
            SET relation_status = 'PENDING', requirement_id = 'identity'
            WHERE id = 'd-1'
            """
        )
    )
    db_session.flush()
    updated = run_once(db_session)
    assert updated == 2
    statuses = {
        row["id"]: row["relation_status"]
        for row in db_session.execute(
            text(
                """
                SELECT id, relation_status
                FROM case_documents
                WHERE id IN ('d-1', 'd-2')
                """
            )
        ).mappings()
    }
    assert statuses["d-1"] == "NOT_APPLICABLE"
    assert statuses["d-2"] == "NOT_APPLICABLE"
    assert _case(db_session, "case-0139")["version"] == before["version"]
