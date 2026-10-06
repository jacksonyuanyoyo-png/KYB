import re

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from fcc_api.config import Settings
from fcc_api.ids import new_id
from fcc_api.labels import DOC_STATUS_LABELS, ROLE_LABELS, STATUS_LABELS
from fcc_api.logging import REDACTED, redact
from fcc_api.main import create_app


def test_liveness_does_not_require_identity_or_database(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "fcc-kyb-api"}
    assert re.fullmatch(r"req_[a-z0-9]{8}", response.headers["x-request-id"])


def test_ready_succeeds_when_database_is_up(
    client: TestClient,
    app_database_probe: tuple[bool, str],
) -> None:
    reachable, reason = app_database_probe
    if not reachable:
        pytest.skip(
            "数据库未就绪，跳过 GET /api/health/ready 的成功断言。"
            f"原因：{reason}。"
            "请启动仓库根目录已有的 PostgreSQL（docker compose up -d postgres）。"
            "不要修改 docker-compose。"
        )
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "fcc-kyb-api",
        "database": "ok",
    }


def test_ready_returns_503_when_database_is_down(
    client: TestClient,
    app_database_probe: tuple[bool, str],
) -> None:
    reachable, _reason = app_database_probe
    if reachable:
        pytest.skip("数据库已连通，跳过就绪检查失败路径。")
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "service": "fcc-kyb-api",
        "database": "error",
    }


def test_request_validation_is_400_not_422(client: TestClient) -> None:
    class Party(BaseModel):
        model_config = ConfigDict(populate_by_name=True)
        ownership_percent: int = Field(alias="ownershipPercent", le=100)

    class Payload(BaseModel):
        parties: list[Party]

    @client.app.post("/api/_validation_probe")
    def _probe(body: Payload) -> dict[str, bool]:
        return {"ok": True}

    @client.app.get("/api/_query_probe")
    def _query(limit: int) -> dict[str, int]:
        return {"limit": limit}

    response = client.post(
        "/api/_validation_probe",
        json={
            "parties": [
                {"ownershipPercent": 10},
                {"ownershipPercent": 20},
                {"ownershipPercent": 150},
            ]
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"
    assert body["error"]["message"] == "Request body is invalid."
    matched = [
        item for item in body["error"]["details"]["fields"] if item["path"] == "parties[2].ownershipPercent"
    ]
    assert matched
    assert "less than or equal to 100" in matched[0]["message"]
    assert set(matched[0]) == {"path", "message"}
    assert response.headers["x-request-id"] == body["requestId"]
    assert re.fullmatch(r"req_[a-z0-9]{8}", body["requestId"])

    query_response = client.get("/api/_query_probe", params={"limit": "nope"})
    assert query_response.status_code == 400
    query_body = query_response.json()
    assert query_body["error"]["code"] == "VALIDATION_FAILED"
    assert query_body["error"]["message"] == "Request is invalid."


def test_unknown_path_uses_error_shape(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["details"] == {}
    assert response.headers["x-request-id"] == body["requestId"]


def test_openapi_docs_are_available_locally(client: TestClient) -> None:
    docs = client.get("/api/docs")
    spec = client.get("/api/openapi.json")
    assert docs.status_code == 200
    assert spec.status_code == 200
    assert "/api/health" in spec.text


def test_production_header_auth_refuses_startup() -> None:
    settings = Settings(app_env="production", auth_mode="header")
    with pytest.raises(SystemExit, match="AUTH_MODE=header"):
        create_app(settings)


def test_new_id_matches_frontend_uid_width() -> None:
    values = {new_id("case") for _ in range(20)}
    assert len(values) == 20
    for value in values:
        assert re.fullmatch(r"case_[a-z0-9]{8}", value)


def test_status_labels_match_frontend_copy() -> None:
    assert STATUS_LABELS["BUILDING"] == "Building"
    assert STATUS_LABELS["DOCS_REQUESTED"] == "Docs requested"
    assert STATUS_LABELS["READY_FOR_COMPLIANCE"] == "Ready for compliance"
    assert STATUS_LABELS["RETURNED"] == "Returned"
    assert STATUS_LABELS["APPROVED"] == "Approved"
    assert DOC_STATUS_LABELS["VERIFIED"] == "Verified"
    assert ROLE_LABELS["ADVISOR"] == "WI/PI Advisor"


def test_log_redaction_replaces_sensitive_keys() -> None:
    cleaned = redact(
        {
            "fileName": "alice.pdf",
            "legal_name": "Acme",
            "registrationNumber": "123456789",
            "case_id": "case-1",
            "s3_kms_key_id": "key-1",
            "nested": {"quote": "page text", "sha256": "abc"},
            "changes": [{"field": "Status", "from": "Building", "to": "Approved"}],
            "url": "https://files.example/upload?sig=abc&expires=9",
        }
    )
    assert cleaned["fileName"] == REDACTED
    assert cleaned["legal_name"] == REDACTED
    assert cleaned["registrationNumber"] == REDACTED
    assert cleaned["case_id"] == "case-1"
    assert cleaned["s3_kms_key_id"] == "key-1"
    assert cleaned["nested"]["quote"] == REDACTED
    assert cleaned["nested"]["sha256"] == "abc"
    assert cleaned["changes"][0]["from"] == REDACTED
    assert cleaned["changes"][0]["to"] == REDACTED
    assert cleaned["changes"][0]["field"] == "Status"
    assert cleaned["url"] == REDACTED
    assert redact("GET /upload?sig=abc&expires=9") == "GET /upload?sig=[REDACTED]&expires=[REDACTED]"


def test_role_headers_use_seed_users(role_headers) -> None:
    assert role_headers("advisor") == {"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"}
    assert role_headers("OPERATIONS")["X-User-Id"] == "u-ops"
    assert role_headers("COMPLIANCE")["X-User-Role"] == "COMPLIANCE"
    assert role_headers("ADMIN", user_id="u-admin")["X-User-Id"] == "u-admin"
    with pytest.raises(ValueError):
        role_headers("GUEST")


def test_db_session_can_query_when_test_database_is_up(db_session: Session) -> None:
    assert db_session.execute(text("SELECT 1")).scalar() == 1
