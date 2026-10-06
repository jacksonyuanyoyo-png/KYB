"""一次抽取运行、分页 OCR 正文和抽出的字段。正文不是文件字节。"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from fcc_api.db.base import Base


class ExtractionRunStatus(StrEnum):
    PROCESSING = "PROCESSING"
    EXTRACTED = "EXTRACTED"
    FAILED = "FAILED"


def _in_check(column: str, enum_cls: type[StrEnum]) -> str:
    literals = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({literals})"


class DocumentExtraction(Base):
    __tablename__ = "document_extractions"
    __table_args__ = (
        CheckConstraint(
            _in_check("status", ExtractionRunStatus),
            name=conv("ck_document_extractions_status"),
        ),
        Index("ix_extractions_document", "document_id", sql_text("started_at DESC")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "case_documents.id",
            name="fk_document_extractions_document_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    adapter: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    relation_prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    relation_model: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExtractionPage(Base):
    __tablename__ = "extraction_pages"
    __table_args__ = (
        CheckConstraint("page_no >= 1", name=conv("ck_extraction_pages_page_no")),
    )

    extraction_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "document_extractions.id",
            name="fk_extraction_pages_extraction_id",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    page_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 该页 OCR 正文。Restricted，不是原文件。
    text: Mapped[str] = mapped_column(Text, nullable=False)


class ExtractionField(Base):
    __tablename__ = "extraction_fields"
    __table_args__ = (
        CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name=conv("ck_extraction_fields_confidence"),
        ),
        Index("ix_extraction_fields_group", "extraction_id", "group_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    extraction_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "document_extractions.id",
            name="fk_extraction_fields_extraction_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    group_key: Mapped[str] = mapped_column(Text, nullable=False)
    field_key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    citation: Mapped[str | None] = mapped_column(Text, nullable=True)
    bbox: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
