"""客户门户：非 DOCS_REQUESTED 拒绝、清单字段、VERIFIED 上传、缺表跳过。"""

from __future__ import annotations

import base64
import hashlib
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from fcc_api.api.deps import get_db
from fcc_api.db.session import get_session
from fcc_api.main import create_app

CASE_DOCS = "case-0139"
CASE_BUILDING = "case-0142"
EMAIL = "  Portal.Client@Example.com  "
NORMALIZED_EMAIL = "portal.client@example.com"
FORBIDDEN_TEXT = (
    "Alice Chen",
    "Raj Patel",
    "Mei Lin",
    "David Okoye",
    "Helen Chen",
    "Harbourview",
    "OBCA 2748113",
    "ownershipPercent",
    "registrationNumber",
    "legalName",
    "partyIds",
    "isPepHio",
    "trustedContactName",
    NORMALIZED_EMAIL,
    "Portal.Client@Example.com",
)


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application = create_app()
    from fcc_api.api.routers.portal import router

    mounted = any(getattr(route, "path", None) == "/api/v1/portal/session" for route in application.routes)
    if not mounted:
        application.include_router(router)

    def override_session() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_session] = override_session
    application.dependency_overrides[get_db] = override_session
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def portal_columns(db_session: Session) -> set[str]:
    exists = db_session.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'portal_invites'
            )
            """
        )
    ).scalar_one()
    if not exists:
        pytest.skip("portal_invites 表不存在，跳过依赖该表的门户测试。")
    rows = db_session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'portal_invites'
            """
        )
    ).scalars()
    return set(rows)


def _advisor() -> dict[str, str]:
    return {"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"}


def _version(db: Session, case_id: str) -> int:
    return int(db.execute(text("SELECT version FROM cases WHERE id = :id"), {"id": case_id}).scalar_one())


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ensure_session_columns(db: Session, columns: set[str]) -> None:
    """事务内补上文档要求的可空会话列。外层事务回滚后不会留下。"""

    if "session_token_hash" not in columns:
        db.execute(text("ALTER TABLE portal_invites ADD COLUMN session_token_hash text"))
    if "session_expires_at" not in columns:
        db.execute(text("ALTER TABLE portal_invites ADD COLUMN session_expires_at timestamptz"))
    db.flush()


