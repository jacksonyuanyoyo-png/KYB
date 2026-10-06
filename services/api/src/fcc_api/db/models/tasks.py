"""复核任务。删除股权节点时只把 ``party_id`` 置空。"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    false,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from fcc_api.db.base import Base


class TaskSource(StrEnum):
    COMPLIANCE = "COMPLIANCE"
    AI = "AI"
    MANUAL = "MANUAL"


def _in_check(column: str, enum_cls: type[StrEnum]) -> str:
    literals = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({literals})"


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        # PostgreSQL 15+：只把 party_id 置空，case_id 保持 NOT NULL。
        ForeignKeyConstraint(
            ["case_id", "party_id"],
            ["parties.case_id", "parties.id"],
            name="fk_review_tasks_party",
            ondelete="SET NULL (party_id)",
        ),
        CheckConstraint(
            "length(btrim(title)) BETWEEN 1 AND 500",
            name=conv("ck_review_tasks_title"),
        ),
        CheckConstraint(_in_check("source", TaskSource), name=conv("ck_review_tasks_source")),
        Index("ix_tasks_case", "case_id", "created_at"),
        Index(
            "ix_tasks_open",
            "created_at",
            postgresql_where=sql_text("NOT done"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_review_tasks_case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    party_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirement_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_review_tasks_created_by", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    done_by: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_review_tasks_done_by", ondelete="RESTRICT"),
        nullable=True,
    )
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
