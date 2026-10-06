"""T-FILE-01 through T-FILE-13. Each test reads the live case version and does not depend on order."""

from __future__ import annotations

import base64
import hashlib
import logging
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

MARKER = b"FCC-BYTES-MARKER-7f3c"
MARKER_TEXT = "FCC-BYTES-MARKER-7f3c"
FILE_NAME = "Board_Pack_Q3.pdf"
CASE_ID = "case-0139"
REGISTRATION_NUMBER = "OBCA 2748113"


def _pdf(marker: bytes = b"") -> bytes:
    return b"%PDF-1.4\n" + marker + b"\n" + (b"0" * 80) + b"\n%%EOF\n"


def _auth(user_id: str, role: str) -> dict[str, str]:
    return {"X-User-Id": user_id, "X-User-Role": role}


def _advisor() -> dict[str, str]:
    return _auth("u-advisor", "ADVISOR")


def _settings():
    from fcc_api.config import get_settings

    return get_settings()


def _storage_root() -> Path:
    from fcc_api.storage.local import resolve_storage_root

    return resolve_storage_root(_settings())


@pytest.fixture
def client(db_client):
    return db_client


@pytest.fixture
def db(db_session: Session):
    return db_session


def _case_row(db: Session, case_id: str = CASE_ID):
    return db.execute(
        text("SELECT version, updated_at, registration_number FROM cases WHERE id = :id"),
        {"id": case_id},
    ).one()


def _count(db: Session, sql: str, params: dict | None = None) -> int:
    return int(db.execute(text(sql), params or {}).scalar_one())


def _audit_count(db: Session, case_id: str = CASE_ID) -> int:
    return _count(db, "SELECT count(*) FROM audit_events WHERE case_id = :id", {"id": case_id})


def _slot_count(db: Session) -> int:
    return _count(db, "SELECT count(*) FROM upload_slots")


def _document_count(db: Session, case_id: str = CASE_ID) -> int:
    return _count(db, "SELECT count(*) FROM case_documents WHERE case_id = :id", {"id": case_id})


def _request_slot(client, content: bytes, *, file_name: str = FILE_NAME, size: int | None = None, mime: str = "application/pdf"):
    response = client.post(
        f"/api/v1/cases/{CASE_ID}/uploads",
        headers=_advisor(),
        json={
            "requirementId": None,
            "files": [{"fileName": file_name, "sizeBytes": len(content) if size is None else size, "mimeType": mime}],
        },
    )
    return response


def _put_bytes(client, slot: dict, content: bytes):
    parts = urlsplit(slot["url"])
    target = parts.path + (("?" + parts.query) if parts.query else "")
    return client.put(target, content=content, headers=slot["headers"])


def _complete(client, db: Session, batch_id: str, upload_ids: list[str]):
    version = _case_row(db).version
    return client.post(
        f"/api/v1/cases/{CASE_ID}/documents",
        headers=_advisor(),
        json={"version": version, "batchId": batch_id, "uploadIds": upload_ids},
    )


def _register(client, db: Session, content: bytes, *, file_name: str = FILE_NAME):
    created = _request_slot(client, content, file_name=file_name)
    assert created.status_code == 201, created.text
    body = created.json()
    stored = _put_bytes(client, body["slots"][0], content)
    assert stored.status_code == 204, stored.text
    finished = _complete(client, db, body["batchId"], [body["slots"][0]["uploadId"]])
    return finished, body


def _public_tables(db: Session) -> list[str]:
    rows = db.execute(
        text(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY c.relname
            """
        )
    ).scalars()
    return [name for name in rows if name.replace("_", "").isalnum()]


def _rows_containing(db: Session, token: str) -> int:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    total = 0
    for table in _public_tables(db):
        total += _count(
            db,
            f'SELECT count(*) FROM public."{table}" AS t WHERE t::text LIKE :pattern ESCAPE :escape',
            {"pattern": pattern, "escape": "\\"},
        )
    return total


def test_t_file_01_no_bytea_columns(db: Session) -> None:
    rows = db.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND data_type = 'bytea'
            """
        )
    ).all()
    assert rows == []


