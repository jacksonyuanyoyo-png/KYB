"""股权树：邻接表。每个案件至多一个根（``parent_id IS NULL``）。

「至少一个根」由服务端在提交前检查，不在数据库里强制。

``ix_parties_name`` 依赖 pg_trgm。扩展由迁移创建：

    CREATE EXTENSION IF NOT EXISTS pg_trgm;
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    false,
    func,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from fcc_api.db.base import Base
from fcc_api.db.models.cases import EntityType


class PartyKind(StrEnum):
    ENTITY = "ENTITY"
    PERSON = "PERSON"


class UsTaxClass(StrEnum):
    COMPLEX = "complex"
    SIMPLE = "simple"
    UNSURE = "unsure"


def _in_check(column: str, enum_cls: type[StrEnum]) -> str:
    literals = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({literals})"


class Party(Base):
    __tablename__ = "parties"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "parent_id"],
            ["parties.case_id", "parties.id"],
            name="fk_parties_parent",
            # 不写 ON DELETE。保持 NO ACTION，延迟检查才能在同一事务里改树。
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "case_id",
            "position",
            name="uq_parties_case_position",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("id ~ '^[A-Za-z0-9_-]{1,64}$'", name=conv("ck_parties_id_format")),
        CheckConstraint(_in_check("kind", PartyKind), name=conv("ck_parties_kind")),
        CheckConstraint(
            "length(btrim(legal_name)) BETWEEN 1 AND 300",
            name=conv("ck_parties_legal_name"),
        ),
        CheckConstraint(_in_check("entity_type", EntityType), name=conv("ck_parties_entity_type")),
        CheckConstraint(_in_check("us_tax_class", UsTaxClass), name=conv("ck_parties_us_tax_class")),
        CheckConstraint(
            "ownership_percent BETWEEN 0 AND 100",
            name=conv("ck_parties_ownership_percent"),
        ),
        CheckConstraint(
            "parent_id IS NOT NULL OR kind = 'ENTITY'",
            name=conv("ck_parties_root_is_entity"),
        ),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name=conv("ck_parties_parent_not_self"),
        ),
        CheckConstraint(
            "kind = 'ENTITY' OR (entity_type IS NULL AND us_tax_class IS NULL)",
            name=conv("ck_parties_person_shape"),
        ),
        CheckConstraint(
            "kind = 'PERSON' OR entity_type IS NOT NULL",
            name=conv("ck_parties_entity_has_type"),
        ),
        Index(
            "uq_parties_one_root",
            "case_id",
            unique=True,
            postgresql_where=sql_text("parent_id IS NULL"),
        ),
        # 文档在可延迟唯一约束之外又列了这条查找索引。
        Index("ix_parties_case_position", "case_id", "position"),
        Index(
            "ix_parties_pep",
            "case_id",
            postgresql_where=sql_text("is_pep_hio"),
        ),
        Index(
            "ix_parties_us",
            "case_id",
            postgresql_where=sql_text("is_us_person"),
        ),
        Index(
            "ix_parties_name",
            sql_text("lower(legal_name) gin_trgm_ops"),
            postgresql_using="gin",
        ),
    )

    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_parties_case_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    us_tax_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    ownership_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    is_controller: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    is_signing_authority: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=false(),
    )
    is_us_person: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    is_pep_hio: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
