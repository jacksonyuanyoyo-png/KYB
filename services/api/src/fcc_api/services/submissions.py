"""Submit an approved case to uDirect or uniFide, and apply the vendor callback."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from fcc_api.adapters.submission import callback_secret, get_submitter
from fcc_api.auth.actor import Actor, fail_auth
from fcc_api.auth.policy import case_visible
from fcc_api.db.models.cases import Case
from fcc_api.errors import ApiError, not_found, unavailable, validation_failed, version_conflict
from fcc_api.ids import new_id
from fcc_api.services.case_write import CONFLICT_MESSAGE

_SUBMIT_ROLES = frozenset({"OPERATIONS", "ADMIN"})
_TARGETS = frozenset({"UDIRECT", "UNIFIDE"})
_STATUSES = frozenset({"QUEUED", "SENT", "ACCEPTED", "REJECTED"})
_UNAVAILABLE = "Submission is not configured."
_CALLBACK_UNAVAILABLE = "Submission callback is not configured."
_VERSION_COLUMNS = ("version", "case_version")
_PAYLOAD_COLUMNS = ("payload", "request_payload", "body")
_VENDOR_COLUMNS = ("vendor_reference", "vendor_ref")
_ACTOR_COLUMNS = ("created_by", "submitted_by", "actor_id")
_SAFE_COLUMNS = frozenset(
    {
        "id",
        "case_id",
        "target",
        "version",
        "case_version",
        "status",
        "vendor_reference",
        "vendor_ref",
        "payload",
        "request_payload",
        "body",
        "created_by",
        "submitted_by",
        "actor_id",
        "created_at",
        "updated_at",
        "submitted_at",
        "idempotency_key",
    }
)


@dataclass(frozen=True)
class _Column:
    name: str
    nullable: bool
    has_default: bool
    data_type: str


def submit_case(
    session: Session,
    actor: Actor,
    case_id: str,
    version: int,
    target: str,
    *,
    request_id: str,
) -> tuple[int, dict[str, Any]]:
    case = _visible_case(session, actor, case_id, request_id=request_id)
    _authorize_submit(actor, case, case_id=case_id, request_id=request_id)
    normalized = target.strip().upper()
    if normalized not in _TARGETS:
        raise validation_failed(
            "Request body is invalid.",
            {"fields": [{"path": "target", "message": "Target must be UDIRECT or UNIFIDE."}]},
        )
    submitter = get_submitter()
    if submitter.name == "unconfigured":
        raise unavailable(_UNAVAILABLE)
    if not table_exists(session, "submissions"):
        raise unavailable(_UNAVAILABLE)

    existing = _find_submission(session, case_id, normalized, version)
    if existing is not None:
        return 200, existing

    try:
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        locked = session.execute(
            text("SELECT version, status FROM cases WHERE id = :id FOR UPDATE"),
            {"id": case_id},
        ).one()
        if str(locked.status) != "APPROVED":
            _forbid_submit(actor, case_id, str(locked.status), request_id)
        existing = _find_submission(session, case_id, normalized, version)
        if existing is not None:
            session.rollback()
            return 200, existing
        if int(locked.version) != version:
            session.rollback()
            raise version_conflict(
                CONFLICT_MESSAGE,
                {
                    "resource": "case",
                    "id": case_id,
                    "clientVersion": version,
                    "currentVersion": int(locked.version),
                },
            )
        session.refresh(case)
        payload = submission_payload(session, case)
        vendor = submitter.submit(case_id=case_id, target=normalized, payload=payload)
        vendor_reference = vendor if isinstance(vendor, str) and vendor.strip() else None
        row = _insert_submission(
            session,
            case_id=case_id,
            target=normalized,
            version=version,
            actor_id=actor.id,
            payload=payload,
            vendor_reference=vendor_reference,
        )
        bumped = session.execute(
            text(
                """
                UPDATE cases
                SET version = version + 1, updated_at = :now
                WHERE id = :id AND version = :version
                RETURNING version
                """
            ),
            {"id": case_id, "version": version, "now": datetime.now(UTC)},
        ).scalar_one_or_none()
        if bumped is None:
            session.rollback()
            raise version_conflict(
                CONFLICT_MESSAGE,
                {"resource": "case", "id": case_id, "clientVersion": version},
            )
        session.commit()
    except ApiError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    return 201, row


def apply_callback(session: Session, raw_body: bytes, signature: str | None) -> dict[str, Any]:
    secret = callback_secret()
    if not secret:
        raise unavailable(_CALLBACK_UNAVAILABLE)
    if not signature or not _signature_ok(secret, raw_body, signature):
        raise ApiError(401, "UNAUTHENTICATED", "Submission callback signature is invalid.", {})
    if not table_exists(session, "submissions"):
        raise not_found("Submission not found.", {"resource": "submission", "id": ""})
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise validation_failed() from exc
    if not isinstance(body, dict):
        raise validation_failed()
    submission_id = str(body.get("submissionId") or body.get("id") or "").strip()
    status = str(body.get("status") or "").strip().upper()
    if not submission_id or status not in _STATUSES:
        raise validation_failed(
            "Request body is invalid.",
            {"fields": [{"path": "status", "message": "Status is not a submission status."}]},
        )
    has_vendor = "vendorReference" in body or "vendor_reference" in body
    vendor_reference = body.get("vendorReference", body.get("vendor_reference"))
    if vendor_reference is not None:
        vendor_reference = str(vendor_reference)
    columns = _columns(session, "submissions")
    status_column = "status" if "status" in columns else None
    vendor_column = next((name for name in _VENDOR_COLUMNS if name in columns), None)
    if status_column is None:
        raise not_found("Submission not found.", {"resource": "submission", "id": submission_id})
    assignments = [f"{status_column} = :status"]
    params: dict[str, Any] = {"status": status, "id": submission_id}
    if vendor_column is not None and has_vendor:
        assignments.append(f"{vendor_column} = :vendor_reference")
        params["vendor_reference"] = vendor_reference
    updated = session.execute(
        text(f"UPDATE submissions SET {', '.join(assignments)} WHERE id = :id RETURNING id"),
        params,
    ).first()
    if updated is None:
        session.rollback()
        raise not_found("Submission not found.", {"resource": "submission", "id": submission_id})
    session.commit()
    row = _find_by_id(session, submission_id)
    return row or {"id": submission_id, "status": status, "vendorReference": vendor_reference}


def submission_payload(session: Session, case: Case) -> dict[str, Any]:
    rows = session.execute(
        text(
            """
            SELECT id, sha256, requirement_id
            FROM case_documents
            WHERE case_id = :case_id
            ORDER BY uploaded_at, id
            """
        ),
        {"case_id": case.id},
    ).all()
    payload = {
        "reference": case.reference,
        "legalName": case.legal_name,
        "entityType": case.entity_type,
        "province": case.province or "",
        "ruleVersion": case.rule_version,
        "documents": [
            {
                "documentId": row.id,
                "sha256": row.sha256,
                "requirementId": row.requirement_id,
            }
            for row in rows
        ],
    }
    _reject_forbidden_payload(payload)
    return payload


def table_exists(session: Session, name: str) -> bool:
    if not name.replace("_", "").isalnum():
        return False
    found = session.execute(
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


def _insert_submission(
    session: Session,
    *,
    case_id: str,
    target: str,
    version: int,
    actor_id: str,
    payload: dict[str, Any],
    vendor_reference: str | None,
) -> dict[str, Any]:
    columns = _columns(session, "submissions")
    now = datetime.now(UTC)
    row_id = new_id("sub")
    values: dict[str, Any] = {}
    if "id" in columns:
        values["id"] = row_id
    if "case_id" in columns:
        values["case_id"] = case_id
    if "target" in columns:
        values["target"] = target
    version_column = next((name for name in _VERSION_COLUMNS if name in columns), None)
    if version_column is not None:
        values[version_column] = version
    if "status" in columns:
        values["status"] = "QUEUED"
    vendor_column = next((name for name in _VENDOR_COLUMNS if name in columns), None)
    if vendor_column is not None:
        values[vendor_column] = vendor_reference
    payload_column = next((name for name in _PAYLOAD_COLUMNS if name in columns), None)
    if payload_column is not None:
        values[payload_column] = payload
    actor_column = next((name for name in _ACTOR_COLUMNS if name in columns), None)
    if actor_column is not None:
        values[actor_column] = actor_id
    for name in ("created_at", "updated_at", "submitted_at"):
        if name in columns:
            values[name] = now
    if "idempotency_key" in columns:
        values["idempotency_key"] = f"{case_id}:{target}:{version}"
    missing = [
        column.name
        for column in columns.values()
        if not column.nullable and not column.has_default and column.name not in values and column.name in _SAFE_COLUMNS
    ]
    if missing:
        raise unavailable(_UNAVAILABLE)
    if not values:
        raise unavailable(_UNAVAILABLE)
    column_names = [name for name in values if name in _SAFE_COLUMNS and name in columns]
    placeholders = []
    params: dict[str, Any] = {}
    for name in column_names:
        data_type = columns[name].data_type
        if data_type in {"json", "jsonb"}:
            placeholders.append(f"CAST(:{name} AS {data_type})")
            params[name] = json.dumps(values[name])
        else:
            placeholders.append(f":{name}")
            params[name] = values[name]
    session.execute(
        text(
            f"INSERT INTO submissions ({', '.join(column_names)}) VALUES ({', '.join(placeholders)})"
        ),
        params,
    )
    if "status" in columns:
        session.execute(
            text("UPDATE submissions SET status = :status WHERE id = :id"),
            {"status": "SENT", "id": row_id},
        )
    session.flush()
    found = _find_by_id(session, row_id)
    if found is None:
        return {
            "id": row_id,
            "status": "SENT",
            "target": target,
            "version": version,
            "vendorReference": vendor_reference,
        }
    return found


def _find_submission(session: Session, case_id: str, target: str, version: int) -> dict[str, Any] | None:
    columns = _columns(session, "submissions")
    clauses = ["case_id = :case_id", "target = :target"] if "case_id" in columns and "target" in columns else []
    params: dict[str, Any] = {"case_id": case_id, "target": target}
    version_column = next((name for name in _VERSION_COLUMNS if name in columns), None)
    if version_column is not None:
        clauses.append(f"{version_column} = :version")
        params["version"] = version
    elif "idempotency_key" in columns:
        clauses = ["idempotency_key = :idempotency_key"]
        params = {"idempotency_key": f"{case_id}:{target}:{version}"}
    else:
        return None
    selected = _select_list(columns)
    row = session.execute(
        text(f"SELECT {selected} FROM submissions WHERE {' AND '.join(clauses)} ORDER BY id LIMIT 1"),
        params,
    ).mappings().first()
    if row is None:
        return None
    return _public_row(row)


def _find_by_id(session: Session, submission_id: str) -> dict[str, Any] | None:
    columns = _columns(session, "submissions")
    if "id" not in columns:
        return None
    row = session.execute(
        text(f"SELECT {_select_list(columns)} FROM submissions WHERE id = :id"),
        {"id": submission_id},
    ).mappings().first()
    if row is None:
        return None
    return _public_row(row)


def _select_list(columns: dict[str, _Column]) -> str:
    names = [name for name in ("id", "case_id", "target", "version", "case_version", "status", "vendor_reference", "vendor_ref") if name in columns]
    return ", ".join(names) if names else "id"


def _public_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    vendor = data.get("vendor_reference", data.get("vendor_ref"))
    version = data.get("version", data.get("case_version"))
    return {
        "id": data.get("id"),
        "status": data.get("status"),
        "target": data.get("target"),
        "version": version,
        "vendorReference": vendor,
    }


def _columns(session: Session, table: str) -> dict[str, _Column]:
    rows = session.execute(
        text(
            """
            SELECT column_name, is_nullable, column_default, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table
            """
        ),
        {"table": table},
    ).all()
    return {
        row.column_name: _Column(
            name=row.column_name,
            nullable=row.is_nullable == "YES",
            has_default=row.column_default is not None,
            data_type=row.data_type,
        )
        for row in rows
        if row.column_name in _SAFE_COLUMNS
    }


def _signature_ok(secret: str, body: bytes, presented: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    candidate = presented.strip().lower()
    if candidate.startswith("sha256="):
        candidate = candidate[len("sha256=") :]
    if len(candidate) != len(expected):
        return False
    return hmac.compare_digest(expected, candidate)


def _reject_forbidden_payload(payload: dict[str, Any]) -> None:
    def walk(value: object, key: str | None = None) -> None:
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise validation_failed()
        if key is not None and "registration" in key.lower():
            raise validation_failed()
        if isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                walk(child, key)

    walk(payload)


def _visible_case(session: Session, actor: Actor, case_id: str, *, request_id: str) -> Case:
    case = session.get(Case, case_id)
    if case is None or not case_visible(actor, case):
        fail_auth(
            status_code=404,
            code="NOT_FOUND",
            message="Case not found.",
            details={"resource": "case", "id": case_id},
            reason="NOT_FOUND",
            case_id=case_id,
            user_id=actor.id,
            role=actor.role.value,
            request_id=request_id,
        )
    return case


def _authorize_submit(actor: Actor, case: Case, *, case_id: str, request_id: str) -> None:
    if actor.role.value not in _SUBMIT_ROLES or case.status != "APPROVED":
        _forbid_submit(actor, case_id, str(case.status), request_id)


def _forbid_submit(actor: Actor, case_id: str, status: str, request_id: str) -> None:
    if status == "APPROVED":
        message = "Your role cannot perform this operation."
    else:
        message = f"This case is {status} and can no longer be edited by your role."
    fail_auth(
        status_code=403,
        code="FORBIDDEN",
        message=message,
        details={"reason": "CASE_STATUS", "status": status, "operation": "submitCase"},
        reason="CASE_STATUS",
        operation="submitCase",
        case_id=case_id,
        user_id=actor.id,
        role=actor.role.value,
        request_id=request_id,
    )