def test_invite_rejected_unless_docs_requested(client: TestClient, db_session: Session, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    version = _version(db_session, CASE_BUILDING)
    response = client.post(
        f"/api/v1/cases/{CASE_BUILDING}/portal-invites",
        headers=_advisor(),
        json={"version": version, "email": EMAIL},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["details"]["reason"] == "CASE_STATUS"
    assert body["error"]["details"]["status"] == "BUILDING"
    assert NORMALIZED_EMAIL not in response.text
    assert "Portal.Client@Example.com" not in response.text
    assert NORMALIZED_EMAIL not in caplog.text
    assert "Portal.Client@Example.com" not in caplog.text
    assert _version(db_session, CASE_BUILDING) == version


def test_invite_version_conflict_is_409(client: TestClient, db_session: Session) -> None:
    version = _version(db_session, CASE_DOCS)
    response = client.post(
        f"/api/v1/cases/{CASE_DOCS}/portal-invites",
        headers=_advisor(),
        json={"version": version + 1, "email": EMAIL},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert NORMALIZED_EMAIL not in response.text
    assert _version(db_session, CASE_DOCS) == version


def test_portal_bearer_is_not_an_employee_header(client: TestClient) -> None:
    denied = client.get("/api/v1/cases", headers={"Authorization": "Bearer portal-session-token"})
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "UNAUTHENTICATED"

    listed = client.get(
        "/api/v1/cases",
        headers={**_advisor(), "Authorization": "Bearer portal-session-token"},
    )
    assert listed.status_code == 200


def test_session_missing_columns_is_503(
    client: TestClient,
    db_session: Session,
    portal_columns: set[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    if {"session_token_hash", "session_expires_at"} <= portal_columns:
        pytest.skip("会话列已存在，改为校验正常换发会话。")
    caplog.set_level(logging.DEBUG)
    version = _version(db_session, CASE_DOCS)
    invited = client.post(
        f"/api/v1/cases/{CASE_DOCS}/portal-invites",
        headers=_advisor(),
        json={"version": version, "email": EMAIL},
    )
    assert invited.status_code == 201, invited.text
    token = invited.json()["invitePath"].removeprefix("/portal/invite/")
    assert NORMALIZED_EMAIL not in invited.text
    assert NORMALIZED_EMAIL not in caplog.text
    assert token not in caplog.text
    opened = client.post("/api/v1/portal/session", json={"token": token})
    assert opened.status_code == 503
    assert opened.json()["error"]["code"] == "UNAVAILABLE"
    assert token not in opened.text
    assert NORMALIZED_EMAIL not in opened.text
    stored = " ".join(
        db_session.execute(text("SELECT t::text FROM portal_invites AS t")).scalars()
    )
    assert NORMALIZED_EMAIL not in stored
    assert token not in stored
    assert _sha256(NORMALIZED_EMAIL) in stored
    assert _sha256(token) in stored


def test_portal_checklist_returns_only_item_fields(
    client: TestClient,
    db_session: Session,
    portal_columns: set[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _ensure_session_columns(db_session, portal_columns)
    caplog.set_level(logging.DEBUG)
    version = _version(db_session, CASE_DOCS)
    invited = client.post(
        f"/api/v1/cases/{CASE_DOCS}/portal-invites",
        headers=_advisor(),
        json={"version": version, "email": EMAIL},
    )
    assert invited.status_code == 201, invited.text
    invite_path = invited.json()["invitePath"]
    assert invite_path.startswith("/portal/invite/")
    token = invite_path.removeprefix("/portal/invite/")
    assert "/" not in token
    padding = "=" * (-len(token) % 4)
    assert len(base64.urlsafe_b64decode(token + padding)) == 32
    assert NORMALIZED_EMAIL not in invited.text
    assert token not in caplog.text
    assert NORMALIZED_EMAIL not in caplog.text

    opened = client.post("/api/v1/portal/session", json={"token": token})
    assert opened.status_code == 200, opened.text
    session_token = opened.json()["token"]
    assert session_token != token
    assert session_token not in caplog.text

    checklist = client.get("/api/v1/portal/checklist", headers={"Authorization": f"Bearer {session_token}"})
    assert checklist.status_code == 200, checklist.text
    items = checklist.json()
    assert isinstance(items, list) and items
    for item in items:
        assert set(item) == {"id", "name", "status"}
    by_id = {item["id"]: item for item in items}
    assert by_id["naaf"]["status"] == "VERIFIED"
    assert by_id["identity"]["name"] == "Identity verification for signers and controllers"
    rendered = checklist.text
    for secret in FORBIDDEN_TEXT:
        assert secret not in rendered

    denied = client.get("/api/v1/cases", headers={"Authorization": f"Bearer {session_token}"})
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "UNAUTHENTICATED"

    stored = db_session.execute(
        text(
            """
            SELECT email_sha256, token_hash, session_token_hash
            FROM portal_invites
            WHERE case_id = :case_id
            """
        ),
        {"case_id": CASE_DOCS},
    ).mappings().all()
    assert stored
    blob = " ".join(str(value) for row in stored for value in row.values())
    assert NORMALIZED_EMAIL not in blob
    assert token not in blob
    assert session_token not in blob
    assert any(row["email_sha256"] == _sha256(NORMALIZED_EMAIL) for row in stored)
    assert any(row["token_hash"] == _sha256(token) for row in stored)
    assert any(row["session_token_hash"] == _sha256(session_token) for row in stored)

    audit_blob = db_session.execute(
        text(
            """
            SELECT coalesce(string_agg(
                summary || ' ' || coalesce(before_value::text, '') || ' ' || coalesce(after_value::text, ''),
                ' '
            ), '')
            FROM audit_events
            WHERE case_id = :case_id
            """
        ),
        {"case_id": CASE_DOCS},
    ).scalar_one()
    assert NORMALIZED_EMAIL not in str(audit_blob)
    assert token not in str(audit_blob)


def test_portal_upload_to_verified_item_is_403(
    client: TestClient,
    db_session: Session,
    portal_columns: set[str],
) -> None:
    _ensure_session_columns(db_session, portal_columns)
    version = _version(db_session, CASE_DOCS)
    invited = client.post(
        f"/api/v1/cases/{CASE_DOCS}/portal-invites",
        headers=_advisor(),
        json={"version": version, "email": EMAIL},
    )
    assert invited.status_code == 201, invited.text
    token = invited.json()["invitePath"].removeprefix("/portal/invite/")
    opened = client.post("/api/v1/portal/session", json={"token": token})
    assert opened.status_code == 200, opened.text
    session_token = opened.json()["token"]
    response = client.post(
        "/api/v1/portal/uploads",
        headers={"Authorization": f"Bearer {session_token}"},
        json={
            "requirementId": "naaf",
            "files": [{"fileName": "identity.pdf", "sizeBytes": 1200, "mimeType": "application/pdf"}],
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "ITEM_STATUS"
    assert NORMALIZED_EMAIL not in response.text
