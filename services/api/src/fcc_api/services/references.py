"""生成 FCC-{年}-{序号}。序号按 UTC 年份加锁递增。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from fcc_api.db.models.cases import CaseReferenceCounter


def next_reference(session: Session, now: datetime) -> str:
    year = now.astimezone(UTC).year
    session.execute(
        text(
            "INSERT INTO case_reference_counters (year, last_value) VALUES (:year, 0) "
            "ON CONFLICT (year) DO NOTHING"
        ),
        {"year": year},
    )
    row = session.scalar(
        select(CaseReferenceCounter)
        .where(CaseReferenceCounter.year == year)
        .with_for_update()
    )
    if row is None:
        raise RuntimeError("case reference counter was not created")
    row.last_value += 1
    return f"FCC-{year}-{row.last_value:04d}"