def test_t_file_02_bytes_stay_on_disk(client, db: Session) -> None:
    content = _pdf(MARKER)
    assert len(content) > 64
    finished, body = _register(client, db, content)
    assert finished.status_code == 201, finished.text
    digest = hashlib.sha256(content).hexdigest()
    upload_id = body["slots"][0]["uploadId"]
    object_key = f"cases/{CASE_ID}/documents/{upload_id}/original"
    row = db.execute(
        text(
            """
            SELECT sha256, object_key
            FROM case_documents
            WHERE case_id = :case_id AND object_key = :object_key
            """
        ),
        {"case_id": CASE_ID, "object_key": object_key},
    ).one()
    assert row.sha256 == digest
    assert row.object_key == object_key
    root = _storage_root()
    accepted = root / "accepted" / object_key
    quarantine = root / "quarantine" / object_key
    assert accepted.is_file()
    assert hashlib.sha256(accepted.read_bytes()).hexdigest() == digest
    assert not quarantine.exists()
    assert _rows_containing(db, MARKER_TEXT) == 0
    encoded = base64.b64encode(content[:64]).decode("ascii")
    assert _rows_containing(db, encoded) == 0


def test_t_file_03_object_key_has_no_filename(client, db: Session) -> None:
    content = _pdf(b"plain-key-check")
    finished, body = _register(client, db, content)
    assert finished.status_code == 201, finished.text
    upload_id = body["slots"][0]["uploadId"]
    object_key = db.execute(
        text("SELECT object_key FROM upload_slots WHERE id = :id"),
        {"id": upload_id},
    ).scalar_one()
    assert object_key == f"cases/{CASE_ID}/documents/{upload_id}/original"
    assert FILE_NAME not in object_key
    assert "Board_Pack" not in object_key
    assert "Q3" not in object_key


def test_t_file_04_rejects_oversize_declaration(client, db: Session) -> None:
    before_slots = _slot_count(db)
    before = _case_row(db)
    response = client.post(
        f"/api/v1/cases/{CASE_ID}/uploads",
        headers=_advisor(),
        json={"requirementId": None, "files": [{"fileName": FILE_NAME, "sizeBytes": 25000001, "mimeType": "application/pdf"}]},
    )
    assert response.status_code == 400, response.text
    db.expire_all()
    assert _slot_count(db) == before_slots
    assert _case_row(db).version == before.version


def test_t_file_05_rejects_disallowed_mime(client, db: Session) -> None:
    before_slots = _slot_count(db)
    response = client.post(
        f"/api/v1/cases/{CASE_ID}/uploads",
        headers=_advisor(),
        json={"requirementId": None, "files": [{"fileName": FILE_NAME, "sizeBytes": 120, "mimeType": "application/zip"}]},
    )
    assert response.status_code == 400, response.text
    db.expire_all()
    assert _slot_count(db) == before_slots


def test_t_file_06_size_mismatch_writes_nothing(client, db: Session) -> None:
    content = _pdf(b"short")
    declared = len(content) + 32
    created = _request_slot(client, content, size=declared)
    assert created.status_code == 201, created.text
    body = created.json()
    stored = _put_bytes(client, body["slots"][0], content)
    assert stored.status_code == 204, stored.text
    before = _case_row(db)
    before_docs = _document_count(db)
    before_audit = _audit_count(db)
    finished = _complete(client, db, body["batchId"], [body["slots"][0]["uploadId"]])
    assert finished.status_code == 400, finished.text
    payload = finished.json()
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert payload["error"]["details"]["files"] == [{"uploadId": body["slots"][0]["uploadId"], "reason": "SIZE_MISMATCH"}]
    db.expire_all()
    after = _case_row(db)
    assert after.version == before.version
    assert after.updated_at == before.updated_at
    assert _document_count(db) == before_docs
    assert _audit_count(db) == before_audit


def test_t_file_07_expired_put_is_forbidden(client, db: Session) -> None:
    content = _pdf(b"expire")
    created = _request_slot(client, content)
    assert created.status_code == 201, created.text
    slot = created.json()["slots"][0]
    upload_id = slot["uploadId"]
    from fcc_api.storage.local import sign_local_request

    expires = 1
    signature = sign_local_request(str(_settings().local_url_signing_secret), "PUT", upload_id, expires)
    response = client.put(
        f"/api/v1/local-storage/uploads/{upload_id}?expires={expires}&sig={signature}",
        content=content,
        headers=slot["headers"],
    )
    assert response.status_code == 403, response.text
    db.rollback()


