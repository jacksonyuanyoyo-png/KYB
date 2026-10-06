"""清单状态与工作人员追加的清单项。``requirement_id`` 不是外键。"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from fcc_api.db.base import Base


class DocStatus(StrEnum):
    MISSING = "MISSING"
    REQUESTED = "REQUESTED"
    RECEIVED = "RECEIVED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


def _in_check(column: str, enum_cls: type[StrEnum]) -> str:
    literals = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({literals})"


class ChecklistItem(Base):
    __tablename__ = "checklist_items"
    __table_args__ = (
        CheckConstraint(_in_check("status", DocStatus), name=conv("ck_checklist_items_status")),
        Index("ix_checklist_case_status", "case_id", "status"),
    )

    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_checklist_items_case_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    requirement_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_checklist_items_updated_by", ondelete="RESTRICT"),
        nullable=False,
    )


class CustomRequirement(Base):
    __tablename__ = "custom_requirements"
    __table_args__ = (
        CheckConstraint(
            "length(btrim(name)) BETWEEN 1 AND 200",
            name=conv("ck_custom_requirements_name"),
        ),
        UniqueConstraint("case_id", "position", name="uq_custom_requirements_case_position"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_custom_requirements_case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_custom_requirements_created_by", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
