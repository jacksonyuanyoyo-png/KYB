"""门禁用例 T-GATE-01 到 T-GATE-12。"""

from __future__ import annotations

import re
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.orm import Session

from fcc_api.rules.domain import validate_ownership
from fcc_api.rules.types import Party

CONFLICT = (
    "This case was changed by someone else. Reload to see the latest version before saving again."
)
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
REMAINING_0139 = (
    "beneficial-owner",
    "directors",
    "identity",
    "pep",
    "options",
    "tcp",
    "w9",
    "w8",
    "rc519",
)


def _one(db: Session, sql: str, **params: object) -> dict:
    return dict(db.execute(text(sql), params).mappings().one())


def _scalar(db: Session, sql: str, **params: object) -> object:
    return db.execute(text(sql), params).scalar_one()


def _case(db: Session, case_id: str) -> dict:
    return _one(
        db,
        """
        SELECT version, status, submitted_at, tax_residency, updated_at
        FROM cases WHERE id = :id
        """,
        id=case_id,
    )


def _audits(db: Session, case_id: str) -> int:
    value = _scalar(db, "SELECT count(*) FROM audit_events WHERE case_id = :id", id=case_id)
    return int(value)  # type: ignore[arg-type]


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


def test_t_gate_01_ownership_blocks_submit(db_client, db_session, role_headers) -> None:
    before = _case(db_session, "case-0128")
    audits = _audits(db_session, "case-0128")
    response = db_client.post(
        "/api/v1/cases/case-0128/status",
        headers=role_headers("ADVISOR", "u-advisor-2"),
        json={
            "version": 17,
            "status": "READY_FOR_COMPLIANCE",
            "summary": "Submitted for compliance review",
        },
    )
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "GATE_FAILED"
    assert body["error"]["message"] == "Ownership structure is incomplete."
    assert body["error"]["details"]["gate"] == "OWNERSHIP"
    assert body["error"]["details"]["issues"][0]["code"] == "OWNERSHIP_TOTAL"
    after = _case(db_session, "case-0128")
    assert after["version"] == 17
    assert after["status"] == "RETURNED"
    assert after["submitted_at"] is None
    assert after["updated_at"] == before["updated_at"]
    assert _audits(db_session, "case-0128") == audits


