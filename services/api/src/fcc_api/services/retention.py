"""保留期删除。不清 audit_events，缺 approved_at 时直接返回。"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from fcc_api.config import get_settings
from fcc_api.ids import new_id
from fcc_api.storage.local import resolve_storage_root

_SUMMARY = "Retention period elapsed; case contents deleted."
_CLEARED_FIELDS = (
    "legalName",
    "registrationNumber",
    "jurisdiction",
    "province",
    "taxResidency",
    "features",
    "trustedContact",
    "trustedContactName",
)
# 按外键依赖从叶子到根。绝不包含 audit_events。
_CHILD_TABLES = (
    "extraction_fields",
    "extraction_pages",
    "document_relations",
    "document_entities",
    "document_extractions",
    "form_fills",
    "ai_suggestions",
    "screening_runs",
    "review_tasks",
    "checklist_items",
    "custom_requirements",
    "case_documents",
    "upload_slots",
    "parties",
)
_NEVER_DELETE = frozenset({"audit_events", "cases", "users", "legal_holds", "audit_outbox"})
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def delete_expired_cases(session: Session, *, retention_days: int | None = None) -> None:
    days = _retention_days(retention_days)
    try:
        with session.begin_nested():
            rows = session.execute(
                text(
                    """
                    SELECT id
                    FROM cases
                    WHERE status = 'APPROVED'
                      AND approved_at IS NOT NULL
                      AND approved_at + (:days * INTERVAL '1 day') < now()
                      AND btrim(legal_name) <> '[deleted]'
                    """
                ),
                {"days": days},
            ).all()
    except SQLAlchemyError as exc:
        if _sqlstate(exc) == "42703":
            return
        raise
    held = _held_case_ids(session)
    actor_id = _retention_actor_id(session)
    for row in rows:
        case_id = str(row.id)
        if case_id in held:
            continue
        if _purge_case(session, case_id, actor_id):
            session.commit()


def _purge_case(session: Session, case_id: str, actor_id: str | None) -> bool:
    with session.begin_nested():
        locked = session.execute(
            text(
                """
                SELECT id, version, rule_version, legal_name, status
                FROM cases
                WHERE id = :id
                FOR UPDATE
                """
            ),
            {"id": case_id},
        ).one()
        if locked.status != "APPROVED" or str(locked.legal_name).strip() == "[deleted]":
            return False
        if case_id in _held_case_ids(session):
            return False
        job_id = _insert_job(session, case_id)
        if _object_lock_active(session, case_id):
            return job_id is not None
        _delete_local_objects(session, case_id)
        _delete_children(session, case_id)
        session.execute(
            text(
                """
                UPDATE cases
                SET legal_name = '[deleted]',
                    registration_number = '',
                    jurisdiction = '',
                    province = '',
                    tax_residency = NULL,
                    features = '{}'::text[],
                    trusted_contact = NULL,
                    trusted_contact_name = '',
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": case_id},
        )
        if actor_id is None:
            raise RuntimeError("retention delete requires an actor user")
        correlation_id = job_id or new_id("job")
        session.execute(
            text(
                """
                INSERT INTO audit_events (
                    id, scope, case_id, actor_id, action, summary, at, version,
                    changes, ai_model, ai_accepted, ai_rule_version, rule_version,
                    before_value, after_value, correlation_id, request_id
                ) VALUES (
                    :id, 'CASE', :case_id, :actor_id, 'RETENTION_DELETED',
                    :summary, :at, :version,
                    NULL, NULL, NULL, NULL, :rule_version,
                    CAST(:before_value AS jsonb), NULL, :correlation_id, :request_id
                )
                """
            ),
            {
                "id": new_id("evt"),
                "case_id": case_id,
                "actor_id": actor_id,
                "summary": _SUMMARY,
                "at": datetime.now(UTC),
                "version": int(locked.version),
                "rule_version": locked.rule_version,
                "before_value": json.dumps(list(_CLEARED_FIELDS)),
                "correlation_id": correlation_id,
                "request_id": new_id("req"),
            },
        )
        _complete_job(session, job_id)
    return True


def _held_case_ids(session: Session) -> set[str]:
    if not _relation_exists(session, "legal_holds"):
        return set()
    rows = session.execute(
        text(
            """
            SELECT scope, target_id
            FROM legal_holds
            WHERE released_at IS NULL
            """
        )
    ).all()
    case_ids: set[str] = set()
    document_ids: list[str] = []
    for scope, target_id in rows:
        if scope == "CASE":
            case_ids.add(str(target_id))
        elif scope == "DOCUMENT":
            document_ids.append(str(target_id))
    if document_ids and _relation_exists(session, "case_documents"):
        linked = session.execute(
            text("SELECT case_id FROM case_documents WHERE id = ANY(:ids)"),
            {"ids": document_ids},
        ).scalars().all()
        case_ids.update(str(case_id) for case_id in linked)
    return case_ids


def _delete_children(session: Session, case_id: str) -> None:
    extras = _extra_extraction_tables(session)
    ordered = list(extras) + [name for name in _CHILD_TABLES if name not in extras]
    for table in ordered:
        _delete_from_table(session, table, case_id)


