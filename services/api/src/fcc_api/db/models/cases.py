"""案件主行与按年的参考号计数器。

``ix_cases_search`` 依赖 pg_trgm。扩展不在模型里创建，迁移需先执行：

    CREATE EXTENSION IF NOT EXISTS pg_trgm;
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from fcc_api.db.base import Base


class EntityType(StrEnum):
    CORPORATION = "corporation"
    CHARITY = "charity"
    TRUST = "trust"
    IPP_RCA = "ipp_rca"
    PARTNERSHIP = "partnership"
    ESTATE = "estate"
    CONDO = "condo"
    POOLED_FUND = "pooled_fund"
    ASSOCIATION = "association"
    FIRST_NATION = "first_nation"


class CaseStatus(StrEnum):
    BUILDING = "BUILDING"
    DOCS_REQUESTED = "DOCS_REQUESTED"
    READY_FOR_COMPLIANCE = "READY_FOR_COMPLIANCE"
    RETURNED = "RETURNED"
    APPROVED = "APPROVED"


class TaxResidency(StrEnum):
    CANADA = "CANADA"
    US = "US"
    INTERNATIONAL = "INTERNATIONAL"
    MIXED = "MIXED"


class AccountFeature(StrEnum):
    MARGIN = "MARGIN"
    OPTIONS = "OPTIONS"
    COD_DVP = "COD_DVP"
    FPL = "FPL"


# labels.ts PROVINCES。空字符串表示还没选省。
PROVINCE_CODES: tuple[str, ...] = (
    "AB",
    "BC",
    "MB",
    "NB",
    "NL",
    "NS",
    "NT",
    "NU",
    "ON",
    "PE",
    "QC",
    "SK",
    "YT",
)


def _in_check(column: str, enum_cls: type[StrEnum]) -> str:
    literals = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({literals})"


def _province_check() -> str:
    codes = ", ".join(f"'{code}'" for code in PROVINCE_CODES)
    return f"province = '' OR province IN ({codes})"


def _features_check() -> str:
    values = ", ".join(f"'{member.value}'" for member in AccountFeature)
    return f"features <@ ARRAY[{values}]::text[]"


class CaseReferenceCounter(Base):
    """建案参考号按 UTC 年份计数。``last_value`` 从 0 起，加一后写入案件。"""

    __tablename__ = "case_reference_counters"
    __table_args__ = (
        CheckConstraint("last_value >= 0", name=conv("ck_case_reference_counters_last_value")),
    )

    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint(
            "reference ~ '^FCC-[0-9]{4}-[0-9]{4,}$'",
            name=conv("ck_cases_reference"),
        ),
        CheckConstraint("version >= 1", name=conv("ck_cases_version")),
        CheckConstraint(
            "length(btrim(legal_name)) BETWEEN 1 AND 300",
            name=conv("ck_cases_legal_name"),
        ),
        CheckConstraint(_in_check("entity_type", EntityType), name=conv("ck_cases_entity_type")),
        CheckConstraint(_in_check("status", CaseStatus), name=conv("ck_cases_status")),
        CheckConstraint(_province_check(), name=conv("ck_cases_province")),
        CheckConstraint(_in_check("tax_residency", TaxResidency), name=conv("ck_cases_tax_residency")),
        CheckConstraint(_features_check(), name=conv("ck_cases_features")),
        Index("ix_cases_status_updated", "status", sql_text("updated_at DESC")),
        Index("ix_cases_owner", "owner_id"),
        Index("ix_cases_created_by", "created_by"),
        Index("ix_cases_entity_type", "entity_type"),
        Index("ix_cases_rule_version", "rule_version"),
        Index(
            "ix_cases_search",
            sql_text(
                "lower(legal_name || ' ' || reference || ' ' || registration_number) gin_trgm_ops"
            ),
            postgresql_using="gin",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    reference: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("1"))
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sql_text("'BUILDING'"),
    )
    rule_version: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "rule_versions.version",
            name="fk_cases_rule_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    owner_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_cases_owner_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_cases_created_by", ondelete="RESTRICT"),
        nullable=False,
    )
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("''"))
    registration_number: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sql_text("''"),
    )
    province: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("''"))
    tax_residency: Mapped[str | None] = mapped_column(Text, nullable=True)
    features: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        server_default=sql_text("'{}'::text[]"),
    )
    trusted_contact: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    trusted_contact_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sql_text("''"),
    )
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