def test_t_file_08_slot_request_does_not_change_case(client, db: Session) -> None:
    before = _case_row(db)
    before_audit = _audit_count(db)
    response = _request_slot(client, _pdf(b"slot-only"))
    assert response.status_code == 201, response.text
    assert "batchId" in response.json()
    db.expire_all()
    after = _case_row(db)
    assert after.version == before.version
    assert after.updated_at == before.updated_at
    assert _audit_count(db) == before_audit


def test_t_file_09_extraction_does_not_bump_version(client, db: Session) -> None:
    content = _pdf(b"extract-empty")
    before = _case_row(db)
    before_audit = _audit_count(db)
    finished, body = _register(client, db, content)
    assert finished.status_code == 201, finished.text
    upload_id = body["slots"][0]["uploadId"]
    object_key = f"cases/{CASE_ID}/documents/{upload_id}/original"
    db.expire_all()
    after = _case_row(db)
    assert after.version == before.version + 1
    assert _audit_count(db) == before_audit + 1
    document = db.execute(
        text("SELECT id, extraction FROM case_documents WHERE object_key = :object_key"),
        {"object_key": object_key},
    ).one()
    assert document.extraction == "EXTRACTED"
    runs = db.execute(
        text(
            """
            SELECT status, page_count, adapter
            FROM document_extractions
            WHERE document_id = :document_id
            """
        ),
        {"document_id": document.id},
    ).all()
    assert len(runs) == 1
    assert runs[0].status == "EXTRACTED"
    assert runs[0].page_count == 0
    assert runs[0].adapter == "noop"
    assert _case_row(db).version == before.version + 1


def test_t_file_10_metadata_only_content_url(client) -> None:
    response = client.get(f"/api/v1/cases/{CASE_ID}/documents/d-1/content-url", headers=_advisor())
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "GATE_FAILED"
    assert body["error"]["details"]["gate"] == "FILE_NOT_STORED"
    assert body["error"]["message"] == "The original file is not stored for this document."


def test_t_file_11_content_url_serves_pdf(client, db: Session) -> None:
    content = _pdf(b"preview-body")
    finished, _body = _register(client, db, content)
    assert finished.status_code == 201, finished.text
    document_id = db.execute(
        text(
            """
            SELECT id FROM case_documents
            WHERE case_id = :case_id AND sha256 = :sha256
            ORDER BY uploaded_at DESC
            LIMIT 1
            """
        ),
        {"case_id": CASE_ID, "sha256": hashlib.sha256(content).hexdigest()},
    ).scalar_one()
    issued = client.get(f"/api/v1/cases/{CASE_ID}/documents/{document_id}/content-url", headers=_advisor())
    assert issued.status_code == 200, issued.text
    parts = urlsplit(issued.json()["url"])
    downloaded = client.get(parts.path + "?" + parts.query)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["content-type"].split(";")[0] == "application/pdf"
    assert hashlib.sha256(downloaded.content).hexdigest() == hashlib.sha256(content).hexdigest()


def test_t_file_12_hidden_case_is_not_found(client) -> None:
    response = client.get("/api/v1/cases/case-0137/documents/t-1/content-url", headers=_advisor())
    assert response.status_code == 404, response.text


def test_t_file_13_logs_omit_restricted_fields(client, db: Session) -> None:
    content = _pdf(MARKER)
    captured: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.name.startswith(("httpx", "httpcore", "uvicorn.access")):
                return
            captured.append(self.format(record))

    handler = _Collect(level=logging.DEBUG)
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        finished, _body = _register(client, db, content, file_name=FILE_NAME)
        assert finished.status_code == 201, finished.text
        document_id = db.execute(
            text(
                """
                SELECT id FROM case_documents
                WHERE sha256 = :sha256
                ORDER BY uploaded_at DESC
                LIMIT 1
                """
            ),
            {"sha256": hashlib.sha256(content).hexdigest()},
        ).scalar_one()
        issued = client.get(f"/api/v1/cases/{CASE_ID}/documents/{document_id}/content-url", headers=_advisor())
        assert issued.status_code == 200, issued.text
        parts = urlsplit(issued.json()["url"])
        downloaded = client.get(parts.path + "?" + parts.query)
        assert downloaded.status_code == 200, downloaded.text
    finally:
        root.setLevel(previous)
        root.removeHandler(handler)
    blob = "\n".join(captured)
    registration = _case_row(db).registration_number
    assert FILE_NAME not in blob
    assert "sig=" not in blob
    assert MARKER_TEXT not in blob
    assert REGISTRATION_NUMBER not in blob
    assert registration not in blob