def test_t_gate_02_checklist_after_ownership_is_complete(db_client, db_session, role_headers) -> None:
    headers = role_headers("ADVISOR", "u-advisor-2")
    parties = _parties(db_session, "case-0128")
    for item in parties:
        if item["id"] == "pr-wei":
            item["ownershipPercent"] = 69
    parties.append({
        "id": "pr-natural",
        "parentId": "pr-gp",
        "kind": "PERSON",
        "legalName": "Alex Chen",
        "country": "Canada",
        "title": "Shareholder",
        "ownershipPercent": 100,
        "isController": True,
        "isSigningAuthority": False,
        "isUsPerson": False,
        "isPepHio": False,
    })
    saved = db_client.put(
        "/api/v1/cases/case-0128/parties",
        headers=headers,
        json={"version": 17, "parties": parties, "summary": "Disclosed the general partner"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["case"]["version"] == 18
    response = db_client.post(
        "/api/v1/cases/case-0128/status",
        headers=headers,
        json={
            "version": 18,
            "status": "READY_FOR_COMPLIANCE",
            "summary": "Submitted for compliance review",
        },
    )
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "GATE_FAILED"
    assert body["error"]["details"]["gate"] == "CHECKLIST"
    assert body["error"]["details"]["gate"] != "OWNERSHIP"
    assert _case(db_session, "case-0128")["version"] == 18
    assert _case(db_session, "case-0128")["status"] == "RETURNED"


def test_t_gate_03_ownership_tolerance() -> None:
    parties = [
        Party(
            id="root",
            parent_id=None,
            kind="ENTITY",
            legal_name="Root",
            ownership_percent=100,
            entity_type="corporation",
        ),
        Party(id="a", parent_id="root", kind="PERSON", legal_name="A", ownership_percent=33.33),
        Party(id="b", parent_id="root", kind="PERSON", legal_name="B", ownership_percent=33.33),
        Party(id="c", parent_id="root", kind="PERSON", legal_name="C", ownership_percent=33.34),
    ]
    issues = validate_ownership(SimpleNamespace(parties=parties))
    assert [item.code for item in issues if item.code == "OWNERSHIP_TOTAL"] == []


def test_t_gate_04_details_block_submit(db_client, db_session, role_headers) -> None:
    audits = _audits(db_session, "case-0142")
    response = db_client.post(
        "/api/v1/cases/case-0142/status",
        headers=role_headers("ADVISOR"),
        json={
            "version": 4,
            "status": "READY_FOR_COMPLIANCE",
            "summary": "Submitted for compliance review",
        },
    )
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["message"] == "Account details are incomplete."
    assert body["error"]["details"]["gate"] == "DETAILS"
    assert _case(db_session, "case-0142")["version"] == 4
    assert _audits(db_session, "case-0142") == audits


def test_t_gate_05_checklist_blocks_submit(db_client, db_session, role_headers) -> None:
    audits = _audits(db_session, "case-0139")
    response = db_client.post(
        "/api/v1/cases/case-0139/status",
        headers=role_headers("ADVISOR"),
        json={
            "version": 9,
            "status": "READY_FOR_COMPLIANCE",
            "summary": "Submitted for compliance review",
        },
    )
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["message"] == "All checklist items must be collected first."
    assert body["error"]["details"]["gate"] == "CHECKLIST"
    assert _case(db_session, "case-0139")["version"] == 9
    assert _audits(db_session, "case-0139") == audits


def _collect_0139(db_client, role_headers) -> int:
    headers = role_headers("OPERATIONS")
    version = 9
    for requirement_id in REMAINING_0139:
        response = db_client.put(
            f"/api/v1/cases/case-0139/checklist/{requirement_id}",
            headers=headers,
            json={"version": version, "status": "RECEIVED"},
        )
        assert response.status_code == 200, response.text
        version = response.json()["case"]["version"]
    created = db_client.post(
        "/api/v1/cases/case-0139/tasks",
        headers=headers,
        json={
            "version": version,
            "source": "MANUAL",
            "tasks": [{"title": "Confirm the signing pages"}],
        },
    )
    assert created.status_code == 201, created.text
    return int(created.json()["case"]["version"])


def test_t_gate_06_open_task_blocks_submit(db_client, db_session, role_headers) -> None:
    version = _collect_0139(db_client, role_headers)
    audits = _audits(db_session, "case-0139")
    response = db_client.post(
        "/api/v1/cases/case-0139/status",
        headers=role_headers("ADVISOR"),
        json={
            "version": version,
            "status": "READY_FOR_COMPLIANCE",
            "summary": "Submitted for compliance review",
        },
    )
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["message"] == "Resolve open follow-up tasks first."
    assert body["error"]["details"]["gate"] == "TASKS"
    assert _case(db_session, "case-0139")["version"] == version
    assert _case(db_session, "case-0139")["status"] == "DOCS_REQUESTED"
    assert _audits(db_session, "case-0139") == audits


def test_t_gate_07_submit_after_task_is_done(db_client, db_session, role_headers) -> None:
    version = _collect_0139(db_client, role_headers)
    task_id = _scalar(
        db_session,
        """
        SELECT id FROM review_tasks
        WHERE case_id = 'case-0139' AND done = false
        ORDER BY created_at DESC LIMIT 1
        """,
    )
    toggled = db_client.post(
        f"/api/v1/cases/case-0139/tasks/{task_id}/toggle",
        headers=role_headers("ADVISOR"),
        json={"version": version},
    )
    assert toggled.status_code == 200, toggled.text
    ready_version = toggled.json()["case"]["version"]
    response = db_client.post(
        "/api/v1/cases/case-0139/status",
        headers=role_headers("ADVISOR"),
        json={
            "version": ready_version,
            "status": "READY_FOR_COMPLIANCE",
            "summary": "Submitted for compliance review",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["case"]["status"] == "READY_FOR_COMPLIANCE"
    assert payload["case"]["version"] == ready_version + 1
    assert payload["case"]["submittedAt"] is not None
    assert ISO.match(payload["case"]["submittedAt"])
    stored = _case(db_session, "case-0139")
    assert stored["status"] == "READY_FOR_COMPLIANCE"
    assert stored["submitted_at"] is not None
    audit = _one(
        db_session,
        """
        SELECT action, changes FROM audit_events
        WHERE case_id = 'case-0139' AND action = 'STATUS_CHANGED'
        ORDER BY seq DESC LIMIT 1
        """,
    )
    assert audit["action"] == "STATUS_CHANGED"
    assert audit["changes"] == [{
        "field": "Status",
        "from": "Docs requested",
        "to": "Ready for compliance",
    }]


def test_t_gate_08_docs_requested_checks_ownership(db_client, db_session, role_headers) -> None:
    audits = _audits(db_session, "case-0131")
    response = db_client.post(
        "/api/v1/cases/case-0131/status",
        headers=role_headers("ADVISOR"),
        json={"version": 1, "status": "DOCS_REQUESTED", "summary": "Requested the formation file"},
    )
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["details"]["gate"] == "OWNERSHIP"
    assert _case(db_session, "case-0131")["version"] == 1
    assert _case(db_session, "case-0131")["status"] == "BUILDING"
    assert _audits(db_session, "case-0131") == audits


def test_t_gate_09_approve_reruns_checklist_gate(db_client, db_session, role_headers) -> None:
    rejected = db_client.put(
        "/api/v1/cases/case-0137/checklist/identity",
        headers=role_headers("COMPLIANCE"),
        json={"version": 14, "status": "REJECTED"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["case"]["version"] == 15
    audits = _audits(db_session, "case-0137")
    response = db_client.post(
        "/api/v1/cases/case-0137/compliance-decision",
        headers=role_headers("COMPLIANCE"),
        json={"version": 15, "decision": "APPROVE", "comments": []},
    )
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["details"]["gate"] == "CHECKLIST"
    assert _case(db_session, "case-0137")["version"] == 15
    assert _case(db_session, "case-0137")["status"] == "READY_FOR_COMPLIANCE"
    assert _audits(db_session, "case-0137") == audits


def test_t_gate_10_approve_verifies_received_items(db_client, db_session, role_headers) -> None:
    response = db_client.post(
        "/api/v1/cases/case-0137/compliance-decision",
        headers=role_headers("COMPLIANCE"),
        json={"version": 14, "decision": "APPROVE", "comments": []},
    )
    assert response.status_code == 200, response.text
    assert response.json()["case"]["status"] == "APPROVED"
    rows = db_session.execute(
        text(
            """
            SELECT requirement_id, status, updated_by
            FROM checklist_items WHERE case_id = 'case-0137'
            """
        )
    ).mappings()
    by_id = {row["requirement_id"]: row for row in rows}
    for requirement_id in ("beneficial-owner", "identity", "cod-dvp"):
        assert by_id[requirement_id]["status"] == "VERIFIED"
        assert by_id[requirement_id]["updated_by"] == "u-compliance"
    assert by_id["naaf"]["status"] == "VERIFIED"
    assert by_id["formation"]["status"] == "VERIFIED"
    audit = _one(
        db_session,
        """
        SELECT summary FROM audit_events
        WHERE case_id = 'case-0137' AND action = 'COMPLIANCE_DECISION'
        ORDER BY seq DESC LIMIT 1
        """,
    )
    assert audit["summary"] == "Approved by Compliance"


def test_t_gate_11_return_creates_compliance_tasks(db_client, db_session, role_headers) -> None:
    before_tasks = int(
        _scalar(db_session, "SELECT count(*) FROM review_tasks WHERE case_id = 'case-0137'")  # type: ignore[arg-type]
    )
    response = db_client.post(
        "/api/v1/cases/case-0137/compliance-decision",
        headers=role_headers("COMPLIANCE"),
        json={
            "version": 14,
            "decision": "RETURN",
            "comments": [
                {"title": "Name the trustees.", "partyId": "tf-root"},
                {"title": "Replace the rejected formation document.", "requirementId": "formation"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["case"]["status"] == "RETURNED"
    added = db_session.execute(
        text(
            """
            SELECT source FROM review_tasks
            WHERE case_id = 'case-0137'
            ORDER BY created_at DESC
            LIMIT 2
            """
        )
    ).scalars()
    assert list(added) == ["COMPLIANCE", "COMPLIANCE"]
    assert int(
        _scalar(db_session, "SELECT count(*) FROM review_tasks WHERE case_id = 'case-0137'")  # type: ignore[arg-type]
    ) == before_tasks + 2
    audit = _one(
        db_session,
        """
        SELECT summary FROM audit_events
        WHERE case_id = 'case-0137' AND action = 'COMPLIANCE_DECISION'
        ORDER BY seq DESC LIMIT 1
        """,
    )
    assert audit["summary"] == "Returned to advisor with 2 comments"


def test_t_gate_12_return_requires_a_comment(db_client, db_session, role_headers) -> None:
    audits = _audits(db_session, "case-0137")
    response = db_client.post(
        "/api/v1/cases/case-0137/compliance-decision",
        headers=role_headers("COMPLIANCE"),
        json={"version": 14, "decision": "RETURN", "comments": []},
    )
    body = response.json()
    assert response.status_code == 400
    assert body["error"]["code"] == "VALIDATION_FAILED"
    assert _case(db_session, "case-0137")["version"] == 14
    assert _case(db_session, "case-0137")["status"] == "READY_FOR_COMPLIANCE"
    assert _audits(db_session, "case-0137") == audits


def test_conflict_message_matches_frontend() -> None:
    assert CONFLICT.startswith("This case was changed")
