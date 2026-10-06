"""PDF fill and uDirect/uniFide submission. Submission-table tests skip when the table is absent."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from fcc_api.adapters.forms import get_form_filler
from fcc_api.adapters.submission import set_submitter
from fcc_api.api.deps import get_db
from fcc_api.api.routers.forms import router as forms_router
from fcc_api.api.routers.submissions import router as submissions_router
from fcc_api.db.session import get_session
from fcc_api.errors import ApiError
from fcc_api.main import create_app

CASE_ID = "case-0139"
APPROVED_ID = "case-0119"
MARKER = b"FCC-FORM-MARKER-91ab"
MARKER_TEXT = "FCC-FORM-MARKER-91ab"
REGISTRATION_NUMBER = "Band No. 612"


class _FakeSubmitter:
    name = "http"

    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def submit(self, *, case_id: str, target: str, payload: dict[str, object]) -> str | None:
        del case_id, target
        self.payloads.append(payload)
        return None


@pytest.fixture
def db(db_session: Session) -> Session:
    return db_session


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application = create_app()
    application.include_router(forms_router)
    application.include_router(submissions_router)

    def override_session() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_session] = override_session
    application.dependency_overrides[get_db] = override_session
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clear_submitter() -> Iterator[None]:
    set_submitter(None)
    yield
    set_submitter(None)


def _advisor() -> dict[str, str]:
    return {"X-User-Id": "u-advisor", "X-User-Role": "ADVISOR"}


def _operations() -> dict[str, str]:
    return {"X-User-Id": "u-ops", "X-User-Role": "OPERATIONS"}


def _compliance() -> dict[str, str]:
    return {"X-User-Id": "u-compliance", "X-User-Role": "COMPLIANCE"}


def _version(db: Session, case_id: str) -> int:
    return int(db.execute(text("SELECT version FROM cases WHERE id = :id"), {"id": case_id}).scalar_one())


def _parties(db: Session, case_id: str) -> list[tuple[object, ...]]:
    rows = db.execute(
        text(
            """
            SELECT id, legal_name, ownership_percent::text, parent_id
            FROM parties
            WHERE case_id = :id
            ORDER BY position, id
            """
        ),
        {"id": case_id},
    ).all()
    return [tuple(row) for row in rows]


def _has_table(db: Session, name: str) -> bool:
    found = db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = :name
            """
        ),
        {"name": name},
    ).first()
    return found is not None


def _require_submissions(db: Session) -> None:
    if not _has_table(db, "submissions"):
        pytest.skip("submissions 表不存在，相关测试已跳过。")


def _submission_count(db: Session) -> int:
    return int(db.execute(text("SELECT count(*) FROM submissions")).scalar_one())


def _document_count(db: Session, case_id: str) -> int:
    return int(
        db.execute(
            text("SELECT count(*) FROM case_documents WHERE case_id = :id"),
            {"id": case_id},
        ).scalar_one()
    )


def _write_template(root: Path, *, source: str = "case.legalName", pdf_name: str = "minimal.pdf") -> None:
    folder = root / "naaf"
    folder.mkdir(parents=True)
    (folder / pdf_name).write_bytes(b"%PDF-1.4\n%" + MARKER + b"\n%%EOF\n")
    payload = {
        "templateId": "naaf",
        "templateVersion": "2026-01",
        "requirementId": "naaf",
        "pdf": pdf_name,
        "fields": [
            {"pdfField": "LegalName", "source": source},
            {"pdfField": "Province", "source": "case.province"},
        ],
    }
    (folder / "2026-01.map.json").write_text(json.dumps(payload), encoding="utf-8")


def _rows_containing(db: Session, token: str) -> int:
    tables = (
        "cases",
        "parties",
        "case_documents",
        "upload_slots",
        "checklist_items",
        "audit_events",
        "document_extractions",
        "extraction_pages",
        "extraction_fields",
    )
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    total = 0
    for table in tables:
        if not _has_table(db, table):
            continue
        total += int(
            db.execute(
                text(
                    f'SELECT count(*) FROM public."{table}" AS t '
                    "WHERE t::text LIKE :pattern ESCAPE :escape"
                ),
                {"pattern": pattern, "escape": "\\"},
            ).scalar_one()
        )
    return total


def _assert_payload_safe(payload: object) -> None:
    encoded = json.dumps(payload)
    assert REGISTRATION_NUMBER not in encoded
    assert "registrationNumber" not in encoded
    assert "registration_number" not in encoded
    assert MARKER_TEXT not in encoded
    assert "%PDF" not in encoded

    def walk(value: object, key: str | None = None) -> None:
        assert not isinstance(value, (bytes, bytearray))
        if key is not None:
            assert "registration" not in key.lower()
        if isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                walk(child, key)

    walk(payload)


