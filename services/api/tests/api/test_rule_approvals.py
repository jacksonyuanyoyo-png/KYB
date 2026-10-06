"""Rule approval HTTP contract from docs/backend/12-production-launch.md section 4.2.

These tests skip when the rule_approvals table has not been migrated yet.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.auth.dependencies import get_db_session
from fcc_api.db.session import get_session
from fcc_api.main import create_app

ADMIN = {"X-User-Id": "u-admin", "X-User-Role": "ADMIN"}
COMPLIANCE = {"X-User-Id": "u-compliance", "X-User-Role": "COMPLIANCE"}
ADVISOR = {"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"}
OPERATIONS = {"X-User-Id": "u-ops", "X-User-Role": "OPERATIONS"}
HEADERS = {
    "ADMIN": ADMIN,
    "COMPLIANCE": COMPLIANCE,
    "ADVISOR": ADVISOR,
    "OPERATIONS": OPERATIONS,
}
NAAF_DISPLAY = {
    "conditional": False,
    "name": "New Account Application Form (NAAF)",
    "reason": "New Account Application Form (NAAF)",
    "section": "Entity Formation & Authorization",
    "source": "FCC guide §5.2",
    "trigger": "Always",
}


def _sha(display: dict[str, Any]) -> str:
    payload = json.dumps(display, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _approval(rule_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ruleId": rule_id,
        "builtinVersion": "demo-2026-10-04",
        "ownerName": "Fidelity Compliance",
        "sourceUrl": "FCC guide §5.2",
        "effectiveOn": "2026-10-06",
        "reviewDueOn": "2027-10-06",
        "testIds": ["T-LAUNCH-05"],
        "approvalTicket": "TICKET-1",
    }
    body.update(overrides)
    return body


def _statuses(db: Session) -> dict[str, str]:
    rows = db.execute(text("SELECT id, approval_status FROM rule_approvals")).all()
    return {row.id: row.approval_status for row in rows}


def _library_version(api: TestClient) -> int:
    current = api.get("/api/v1/rule-library", headers=ADMIN)
    assert current.status_code == 200, current.text
    body = current.json()
    if body.get("draft"):
        return int(body["version"])
    started = api.post("/api/v1/rule-library/draft", headers=ADMIN, json={"version": body["version"]})
    assert started.status_code in {200, 201}, started.text
    return int(started.json()["version"])


def _add_extra(api: TestClient, rule_id: str, *, name: str, reason: str, source: str) -> dict[str, Any]:
    version = _library_version(api)
    rule = {
        "id": rule_id,
        "name": name,
        "section": "Entity Formation & Authorization",
        "conditional": True,
        "source": source,
        "reason": reason,
        "enabled": True,
        "trigger": {"kind": "ALWAYS"},
    }
    saved = api.post(
        "/api/v1/rule-library/draft/extras",
        headers=ADMIN,
        json={"version": version, "rule": rule},
    )
    assert saved.status_code == 200, saved.text
    return {
        "conditional": True,
        "name": name,
        "reason": reason,
        "section": "Entity Formation & Authorization",
        "source": source,
        "trigger": {"kind": "ALWAYS"},
    }


@pytest.fixture
def api(db_session: Session) -> Iterator[TestClient]:
    present = db_session.execute(text("SELECT to_regclass('rule_approvals')")).scalar()
    if present is None:
        pytest.skip("rule_approvals 表还不存在（迁移 0003 未应用）。接口测试跳过，不创建迁移。")
    application = create_app()

    def override_session() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_session] = override_session
    application.dependency_overrides[get_db] = override_session
    application.dependency_overrides[get_db_session] = override_session
    with TestClient(application) as client:
        yield client


def test_content_hash_is_canonical_display_json() -> None:
    from fcc_api.services.approvals import catalog_display, content_sha256

    assert catalog_display("naaf") == NAAF_DISPLAY
    assert content_sha256(NAAF_DISPLAY) == _sha(NAAF_DISPLAY)
    snake = {
        "name": "Extra",
        "section": "Entity Formation & Authorization",
        "conditional": True,
        "source": "Internal",
        "reason": "Because",
        "trigger": {"kind": "ENTITY", "entity_types": ["trust"]},
    }
    assert content_sha256(snake) == _sha(
        {
            "conditional": True,
            "name": "Extra",
            "reason": "Because",
            "section": "Entity Formation & Authorization",
            "source": "Internal",
            "trigger": {"entityTypes": ["trust"], "kind": "ENTITY"},
        }
    )


def test_submit_requires_admin_and_known_identity(api: TestClient, db_session: Session) -> None:
    before = _statuses(db_session)
    anonymous = api.post("/api/v1/rule-approvals", json=_approval("naaf"))
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "UNAUTHENTICATED"
    assert anonymous.json()["requestId"] == anonymous.headers["x-request-id"]

    for role in ("ADVISOR", "OPERATIONS", "COMPLIANCE"):
        denied = api.post("/api/v1/rule-approvals", headers=HEADERS[role], json=_approval("naaf"))
        assert denied.status_code == 403, denied.text
        assert denied.json()["error"] == {
            "code": "FORBIDDEN",
            "message": "Only an admin can submit a rule approval.",
            "details": {"reason": "ROLE", "role": role, "operation": "submitRuleApproval"},
        }
    assert _statuses(db_session) == before


def test_builtin_submit_returns_the_seeded_pending_row(api: TestClient, db_session: Session) -> None:
    before = _statuses(db_session)
    seeded = db_session.execute(
        text(
            """
            SELECT owner_name, source_url, effective_on, review_due_on, test_ids,
                   approval_ticket, submitted_by, approved_by, approval_status
            FROM rule_approvals
            WHERE id = 'apr_naaf'
            """
        )
    ).one()
    created = api.post("/api/v1/rule-approvals", headers=ADMIN, json=_approval("naaf"))
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["id"] == "apr_naaf"
    assert body["contentSha256"] == _sha(NAAF_DISPLAY)
    assert body["approvalStatus"] == "PENDING"
    assert body["submittedBy"] is None
    assert body["effectiveOn"] is None
    assert body["reviewDueOn"] is None
    assert body["testIds"] == []
    assert body["approvalTicket"] is None
    assert body["ruleId"] == "naaf"
    assert "rule_id" not in body
    replay = api.post(
        "/api/v1/rule-approvals",
        headers=ADMIN,
        json=_approval("naaf", ownerName="Someone Else", approvalTicket="TICKET-2"),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == "apr_naaf"
    assert replay.json()["ownerName"] == seeded.owner_name
    assert replay.json()["approvalTicket"] is None
    blocked = api.post(
        "/api/v1/rule-approvals/apr_naaf/approve",
        headers=COMPLIANCE,
        json={"approvalTicket": "SIGN-SEED"},
    )
    assert blocked.status_code == 400, blocked.text
    assert _statuses(db_session) == before
    again = db_session.execute(
        text(
            """
            SELECT owner_name, source_url, effective_on, review_due_on, test_ids,
                   approval_ticket, submitted_by, approved_by, approval_status
            FROM rule_approvals
            WHERE id = 'apr_naaf'
            """
        )
    ).one()
    assert again == seeded


def test_draft_extra_is_submitted_then_approved_by_someone_else(api: TestClient, db_session: Session) -> None:
    before = _statuses(db_session)
    display = _add_extra(
        api,
        "rule_apr_one",
        name="Approval evidence",
        reason="Needed before production.",
        source="Internal policy",
    )
    created = api.post("/api/v1/rule-approvals", headers=ADMIN, json=_approval("rule_apr_one", sourceUrl="https://fcc.example/rules"))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["contentSha256"] == _sha(display)
    assert body["submittedBy"] == "u-admin"
    assert body["approvedBy"] is None
    assert body["approvedAt"] is None
    assert body["submittedAt"].endswith("Z")
    assert body["effectiveOn"] == "2026-10-06"
    assert body["reviewDueOn"] == "2027-10-06"
    assert body["testIds"] == ["T-LAUNCH-05"]
    assert body["sourceUrl"] == "https://fcc.example/rules"

    same_person = api.post(
        f"/api/v1/rule-approvals/{body['id']}/approve",
        headers=ADMIN,
        json={"approvalTicket": "SIGN-9"},
    )
    assert same_person.status_code == 403, same_person.text
    assert same_person.json()["error"]["details"]["reason"] == "ROLE"
    assert same_person.json()["error"]["message"] == "Only Compliance can approve a rule."
    assert _statuses(db_session)[body["id"]] == "PENDING"

    approved = api.post(
        f"/api/v1/rule-approvals/{body['id']}/approve",
        headers=COMPLIANCE,
        json={"approvalTicket": "SIGN-9"},
    )
    assert approved.status_code == 200, approved.text
    decision = approved.json()
    assert decision["approvalStatus"] == "APPROVED"
    assert decision["approvedBy"] == "u-compliance"
    assert decision["approvalTicket"] == "SIGN-9"
    assert decision["approvedAt"].endswith("Z")
    assert decision["submittedBy"] == "u-admin"

    again = api.post("/api/v1/rule-approvals", headers=ADMIN, json=_approval("rule_apr_one", sourceUrl="https://fcc.example/rules"))
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "VERSION_CONFLICT"
    assert _statuses(db_session)[body["id"]] == "APPROVED"
    after = _statuses(db_session)
    for approval_id, status in before.items():
        assert after[approval_id] == status

    for headers in (ADMIN, COMPLIANCE, ADVISOR, OPERATIONS):
        listed = api.get("/api/v1/rule-approvals", headers=headers)
        assert listed.status_code == 200, listed.text
        assert any(item["id"] == body["id"] and item["approvalStatus"] == "APPROVED" for item in listed.json()["items"])


def test_approver_cannot_be_the_submitter(api: TestClient, db_session: Session) -> None:
    _add_extra(api, "rule_apr_eyes", name="Four eyes evidence", reason="Separate person.", source="Internal policy")
    created = api.post("/api/v1/rule-approvals", headers=ADMIN, json=_approval("rule_apr_eyes"))
    assert created.status_code == 201, created.text
    approval_id = created.json()["id"]
    db_session.execute(
        text("UPDATE rule_approvals SET submitted_by = :user_id WHERE id = :id"),
        {"user_id": "u-compliance", "id": approval_id},
    )
    db_session.commit()
    denied = api.post(
        f"/api/v1/rule-approvals/{approval_id}/approve",
        headers=COMPLIANCE,
        json={"approvalTicket": "SIGN-SAME"},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"] == {
        "code": "FORBIDDEN",
        "message": "The approver must be a different person from the submitter.",
        "details": {"reason": "FOUR_EYES"},
    }
    row = db_session.execute(
        text("SELECT approval_status, approved_by FROM rule_approvals WHERE id = :id"),
        {"id": approval_id},
    ).one()
    assert row.approval_status == "PENDING"
    assert row.approved_by is None


def test_draft_override_changes_the_builtin_hash(api: TestClient, db_session: Session) -> None:
    before = _statuses(db_session)
    version = _library_version(api)
    renamed = "NAAF for approval"
    overridden = api.put(
        "/api/v1/rule-library/draft/builtins/naaf/override",
        headers=ADMIN,
        json={
            "version": version,
            "override": {
                "name": renamed,
                "section": "Entity Formation & Authorization",
                "conditional": False,
                "source": "FCC guide §5.2",
                "reason": "Required for every new account.",
            },
        },
    )
    assert overridden.status_code == 200, overridden.text
    created = api.post("/api/v1/rule-approvals", headers=ADMIN, json=_approval("naaf", approvalTicket="TICKET-OVERRIDE"))
    assert created.status_code == 201, created.text
    assert created.json()["approvalStatus"] == "PENDING"
    assert created.json()["contentSha256"] == _sha(
        {
            "conditional": False,
            "name": renamed,
            "reason": "Required for every new account.",
            "section": "Entity Formation & Authorization",
            "source": "FCC guide §5.2",
            "trigger": "Always",
        }
    )
    after = _statuses(db_session)
    for approval_id, status in before.items():
        assert after[approval_id] == status
    assert after[created.json()["id"]] == "PENDING"


def test_invalid_body_and_unknown_rule(api: TestClient, db_session: Session) -> None:
    before = _statuses(db_session)
    dates = api.post(
        "/api/v1/rule-approvals",
        headers=ADMIN,
        json=_approval("naaf", effectiveOn="2026-10-06", reviewDueOn="2026-10-06"),
    )
    assert dates.status_code == 400, dates.text
    assert dates.json()["error"]["code"] == "VALIDATION_FAILED"
    assert any(field["path"] == "reviewDueOn" for field in dates.json()["error"]["details"]["fields"])

    missing = api.post("/api/v1/rule-approvals", headers=ADMIN, json=_approval("rule_missing_zz"))
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "NOT_FOUND"
    assert missing.json()["error"]["details"] == {"resource": "rule", "id": "rule_missing_zz"}

    absent = api.post(
        "/api/v1/rule-approvals/apr_missing/approve",
        headers=COMPLIANCE,
        json={"approvalTicket": "SIGN-404"},
    )
    assert absent.status_code == 404, absent.text
    assert absent.json()["error"]["details"]["resource"] == "ruleApproval"
    assert _statuses(db_session) == before
