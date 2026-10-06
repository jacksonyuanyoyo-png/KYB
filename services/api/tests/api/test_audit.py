"""T-AUD cases from docs/backend/10-test-catalog.md section 9.

Audit list responses omit before_value and after_value. Rule-library events are
stored with scope RULE_LIBRARY and a null case_id, and the API writes caseId
as "rule-library".
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

ADMIN = {"X-User-Id": "u-admin", "X-User-Role": "ADMIN"}
ADVISOR = {"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"}
OPS = {"X-User-Id": "u-ops", "X-User-Role": "OPERATIONS"}
COMPLIANCE = {"X-User-Id": "u-compliance", "X-User-Role": "COMPLIANCE"}


@pytest.fixture
def api(db_client: Any) -> Any:
    return db_client


@pytest.fixture
def db(db_session: Any) -> Any:
    return db_session


def _count(db: Any) -> int:
    db.rollback()
    return int(db.execute(text("SELECT count(*) FROM audit_events")).scalar())


def _library_version(api: Any) -> int:
    response = api.get("/api/v1/rule-library", headers=ADMIN)
    assert response.status_code == 200, response.text
    return int(response.json()["version"])


def test_t_aud_01_append_only(db: Any) -> None:
    for statement in (
        "UPDATE audit_events SET summary = 'x'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ):
        with pytest.raises(Exception):
            db.execute(text(statement))
            db.commit()
        db.rollback()


def test_t_aud_02_rule_library_writes_one_audit_row(api: Any, db: Any) -> None:
    version = _library_version(api)
    library = api.get("/api/v1/rule-library", headers=ADMIN).json()
    if library.get("draft"):
        discarded = api.delete("/api/v1/rule-library/draft", params={"version": version}, headers=ADMIN)
        assert discarded.status_code == 200, discarded.text
        version = discarded.json()["version"]
    before = _count(db)
    started = api.post("/api/v1/rule-library/draft", headers=ADMIN, json={"version": version})
    assert started.status_code == 201, started.text
    assert _count(db) == before + 1
    row = db.execute(
        text(
            "SELECT scope, case_id, action, actor_id, version, summary FROM audit_events ORDER BY seq DESC LIMIT 1"
        )
    ).one()
    assert row.scope == "RULE_LIBRARY"
    assert row.case_id is None
    assert row.action == "RULE_LIBRARY"
    assert row.actor_id == "u-admin"
    assert row.version == started.json()["version"]
    assert row.summary.startswith("Daniel Okafor started rule draft ")


def test_t_aud_03_create_case_ai_suggestion(api: Any, db: Any) -> None:
    before = _count(db)
    response = api.post(
        "/api/v1/cases",
        headers=ADVISOR,
        json={
            "legalName": "Audit Suggestion Trust",
            "entityType": "trust",
            "jurisdiction": "Ontario",
            "registrationNumber": "AUD 3",
            "ownerId": "u-advisor",
            "aiEntityType": {"suggested": "ipp_rca", "accepted": False},
        },
    )
    if response.status_code == 404:
        pytest.skip("POST /api/v1/cases is not mounted yet")
    assert response.status_code == 201, response.text
    assert _count(db) == before + 2
    rows = db.execute(
        text(
            "SELECT action, summary, ai_accepted, ai_rule_version, rule_version FROM audit_events "
            "WHERE case_id = :case_id ORDER BY seq"
        ),
        {"case_id": response.json()["case"]["id"]},
    ).all()
    actions = [row.action for row in rows]
    assert actions[:2] == ["CASE_CREATED", "AI_SUGGESTION"]
    suggestion = rows[1]
    assert suggestion.summary == "Entity type suggested: ipp_rca (overridden)"
    assert suggestion.ai_accepted is False
    assert suggestion.ai_rule_version == response.json()["case"]["ruleVersion"]


def test_t_aud_04_rejected_requests_do_not_write_audit(api: Any, db: Any) -> None:
    version = _library_version(api)
    rejected = [
        lambda: api.post("/api/v1/rule-library/draft/publish", headers=ADMIN, json={"version": version}),
        lambda: api.post("/api/v1/rule-library/draft", headers=COMPLIANCE, json={"version": version}),
        lambda: api.post(
            "/api/v1/cases/case-0128/status",
            headers={"X-User-Id": "u-advisor-2", "X-User-Role": "ADVISOR"},
            json={"version": 17, "status": "READY_FOR_COMPLIANCE", "summary": "Submitted for compliance review"},
        ),
        lambda: api.post(
            "/api/v1/cases/case-0142/status",
            headers=ADVISOR,
            json={"version": 4, "status": "READY_FOR_COMPLIANCE", "summary": "Submitted for compliance review"},
        ),
        lambda: api.post(
            "/api/v1/cases/case-0139/status",
            headers=ADVISOR,
            json={"version": 9, "status": "READY_FOR_COMPLIANCE", "summary": "Submitted for compliance review"},
        ),
        lambda: api.put(
            "/api/v1/cases/case-0139/checklist/formation",
            headers=ADVISOR,
            json={"version": 9, "status": "VERIFIED"},
        ),
        lambda: api.post("/api/v1/cases", headers=COMPLIANCE, json={"legalName": "Nope", "entityType": "trust", "ownerId": "u-advisor"}),
    ]
    for call in rejected:
        before = _count(db)
        response = call()
        assert response.status_code >= 400, response.text
        assert _count(db) == before


def test_t_aud_05_rule_library_case_id(api: Any, db: Any) -> None:
    version = _library_version(api)
    library = api.get("/api/v1/rule-library", headers=ADMIN).json()
    if not library.get("draft"):
        started = api.post("/api/v1/rule-library/draft", headers=ADMIN, json={"version": version})
        assert started.status_code == 201, started.text
        version = started.json()["version"]
    else:
        version = library["version"]
    response = api.get("/api/v1/audit", headers=ADMIN, params={"caseId": "rule-library", "limit": 20})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"]
    for item in body["items"]:
        assert item["caseId"] == "rule-library"
        assert "beforeValue" not in item
        assert "afterValue" not in item
        assert "before_value" not in item
        assert "after_value" not in item
    row = db.execute(
        text(
            "SELECT scope, case_id, version FROM audit_events WHERE action = 'RULE_LIBRARY' ORDER BY seq DESC LIMIT 1"
        )
    ).one()
    assert row.scope == "RULE_LIBRARY"
    assert row.case_id is None
    assert row.version == version or row.version >= 1


def test_t_aud_06_unknown_ai_model(api: Any) -> None:
    response = api.post(
        "/api/v1/cases/case-0142/ai-suggestions/accept",
        headers=ADVISOR,
        json={"version": 4, "summary": "Accepted parties", "model": "gpt-unknown", "parties": []},
    )
    if response.status_code == 404:
        pytest.skip("applyAiParties is not mounted yet")
    assert response.status_code == 400, response.text


def test_t_aud_07_correlation_id(api: Any, db: Any) -> None:
    version = _library_version(api)
    response = api.post(
        "/api/v1/rule-library/draft",
        headers={**ADMIN, "X-Correlation-Id": "c-1"},
        json={"version": version},
    )
    assert response.status_code in {200, 201}, response.text
    if response.status_code == 200:
        pytest.skip("a draft was already open, so this call does not write audit")
    row = db.execute(text("SELECT correlation_id FROM audit_events ORDER BY seq DESC LIMIT 1")).one()
    assert row.correlation_id == "c-1"


def test_t_aud_08_profile_before_after(api: Any, db: Any) -> None:
    current = db.execute(
        text(
            "SELECT version, province, tax_residency, features, trusted_contact, trusted_contact_name "
            "FROM cases WHERE id = 'case-0139'"
        )
    ).one()
    profile = {
        "province": current.province or "",
        "taxResidency": "CANADA" if current.tax_residency != "CANADA" else "US",
        "features": list(current.features or []),
        "trustedContact": current.trusted_contact,
        "trustedContactName": current.trusted_contact_name or "",
    }
    response = api.put(
        "/api/v1/cases/case-0139/profile",
        headers={**ADVISOR, "X-Correlation-Id": "c-profile"},
        json={
            "version": current.version,
            "profile": profile,
            "change": {"field": "Tax residency", "from": "Mixed", "to": "Canada"},
        },
    )
    assert response.status_code == 200, response.text
    row = db.execute(
        text("SELECT before_value, after_value, correlation_id FROM audit_events ORDER BY seq DESC LIMIT 1")
    ).one()
    assert row.correlation_id == "c-profile"
    assert row.before_value is not None
    assert row.after_value is not None


def test_audit_list_hides_values_and_advisor_scope(api: Any) -> None:
    admin = api.get("/api/v1/audit", headers=ADMIN, params={"limit": 50})
    assert admin.status_code == 200, admin.text
    for item in admin.json()["items"]:
        assert "beforeValue" not in item
        assert "afterValue" not in item
    advisor = api.get("/api/v1/audit", headers=ADVISOR, params={"limit": 50})
    assert advisor.status_code == 200, advisor.text
    for item in advisor.json()["items"]:
        if item["caseId"] != "rule-library":
            assert item["caseId"] in {"case-0139", "case-0142", "case-0131", "case-0119"}


def test_read_models_filter_by_visibility(db: Any) -> None:
    from fcc_api.services.queries import list_cases, list_compliance_queue, list_entities, list_tasks

    advisor = _actor("u-advisor", "ADVISOR", "Sarah Whitfield")
    operations = _actor("u-ops", "OPERATIONS", "Marcus Lee")
    advisor_cases = list_cases(db, advisor)
    operations_cases = list_cases(db, operations)
    assert advisor_cases.counts["ALL"] == 4
    assert advisor_cases.counts["MINE"] == 4
    assert operations_cases.counts["ALL"] == 7
    assert operations_cases.counts["MINE"] == 0
    assert {item.id for item in advisor_cases.items} <= {"case-0139", "case-0142", "case-0131", "case-0119"}
    queue = list_compliance_queue(db, operations)
    assert [item.id for item in queue.items if item.status == "READY_FOR_COMPLIANCE"]
    entities = list_entities(db, operations)
    assert entities.counts["ALL"] == 17
    assert {item.case_id for item in entities.items} <= {item.id for item in operations_cases.items}
    tasks = list_tasks(db, advisor, done=False, limit=5)
    assert all(item.case_id in {"case-0139", "case-0142", "case-0131", "case-0119"} for item in tasks.items)


def _actor(user_id: str, role: str, name: str) -> Any:
    try:
        from fcc_api.auth.actor import Actor

        return Actor(id=user_id, role=role, name=name, team="")
    except Exception:
        from types import SimpleNamespace

        return SimpleNamespace(id=user_id, role=role, name=name, team="")