def _delete_from_table(session: Session, table: str, case_id: str) -> None:
    if table in _NEVER_DELETE or table == "audit_events":
        raise RuntimeError("refusing to delete audit_events or protected tables")
    if _IDENT.fullmatch(table) is None or not _relation_exists(session, table):
        return
    columns = _column_names(session, table)
    if "extraction_id" in columns and "case_id" not in columns and "document_id" not in columns:
        if not _relation_exists(session, "document_extractions"):
            return
        session.execute(
            text(
                f"""
                DELETE FROM {table}
                WHERE extraction_id IN (
                    SELECT de.id
                    FROM document_extractions de
                    JOIN case_documents cd ON cd.id = de.document_id
                    WHERE cd.case_id = :case_id
                )
                """
            ),
            {"case_id": case_id},
        )
        return
    if "case_id" in columns:
        session.execute(
            text(f"DELETE FROM {table} WHERE case_id = :case_id"),
            {"case_id": case_id},
        )
        return
    if "document_id" in columns and _relation_exists(session, "case_documents"):
        session.execute(
            text(
                f"""
                DELETE FROM {table}
                WHERE document_id IN (
                    SELECT id FROM case_documents WHERE case_id = :case_id
                )
                """
            ),
            {"case_id": case_id},
        )


def _extra_extraction_tables(session: Session) -> list[str]:
    rows = session.execute(
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = ANY (current_schemas(false))
              AND table_name LIKE 'extraction\\_%' ESCAPE '\\'
            ORDER BY table_name
            """
        )
    ).scalars().all()
    known = set(_CHILD_TABLES)
    return [str(name) for name in rows if str(name) not in known and str(name) not in _NEVER_DELETE]


def _delete_local_objects(session: Session, case_id: str) -> None:
    if not _relation_exists(session, "case_documents"):
        return
    columns = _column_names(session, "case_documents")
    if "object_key" not in columns:
        return
    keys = session.execute(
        text(
            """
            SELECT object_key
            FROM case_documents
            WHERE case_id = :case_id AND object_key IS NOT NULL
            """
        ),
        {"case_id": case_id},
    ).scalars().all()
    root = resolve_storage_root(get_settings())
    for key in keys:
        _unlink_object(root, str(key))


def _unlink_object(root: Path, object_key: str) -> None:
    relative = Path(object_key)
    if relative.is_absolute() or ".." in relative.parts:
        return
    base = root.resolve()
    for bucket in ("accepted", "quarantine"):
        path = (base / bucket / relative).resolve()
        try:
            path.relative_to(base)
        except ValueError:
            continue
        if path.is_file():
            path.unlink()


def _object_lock_active(session: Session, case_id: str) -> bool:
    if not _relation_exists(session, "case_documents"):
        return False
    columns = _column_names(session, "case_documents")
    for name in ("retain_until", "retain_until_date"):
        if name not in columns or _IDENT.fullmatch(name) is None:
            continue
        found = session.execute(
            text(
                f"""
                SELECT 1
                FROM case_documents
                WHERE case_id = :case_id
                  AND {name} IS NOT NULL
                  AND {name} > now()
                """
            ),
            {"case_id": case_id},
        ).first()
        if found is not None:
            return True
    return False


def _insert_job(session: Session, case_id: str) -> str | None:
    if not _relation_exists(session, "deletion_jobs"):
        return None
    columns = _column_names(session, "deletion_jobs")
    job_id = new_id("job")
    payload: dict[str, Any] = {}
    if "id" in columns:
        payload["id"] = job_id
    if "case_id" in columns:
        payload["case_id"] = case_id
    if "status" in columns:
        payload["status"] = "PENDING"
    if "created_at" in columns:
        payload["created_at"] = datetime.now(UTC)
    if "id" not in payload or "status" not in payload:
        return None
    if any(_IDENT.fullmatch(name) is None for name in payload):
        return None
    names = ", ".join(payload)
    values = ", ".join(f":{name}" for name in payload)
    try:
        with session.begin_nested():
            session.execute(
                text(f"INSERT INTO deletion_jobs ({names}) VALUES ({values})"),
                payload,
            )
    except SQLAlchemyError as exc:
        if _sqlstate(exc) in {"23502", "23514", "42703"}:
            return None
        raise
    return job_id


def _complete_job(session: Session, job_id: str | None) -> None:
    if job_id is None or not _relation_exists(session, "deletion_jobs"):
        return
    columns = _column_names(session, "deletion_jobs")
    if "status" not in columns:
        return
    try:
        with session.begin_nested():
            session.execute(
                text("UPDATE deletion_jobs SET status = 'COMPLETED' WHERE id = :id"),
                {"id": job_id},
            )
    except SQLAlchemyError as exc:
        if _sqlstate(exc) in {"23514", "42703"}:
            return
        raise


def _retention_actor_id(session: Session) -> str | None:
    admin = session.execute(
        text(
            """
            SELECT id FROM users
            WHERE role = 'ADMIN' AND active IS TRUE
            ORDER BY id
            LIMIT 1
            """
        )
    ).scalar()
    if admin:
        return str(admin)
    fallback = session.execute(text("SELECT id FROM users ORDER BY id LIMIT 1")).scalar()
    return None if fallback is None else str(fallback)


def _retention_days(explicit: int | None) -> int:
    if explicit is not None:
        return int(explicit)
    settings = get_settings()
    raw = getattr(settings, "retention_case_days", None)
    if raw is None:
        raw = os.environ.get("RETENTION_CASE_DAYS", "2555")
    return int(raw)


def _relation_exists(session: Session, name: str) -> bool:
    found = session.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = :name
              AND table_schema = ANY (current_schemas(false))
            """
        ),
        {"name": name},
    ).first()
    return found is not None


def _column_names(session: Session, table: str) -> set[str]:
    rows = session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = :table
              AND table_schema = ANY (current_schemas(false))
            """
        ),
        {"table": table},
    ).scalars().all()
    return {str(name) for name in rows}


def _sqlstate(exc: BaseException) -> str | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attr in ("sqlstate", "pgcode"):
            code = getattr(current, attr, None)
            if code:
                return str(code)
        nested = current.__cause__ or getattr(current, "orig", None)
        current = nested if isinstance(nested, BaseException) else None
    return None
