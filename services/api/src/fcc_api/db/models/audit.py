"""只追加审计。

禁止 UPDATE、DELETE、TRUNCATE。触发器必须由 Alembic 迁移创建。本模型不注册
SQLAlchemy DDL event：项目禁止 ``metadata.create_all()``，自动生成也抽不到触发器。

PostgreSQL 里 TRUNCATE 触发器只能是 FOR EACH STATEMENT，不能和 FOR EACH ROW 写在
一起。因此用一条语句级触发器同时挡住 UPDATE、DELETE、TRUNCATE，名字仍是
``audit_events_append_only``。

应用角色授权（``fcc_app`` 仅 INSERT、SELECT）也由迁移处理，不在模型里。

迁移请复制以下 SQL。

-- upgrade
CREATE FUNCTION audit_events_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only (rejected %)', TG_OP;
END;
$$;

CREATE TRIGGER audit_events_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
FOR EACH STATEMENT
EXECUTE FUNCTION audit_events_append_only();

-- downgrade
DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events;
DROP FUNCTION IF EXISTS audit_events_append_only();
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from fcc_api.db.base import Base


class AuditScope(StrEnum):
    CASE = "CASE"
    RULE_LIBRARY = "RULE_LIBRARY"


class AuditAction(StrEnum):
    CASE_CREATED = "CASE_CREATED"
    OWNERSHIP_UPDATED = "OWNERSHIP_UPDATED"
    PROFILE_UPDATED = "PROFILE_UPDATED"
    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    CHECKLIST_UPDATED = "CHECKLIST_UPDATED"
    REQUIREMENT_ADDED = "REQUIREMENT_ADDED"
    STATUS_CHANGED = "STATUS_CHANGED"
    COMPLIANCE_DECISION = "COMPLIANCE_DECISION"
    AI_SUGGESTION = "AI_SUGGESTION"
    TASK_UPDATED = "TASK_UPDATED"
    RULE_LIBRARY = "RULE_LIBRARY"
    SCREENING_RECORDED = "SCREENING_RECORDED"
    RETENTION_DELETED = "RETENTION_DELETED"


def _in_check(column: str, enum_cls: type[StrEnum]) -> str:
    literals = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({literals})"


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(_in_check("scope", AuditScope), name=conv("ck_audit_events_scope")),
        CheckConstraint(_in_check("action", AuditAction), name=conv("ck_audit_events_action")),
        CheckConstraint(
            "length(summary) BETWEEN 1 AND 500",
            name=conv("ck_audit_events_summary"),
        ),
        CheckConstraint("version >= 1", name=conv("ck_audit_events_version")),
        CheckConstraint(
            "(scope = 'CASE') = (case_id IS NOT NULL)",
            name=conv("ck_audit_events_case_scope"),
        ),
        CheckConstraint(
            "(scope = 'RULE_LIBRARY') = (action = 'RULE_LIBRARY')",
            name=conv("ck_audit_events_rule_library_action"),
        ),
        CheckConstraint(
            "(ai_model IS NULL) = (ai_accepted IS NULL) "
            "AND (ai_model IS NULL) = (ai_rule_version IS NULL)",
            name=conv("ck_audit_events_ai_trio"),
        ),
        CheckConstraint(
            "ai_model IS NULL OR action = 'AI_SUGGESTION'",
            name=conv("ck_audit_events_ai_action"),
        ),
        Index("ix_audit_case_seq", "case_id", sql_text("seq DESC")),
        Index("ix_audit_actor_seq", "actor_id", sql_text("seq DESC")),
        Index("ix_audit_action_seq", "action", sql_text("seq DESC")),
        Index("ix_audit_seq", sql_text("seq DESC")),
        {
            "comment": (
                "只追加。禁止 UPDATE、DELETE、TRUNCATE。"
                "迁移必须创建语句级触发器 audit_events_append_only。"
            ),
            "info": {
                "append_only": True,
                "immutable": True,
                "triggers": ("audit_events_append_only",),
            },
        },
    )

    seq: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        nullable=False,
        unique=True,
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_audit_events_case_id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_audit_events_actor_id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changes: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    ai_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_rule_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 不是外键：规则库事件可能写草稿版本号，草稿不在 rule_versions 里。
    rule_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    after_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
