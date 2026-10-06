"""用户。"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Text,
    func,
    text as sql_text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from fcc_api.db.base import Base


class Role(StrEnum):
    ADVISOR = "ADVISOR"
    OPERATIONS = "OPERATIONS"
    COMPLIANCE = "COMPLIANCE"
    ADMIN = "ADMIN"


def _in_check(column: str, enum_cls: type[StrEnum]) -> str:
    literals = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({literals})"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in_check("role", Role), name=conv("ck_users_role")),
        # 3.1：按 lower(email) 唯一，而不是 email 列本身的大小写敏感唯一约束。
        Index("uq_users_email_lower", sql_text("lower(email)"), unique=True),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    team: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    entra_oid: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
