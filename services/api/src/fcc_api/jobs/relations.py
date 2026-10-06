"""OCR 之后的关系抽取。noop 不调用模型，也不改案件 version。"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("fcc_api.jobs.relations")

_IDENTITY_REQUIREMENTS = frozenset({"identity", "pep"})
_PROMPT_VERSION = "relations.v1"


def pending_outcome(
    *,
    requirement_id: str | None,
    adapter: str,
    already_ready: bool,
) -> str:
    """不调用模型时的去向：NOT_APPLICABLE、REUSE，或需要模型的 CALL。"""

    if requirement_id in _IDENTITY_REQUIREMENTS:
        return "NOT_APPLICABLE"
    normalized = (adapter or "noop").strip().lower()
    if normalized in {"", "noop"}:
        return "NOT_APPLICABLE"
    if already_ready:
        return "REUSE"
    return "CALL"


def relation_model_needed(
    *,
    requirement_id: str | None,
    adapter: str,
    already_ready: bool,
) -> bool:
    return pending_outcome(
        requirement_id=requirement_id,
        adapter=adapter,
        already_ready=already_ready,
    ) == "CALL"


def run_once(session: Session | None = None) -> int:
    """处理 relation_status=PENDING 的文件。返回更新行数。不改 cases.version。"""

    owns_session = session is None
    if session is None:
        from fcc_api.db.session import SessionLocal

        session = SessionLocal()
    assert session is not None
    try:
        updated = _process(session)
        session.commit()
        return updated
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def _process(session: Session) -> int:
    if not _has_column(session, "case_documents", "relation_status"):
        return 0
    rows = session.execute(
        text(
            """
            SELECT id, case_id, requirement_id, sha256
            FROM case_documents
            WHERE relation_status = 'PENDING'
            """
        )
    ).mappings().all()
    adapter = _adapter_name()
    updated = 0
    model_calls = 0
    for row in rows:
        outcome = pending_outcome(
            requirement_id=row["requirement_id"],
            adapter=adapter,
            already_ready=_already_ready(session, row["sha256"], row["id"]),
        )
        if outcome == "CALL":
            model_calls += 1
            _call_or_fail(session, row["id"])
            updated += 1
            continue
        status = "READY" if outcome == "REUSE" else "NOT_APPLICABLE"
        updated += _set_status(session, row["id"], status)
    logger.info(
        "relation_job",
        extra={
            "pending": len(rows),
            "updated": updated,
            "model_calls": model_calls,
            "prompt_version": _PROMPT_VERSION,
            "model": adapter,
        },
    )
    return updated


def _call_or_fail(session: Session, document_id: str) -> None:
    """关系抽取仍不在这次任务里把页正文送给模型。失败只改 relation_status。"""

    _set_status(session, document_id, "FAILED")


def _already_ready(session: Session, sha256: str | None, document_id: str) -> bool:
    if not sha256 or not _has_column(session, "document_extractions", "relation_prompt_version"):
        return False
    found = session.execute(
        text(
            """
            SELECT 1
            FROM case_documents AS document
            JOIN document_extractions AS extraction
              ON extraction.document_id = document.id
            WHERE document.sha256 = :sha256
              AND document.id <> :document_id
              AND document.relation_status = 'READY'
              AND extraction.relation_prompt_version = :prompt_version
            LIMIT 1
            """
        ),
        {
            "sha256": sha256,
            "document_id": document_id,
            "prompt_version": _PROMPT_VERSION,
        },
    ).first()
    return found is not None


def _set_status(session: Session, document_id: str, status: str) -> int:
    result = session.execute(
        text(
            """
            UPDATE case_documents
            SET relation_status = :status
            WHERE id = :document_id
              AND relation_status = 'PENDING'
            """
        ),
        {"status": status, "document_id": document_id},
    )
    return int(result.rowcount or 0)


def _adapter_name() -> str:
    from fcc_api.config import get_settings

    settings: Any = get_settings()
    raw = str(getattr(settings, "llm_adapter", "noop") or "noop")
    return raw.strip().lower() or "noop"


def _has_column(session: Session, table: str, column: str) -> bool:
    found = session.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table
              AND column_name = :column
            """
        ),
        {"table": table, "column": column},
    ).first()
    return found is not None
