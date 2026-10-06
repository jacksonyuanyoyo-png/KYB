"""上线追加的表。列与 docs/backend/12-production-launch.md 第 4、6、7、8、9 节一致。"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from fcc_api.db.base import Base
from fcc_api.db.models.cases import EntityType


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    EXPIRED = "EXPIRED"


class LegalHoldScope(StrEnum):
    CASE = "CASE"
    DOCUMENT = "DOCUMENT"


class DeletionJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"


class ScreeningStatus(StrEnum):
    CLEAR = "CLEAR"
    POTENTIAL_MATCH = "POTENTIAL_MATCH"
    ERROR = "ERROR"


class ScreeningDisposition(StrEnum):
    MATCH_CONFIRMED = "MATCH_CONFIRMED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class SuggestionKind(StrEnum):
    EXTRACT_FORMATION = "EXTRACT_FORMATION"
    CLASSIFY_ENTITY = "CLASSIFY_ENTITY"
    PRE_REVIEW = "PRE_REVIEW"
    ASSISTANT = "ASSISTANT"


class SuggestionStage(StrEnum):
    SUGGESTED = "SUGGESTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class DocumentEntityKind(StrEnum):
    PERSON = "PERSON"
    ENTITY = "ENTITY"


class RelationType(StrEnum):
    OWNS = "OWNS"
    CONTROLS = "CONTROLS"
    SIGNS = "SIGNS"
    DIRECTOR_OF = "DIRECTOR_OF"
    TRUSTEE_OF = "TRUSTEE_OF"
    BENEFICIARY_OF = "BENEFICIARY_OF"
    OFFICER_OF = "OFFICER_OF"


class SubmissionTarget(StrEnum):
    UDIRECT = "UDIRECT"
    UNIFIDE = "UNIFIDE"


class SubmissionStatus(StrEnum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


def _in_check(column: str, enum_cls: type[StrEnum]) -> str:
    literals = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({literals})"


def _nullable_entity_type_check() -> str:
    literals = ", ".join(f"'{member.value}'" for member in EntityType)
    return f"entity_type IS NULL OR entity_type IN ({literals})"


class RuleApproval(Base):
    __tablename__ = "rule_approvals"
    __table_args__ = (
        CheckConstraint(_in_check("approval_status", ApprovalStatus), name=conv("ck_rule_approvals_status")),
        CheckConstraint(
            "approval_status = 'PENDING' OR ("
            "effective_on IS NOT NULL AND review_due_on IS NOT NULL "
            "AND approved_by IS NOT NULL AND approval_ticket IS NOT NULL "
            "AND length(btrim(approval_ticket)) >= 1 "
            "AND submitted_by IS NOT NULL AND approved_by <> submitted_by)",
            name=conv("ck_rule_approvals_approved_fields"),
        ),
        CheckConstraint(
            "effective_on IS NULL OR review_due_on IS NULL OR review_due_on > effective_on",
            name=conv("ck_rule_approvals_review_due"),
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name=conv("ck_rule_approvals_sha256"),
        ),
        CheckConstraint(
            "length(btrim(owner_name)) >= 1",
            name=conv("ck_rule_approvals_owner_name"),
        ),
        CheckConstraint(
            "length(btrim(source_url)) >= 1",
            name=conv("ck_rule_approvals_source_url"),
        ),
        UniqueConstraint(
            "rule_id",
            "builtin_version",
            "content_sha256",
            name="uq_rule_approvals_rule_version_hash",
        ),
        Index("ix_rule_approvals_status", "approval_status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    builtin_version: Mapped[str] = mapped_column(Text, nullable=False)
    owner_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    effective_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    review_due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    test_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        server_default=sql_text("'{}'::text[]"),
    )
    content_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    approval_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sql_text("'PENDING'"),
    )
    submitted_by: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_rule_approvals_submitted_by", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_by: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_rule_approvals_approved_by", ondelete="RESTRICT"),
        nullable=True,
    )
    approval_ticket: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LegalHold(Base):
    __tablename__ = "legal_holds"
    __table_args__ = (
        CheckConstraint(_in_check("scope", LegalHoldScope), name=conv("ck_legal_holds_scope")),
        CheckConstraint(
            "char_length(reason) BETWEEN 1 AND 500",
            name=conv("ck_legal_holds_reason"),
        ),
        Index(
            "ix_legal_holds_open",
            "scope",
            "target_id",
            postgresql_where=sql_text("released_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_legal_holds_created_by", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    released_by: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_legal_holds_released_by", ondelete="RESTRICT"),
        nullable=True,
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeletionJob(Base):
    """锁未到期时任务从 RUNNING 改回 PENDING。"""

    __tablename__ = "deletion_jobs"
    __table_args__ = (
        CheckConstraint(
            _in_check("status", DeletionJobStatus),
            name=conv("ck_deletion_jobs_status"),
        ),
        Index(
            "ix_deletion_jobs_pending",
            "created_at",
            postgresql_where=sql_text("status = 'PENDING'"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_deletion_jobs_case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sql_text("'PENDING'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditOutbox(Base):
    __tablename__ = "audit_outbox"
    __table_args__ = (
        Index(
            "ix_audit_outbox_unexported",
            "event_id",
            postgresql_where=sql_text("exported_at IS NULL"),
        ),
    )

    event_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("audit_events.id", name="fk_audit_outbox_event_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScreeningRun(Base):
    __tablename__ = "screening_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "party_id"],
            ["parties.case_id", "parties.id"],
            name="fk_screening_runs_party",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            _in_check("status", ScreeningStatus),
            name=conv("ck_screening_runs_status"),
        ),
        CheckConstraint(
            "disposition IS NULL OR disposition IN ('MATCH_CONFIRMED', 'FALSE_POSITIVE')",
            name=conv("ck_screening_runs_disposition"),
        ),
        CheckConstraint(
            "(disposition IS NULL AND disposition_by IS NULL AND disposition_at IS NULL) "
            "OR (disposition IS NOT NULL AND disposition_by IS NOT NULL AND disposition_at IS NOT NULL)",
            name=conv("ck_screening_runs_disposition_shape"),
        ),
        Index("ix_screening_runs_case", "case_id", "party_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_screening_runs_case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    party_id: Mapped[str] = mapped_column(Text, nullable=False)
    adapter: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    disposition: Mapped[str | None] = mapped_column(Text, nullable=True)
    disposition_by: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_screening_runs_disposition_by", ondelete="RESTRICT"),
        nullable=True,
    )
    disposition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AiSuggestion(Base):
    __tablename__ = "ai_suggestions"
    __table_args__ = (
        CheckConstraint(_in_check("kind", SuggestionKind), name=conv("ck_ai_suggestions_kind")),
        CheckConstraint(_in_check("stage", SuggestionStage), name=conv("ck_ai_suggestions_stage")),
        Index("ix_ai_suggestions_case", "case_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_ai_suggestions_case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="SUGGESTED",
        server_default=sql_text("'SUGGESTED'"),
    )
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_ai_suggestions_created_by", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DocumentEntity(Base):
    __tablename__ = "document_entities"
    __table_args__ = (
        CheckConstraint(
            _in_check("kind", DocumentEntityKind),
            name=conv("ck_document_entities_kind"),
        ),
        CheckConstraint(
            _nullable_entity_type_check(),
            name=conv("ck_document_entities_entity_type"),
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name=conv("ck_document_entities_confidence"),
        ),
        CheckConstraint("page_no >= 1", name=conv("ck_document_entities_page_no")),
        CheckConstraint(
            "length(btrim(legal_name)) >= 1",
            name=conv("ck_document_entities_legal_name"),
        ),
        UniqueConstraint(
            "extraction_id",
            "temp_key",
            name="uq_document_entities_extraction_temp",
        ),
        Index("ix_document_entities_document", "document_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    extraction_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "document_extractions.id",
            name="fk_document_entities_extraction_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    document_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "case_documents.id",
            name="fk_document_entities_document_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    temp_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    citation: Mapped[str] = mapped_column(Text, nullable=False)
    bbox: Mapped[Any | None] = mapped_column(JSONB, nullable=True)


class DocumentRelation(Base):
    __tablename__ = "document_relations"
    __table_args__ = (
        CheckConstraint(
            _in_check("relation_type", RelationType),
            name=conv("ck_document_relations_type"),
        ),
        CheckConstraint(
            "(ownership_percent IS NULL OR ownership_percent BETWEEN 0 AND 100) "
            "AND (relation_type <> 'OWNS' OR ownership_percent IS NOT NULL)",
            name=conv("ck_document_relations_ownership"),
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name=conv("ck_document_relations_confidence"),
        ),
        CheckConstraint("page_no >= 1", name=conv("ck_document_relations_page_no")),
        Index("ix_document_relations_extraction", "extraction_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    extraction_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "document_extractions.id",
            name="fk_document_relations_extraction_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    from_temp_key: Mapped[str] = mapped_column(Text, nullable=False)
    to_temp_key: Mapped[str] = mapped_column(Text, nullable=False)
    ownership_percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    citation: Mapped[str] = mapped_column(Text, nullable=False)


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        CheckConstraint(
            _in_check("target", SubmissionTarget),
            name=conv("ck_submissions_target"),
        ),
        CheckConstraint(
            _in_check("status", SubmissionStatus),
            name=conv("ck_submissions_status"),
        ),
        CheckConstraint("version >= 1", name=conv("ck_submissions_version")),
        UniqueConstraint(
            "case_id",
            "target",
            "version",
            name="uq_submissions_case_target_version",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_submissions_case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    target: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=sql_text("'QUEUED'"),
    )
    vendor_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_submissions_created_by", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PortalInvite(Base):
    __tablename__ = "portal_invites"
    __table_args__ = (
        CheckConstraint(
            "email_sha256 ~ '^[0-9a-f]{64}$'",
            name=conv("ck_portal_invites_email_sha256"),
        ),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name=conv("ck_portal_invites_token_hash"),
        ),
        Index("ix_portal_invites_case", "case_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cases.id", name="fk_portal_invites_case_id", ondelete="RESTRICT"),
        nullable=False,
    )
    email_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    created_by: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_portal_invites_created_by", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session_token_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True, unique=True)
    session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
