"""把 audit_outbox 里尚未导出的行写到本地审计目录。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from fcc_api.services.audit_export import backfill_audit_outbox, export_pending_outbox


def run_once(session: Session) -> None:
    export_pending_outbox(session)


def backfill_outbox(session: Session) -> None:
    backfill_audit_outbox(session)
