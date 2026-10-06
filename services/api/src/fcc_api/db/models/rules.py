"""规则版本、草稿与当前发布指针。

``rule_versions`` 不可变（禁止 UPDATE / DELETE）。触发器必须由 Alembic 迁移创建。
本模型不注册 SQLAlchemy DDL event：项目禁止 ``metadata.create_all()``，自动生成也
抽不到触发器，挂上 event 会和迁移各执行一次。

迁移请复制以下 SQL。

-- upgrade
CREATE FUNCTION rule_versions_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'rule_versions is immutable (rejected %)', TG_OP;
END;
$$;

CREATE TRIGGER rule_versions_immutable
BEFORE UPDATE OR DELETE ON rule_versions
FOR EACH ROW
EXECUTE FUNCTION rule_versions_immutable();

-- downgrade
DROP TRIGGER IF EXISTS rule_versions_immutable ON rule_versions;
DROP FUNCTION IF EXISTS rule_versions_immutable();
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from fcc_api.db.base import Base


class RuleVersionKind(StrEnum):
    BUILTIN = "BUILTIN"
    PUBLISHED = "PUBLISHED"


class RuleDraftStatus(StrEnum):
    OPEN = "OPEN"
    PUBLISHED = "PUBLISHED"
    DISCARDED = "DISCARDED"


def _in_check(column: str, enum_cls: type[StrEnum]) -> str:
    literals = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({literals})"


class RuleVersion(Base):
    """已发布规则快照。行不可变，触发器见模块文档。"""

    __tablename__ = "rule_versions"
    __table_args__ = (
        CheckConstraint(_in_check("kind", RuleVersionKind), name=conv("ck_rule_versions_kind")),
        CheckConstraint(
            "kind = 'PUBLISHED' OR ("
            "extras = '[]'::jsonb AND disabled = '{}' AND overrides = '{}'::jsonb"
            ")",
            name=conv("ck_rule_versions_builtin_plain"),
        ),
        CheckConstraint(
            "kind = 'BUILTIN' OR published_by IS NOT NULL",
            name=conv("ck_rule_versions_published_actor"),
        ),
        {
            "comment": "不可变。迁移必须创建 BEFORE UPDATE OR DELETE 触发器 rule_versions_immutable。",
            "info": {"immutable": True, "triggers": ("rule_versions_immutable",)},
        },
    )

    version: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    builtin_version: Mapped[str] = mapped_column(Text, nullable=False)
    extras: Mapped[Any] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'[]'::jsonb"),
    )
    disabled: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        server_default=sql_text("'{}'::text[]"),
    )
    overrides: Mapped[Any] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )
    published_by: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_rule_versions_published_by", ondelete="RESTRICT"),
        nullable=True,
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parity_report: Mapped[Any | None] = mapped_column(JSONB, nullable=True)


class RuleLibraryState(Base):
    """单行表：当前发布版本 + 规则库乐观锁。"""

    __tablename__ = "rule_library_state"
    __table_args__ = (
        CheckConstraint("id = 1", name=conv("ck_rule_library_state_id")),
        CheckConstraint("version >= 1", name=conv("ck_rule_library_state_version")),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    published_version: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "rule_versions.version",
            name="fk_rule_library_state_published_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("1"))
    updated_by: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_rule_library_state_updated_by", ondelete="RESTRICT"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuleDraft(Base):
    __tablename__ = "rule_drafts"
    __table_args__ = (
        CheckConstraint(_in_check("status", RuleDraftStatus), name=conv("ck_rule_drafts_status")),
        CheckConstraint(
            "(status = 'OPEN') = (closed_at IS NULL)",
            name=conv("ck_rule_drafts_open_closed"),
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR published_version IS NOT NULL",
            name=conv("ck_rule_drafts_published_version"),
        ),
        # 同一时刻最多一份打开的草稿。
        Index(
            "uq_rule_drafts_one_open",
            sql_text("(true)"),
            unique=True,
            postgresql_where=sql_text("status = 'OPEN'"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    base_version: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "rule_versions.version",
            name="fk_rule_drafts_base_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    extras: Mapped[Any] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'[]'::jsonb"),
    )
    disabled: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        server_default=sql_text("'{}'::text[]"),
    )
    overrides: Mapped[Any] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_rule_drafts_started_by", ondelete="RESTRICT"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_rule_drafts_updated_by", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_by: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_rule_drafts_closed_by", ondelete="RESTRICT"),
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_version: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "rule_versions.version",
            name="fk_rule_drafts_published_version",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