def test_blank_fields_follow_the_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_template(tmp_path)
    monkeypatch.setenv("FORM_TEMPLATE_ROOT", str(tmp_path))
    filled = get_form_filler().fill(
        case_id="case-0139",
        template_id="naaf",
        values={"case.legalName": "Acme", "case.province": "  "},
    )
    assert filled.blanks == ["Province"]
    assert filled.requirement_id == "naaf"
    assert filled.pdf_bytes.startswith(b"%PDF-")
    assert b"LegalName=Acme" in filled.pdf_bytes


def test_unknown_source_is_template_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_template(tmp_path, source="case.registrationNumber")
    monkeypatch.setenv("FORM_TEMPLATE_ROOT", str(tmp_path))
    with pytest.raises(ApiError) as caught:
        get_form_filler().fill(case_id="case-0139", template_id="naaf", values={})
    assert caught.value.status_code == 422
    assert caught.value.details["gate"] == "TEMPLATE_MISSING"
    assert caught.value.message == "The form template is not available."


def test_missing_template_returns_422(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORM_TEMPLATE_ROOT", raising=False)
    version = _version(db, CASE_ID)
    parties = _parties(db, CASE_ID)
    response = client.post(
        f"/api/v1/cases/{CASE_ID}/forms/naaf",
        headers=_advisor(),
        json={"version": version},
    )
    assert response.status_code == 422, response.text
    body = response.json()["error"]
    assert body["code"] == "GATE_FAILED"
    assert body["message"] == "The form template is not available."
    assert body["details"]["gate"] == "TEMPLATE_MISSING"
    assert _version(db, CASE_ID) == version
    assert _parties(db, CASE_ID) == parties


def test_compliance_cannot_fill_a_form(client: TestClient, db: Session) -> None:
    response = client.post(
        f"/api/v1/cases/{CASE_ID}/forms/naaf",
        headers=_compliance(),
        json={"version": _version(db, CASE_ID)},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["reason"] == "ROLE"


def test_approved_case_cannot_be_filled(client: TestClient, db: Session) -> None:
    response = client.post(
        f"/api/v1/cases/{APPROVED_ID}/forms/naaf",
        headers=_operations(),
        json={"version": _version(db, APPROVED_ID)},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["reason"] == "CASE_STATUS"


def test_template_registers_through_documents(
    client: TestClient,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_template(tmp_path)
    monkeypatch.setenv("FORM_TEMPLATE_ROOT", str(tmp_path))
    version = _version(db, CASE_ID)
    parties = _parties(db, CASE_ID)
    documents = _document_count(db, CASE_ID)
    response = client.post(
        f"/api/v1/cases/{CASE_ID}/forms/naaf",
        headers=_advisor(),
        json={"version": version},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["documentId"].startswith("doc_")
    assert "LegalName" not in body["blanks"]
    assert _parties(db, CASE_ID) == parties
    assert _version(db, CASE_ID) == version + 1
    assert _document_count(db, CASE_ID) == documents + 1
    row = db.execute(
        text(
            """
            SELECT requirement_id, sha256, object_key, mime_type
            FROM case_documents
            WHERE id = :id
            """
        ),
        {"id": body["documentId"]},
    ).one()
    assert row.requirement_id == "naaf"
    assert row.mime_type == "application/pdf"
    assert row.sha256
    from fcc_api.config import get_settings
    from fcc_api.storage.local import resolve_storage_root

    stored = resolve_storage_root(get_settings()) / "accepted" / row.object_key
    assert stored.is_file()
    file_bytes = stored.read_bytes()
    assert file_bytes.startswith(b"%PDF-")
    assert MARKER in file_bytes
    assert hashlib.sha256(file_bytes).hexdigest() == row.sha256
    assert _rows_containing(db, MARKER_TEXT) == 0


def test_missing_registrar_returns_422(
    client: TestClient,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_template(tmp_path)
    monkeypatch.setenv("FORM_TEMPLATE_ROOT", str(tmp_path))
    monkeypatch.setattr("fcc_api.services.forms.registration_functions", lambda: None)
    version = _version(db, CASE_ID)
    parties = _parties(db, CASE_ID)
    documents = _document_count(db, CASE_ID)
    response = client.post(
        f"/api/v1/cases/{CASE_ID}/forms/naaf",
        headers=_advisor(),
        json={"version": version},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["gate"] == "REGISTRATION_UNAVAILABLE"
    assert _version(db, CASE_ID) == version
    assert _parties(db, CASE_ID) == parties
    assert _document_count(db, CASE_ID) == documents


def test_advisor_cannot_submit(client: TestClient, db: Session) -> None:
    response = client.post(
        f"/api/v1/cases/{APPROVED_ID}/submissions",
        headers=_advisor(),
        json={"version": _version(db, APPROVED_ID), "target": "UDIRECT"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["reason"] == "CASE_STATUS"


def test_submit_requires_approved_status(client: TestClient, db: Session) -> None:
    version = _version(db, CASE_ID)
    response = client.post(
        f"/api/v1/cases/{CASE_ID}/submissions",
        headers=_operations(),
        json={"version": version, "target": "UNIFIDE"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["reason"] == "CASE_STATUS"
    assert _version(db, CASE_ID) == version


def test_unconfigured_submission_writes_nothing(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_submissions(db)
    monkeypatch.setenv("SUBMISSION_ADAPTER", "unconfigured")
    version = _version(db, APPROVED_ID)
    count = _submission_count(db)
    response = client.post(
        f"/api/v1/cases/{APPROVED_ID}/submissions",
        headers=_operations(),
        json={"version": version, "target": "UDIRECT"},
    )
    assert response.status_code == 503, response.text
    body = response.json()["error"]
    assert body["code"] == "UNAVAILABLE"
    assert body["message"] == "Submission is not configured."
    assert _submission_count(db) == count
    assert _version(db, APPROVED_ID) == version


def test_http_submit_is_idempotent_and_omits_file_bytes(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_submissions(db)
    monkeypatch.setenv("SUBMISSION_ADAPTER", "http")
    fake = _FakeSubmitter()
    set_submitter(fake)
    version = _version(db, APPROVED_ID)
    count = _submission_count(db)
    first = client.post(
        f"/api/v1/cases/{APPROVED_ID}/submissions",
        headers=_operations(),
        json={"version": version, "target": "UDIRECT"},
    )
    assert first.status_code == 201, first.text
    created = first.json()
    assert created["id"].startswith("sub_")
    assert created["status"] == "SENT"
    assert created["target"] == "UDIRECT"
    assert created["version"] == version
    assert _version(db, APPROVED_ID) == version + 1
    assert _submission_count(db) == count + 1
    assert len(fake.payloads) == 1
    _assert_payload_safe(fake.payloads[0])
    assert "documents" in fake.payloads[0]
    for document in fake.payloads[0]["documents"]:  # type: ignore[index]
        assert set(document) == {"documentId", "sha256", "requirementId"}

    second = client.post(
        f"/api/v1/cases/{APPROVED_ID}/submissions",
        headers=_admin(),
        json={"version": version, "target": "UDIRECT"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] == created["id"]
    assert _version(db, APPROVED_ID) == version + 1
    assert _submission_count(db) == count + 1
    assert len(fake.payloads) == 1


def _admin() -> dict[str, str]:
    return {"X-User-Id": "u-admin", "X-User-Role": "ADMIN"}


def test_callback_secret_missing_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUBMISSION_CALLBACK_SECRET", raising=False)
    response = client.post(
        "/api/v1/submissions/callback",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "UNAVAILABLE"


def test_callback_updates_submission_without_case_version(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_submissions(db)
    monkeypatch.setenv("SUBMISSION_ADAPTER", "http")
    set_submitter(_FakeSubmitter())
    version = _version(db, APPROVED_ID)
    created = client.post(
        f"/api/v1/cases/{APPROVED_ID}/submissions",
        headers=_operations(),
        json={"version": version, "target": "UNIFIDE"},
    )
    assert created.status_code == 201, created.text
    submission_id = created.json()["id"]
    before = dict(
        db.execute(text("SELECT * FROM submissions WHERE id = :id"), {"id": submission_id}).mappings().one()
    )
    case_version = _version(db, APPROVED_ID)
    secret = "callback-secret"
    monkeypatch.setenv("SUBMISSION_CALLBACK_SECRET", secret)
    raw = json.dumps(
        {"submissionId": submission_id, "status": "ACCEPTED", "vendorReference": "V-19"}
    ).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    response = client.post(
        "/api/v1/submissions/callback",
        content=raw,
        headers={"Content-Type": "application/json", "X-Submission-Signature": signature},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ACCEPTED"
    assert body["vendorReference"] == "V-19"
    assert _version(db, APPROVED_ID) == case_version
    after = dict(
        db.execute(text("SELECT * FROM submissions WHERE id = :id"), {"id": submission_id}).mappings().one()
    )
    changed = {key for key in before if before[key] != after.get(key)}
    assert changed <= {"status", "vendor_reference"}
    assert after["status"] == "ACCEPTED"
    assert after["vendor_reference"] == "V-19"

    rejected = client.post(
        "/api/v1/submissions/callback",
        content=raw,
        headers={**_operations(), "X-Submission-Signature": "0" * 64},
    )
    assert rejected.status_code == 401, rejected.text
