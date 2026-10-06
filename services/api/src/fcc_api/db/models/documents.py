"""文件元数据与上传槽。不存放文件字节。"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from fcc_api.db.base import Base

# 与 UPLOAD_MAX_BYTES 同步。
_UPLOAD_MAX_BYTES = 25_000_000


class ExtractionStatus(StrEnum):
    NONE = "NONE"
    PROCESSING = "PROCESSING"
    EXTRACTED = "EXTRACTED"
    FAILED = "FAILED"


class StorageState(StrEnum):
    QUARANTINED = "QUARANTINED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    METADATA_ONLY = "METADATA_ONLY"


class ScanStatus(StrEnum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    ERROR = "ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CdrStatus(StrEnum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    ERROR = "ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RelationStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class UploadSlotStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


def _in_check(column: str, enum_cls: type[StrEnum]) -> str:
    literals = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({literals})"


class UploadSlot(Base):
    __tablename__ = "upload_slots"
    __table_args__ = (
        CheckConstraint(
            "length(file_name) BETWEEN 1 AND 255",
            name=conv("ck_upload_slots_file_name"),
        ),
        CheckConstraint(
            f"declared_size BETWEEN 1 AND {_UPLOAD_MAX_BYTES}",
            name=conv("ck_upload_slots_declared_size"),
        ),
        CheckConstraint(
            _in_check("status", UploadSlotStatus),
            name=conv("ck_upload_slots_status"),
        ),
        Index("ix_upload_slots_case_batch", "case_id", "batch_id"),
        Index(
            "ix_upload_slots_pending",
            "expires_at",
            postgresql_where=sql_text("status = 'PENDING'"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_upload_slots_case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    declared_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_mime: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sql_text("'PENDING'"),
    )
    created_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_upload_slots_created_by", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    multipart_upload_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class CaseDocument(Base):
    __tablename__ = "case_documents"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name=conv("ck_case_documents_size_bytes")),
        CheckConstraint(
            _in_check("extraction", ExtractionStatus),
            name=conv("ck_case_documents_extraction"),
        ),
        CheckConstraint(
            _in_check("storage_state", StorageState),
            name=conv("ck_case_documents_storage_state"),
        ),
        CheckConstraint(
            _in_check("scan_status", ScanStatus),
            name=conv("ck_case_documents_scan_status"),
        ),
        CheckConstraint(
            _in_check("cdr_status", CdrStatus),
            name=conv("ck_case_documents_cdr_status"),
        ),
        CheckConstraint(
            _in_check("relation_status", RelationStatus),
            name=conv("ck_case_documents_relation_status"),
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name=conv("ck_case_documents_sha256"),
        ),
        CheckConstraint(
            "(storage_state = 'METADATA_ONLY') = (object_key IS NULL)",
            name=conv("ck_case_documents_metadata_only_key"),
        ),
        CheckConstraint(
            "storage_state = 'METADATA_ONLY' OR sha256 IS NOT NULL",
            name=conv("ck_case_documents_sha256_required"),
        ),
        CheckConstraint(
            "storage_state <> 'ACCEPTED' OR scan_status = 'CLEAN'",
            name=conv("ck_case_documents_accepted_clean"),
        ),
        Index("ix_documents_case_requirement", "case_id", "requirement_id"),
        Index(
            "ix_documents_unassigned",
            "case_id",
            postgresql_where=sql_text("requirement_id IS NULL"),
        ),
        Index(
            "ix_documents_extraction",
            "extraction",
            postgresql_where=sql_text("extraction = 'PROCESSING'"),
        ),
        Index("ix_documents_uploaded_at", sql_text("uploaded_at DESC")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_case_documents_case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    requirement_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_case_documents_uploaded_by", ondelete="RESTRICT"),
        nullable=False,
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    extraction: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sql_text("'NONE'"),
    )
    storage_state: Mapped[str] = mapped_column(Text, nullable=False)
    scan_status: Mapped[str] = mapped_column(Text, nullable=False)
    cdr_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="NOT_APPLICABLE",
        server_default=sql_text("'NOT_APPLICABLE'"),
    )
    relation_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="NOT_APPLICABLE",
        server_default=sql_text("'NOT_APPLICABLE'"),
    )
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    detected_mime: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_slot_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "upload_slots.id",
            name="fk_case_documents_upload_slot_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        unique=True,
    )
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scanned_by: Mapped[str | None] = mapped_column(Text, nullable=True)
