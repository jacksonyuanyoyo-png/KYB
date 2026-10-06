"""上线追加表和列。不删除旧列。内置规则批准行保持 PENDING。

Revision ID: 0003_launch
Revises: 0002_reference_data
Create Date: 2026-10-06

content_sha256 是展示字段的规范化 JSON（键排序、无空白、UTF-8）的 SHA-256。
字段为 name、section、conditional、source、reason、trigger。
reason 用规则页在没有 override 时的默认值，即规则 name。
source 用规则页 BUILTIN 已有的 source。
"""

from __future__ import annotations

import hashlib
import json

from alembic import op
from sqlalchemy import text

from fcc_api.rules.builtin_catalog import BUILTIN
from fcc_api.rules.constants import RULE_VERSION

revision = "0003_launch"
down_revision = "0002_reference_data"
branch_labels = None
depends_on = None

_BUILTIN_IDS = (
    "naaf",
    "formation",
    "resolution",
    "beneficial-owner",
    "directors",
    "tcp",
    "identity",
    "pep",
    "margin",
    "options",
    "cod-dvp",
    "fpl",
    "w9",
    "w8",
    "rc519",
    "nffe",
)

_AUDIT_ACTIONS_PREVIOUS = (
    "CASE_CREATED",
    "OWNERSHIP_UPDATED",
    "PROFILE_UPDATED",
    "DOCUMENT_UPLOADED",
    "CHECKLIST_UPDATED",
    "REQUIREMENT_ADDED",
    "STATUS_CHANGED",
    "COMPLIANCE_DECISION",
    "AI_SUGGESTION",
    "TASK_UPDATED",
    "RULE_LIBRARY",
)
_AUDIT_ACTIONS = _AUDIT_ACTIONS_PREVIOUS + ("SCREENING_RECORDED", "RETENTION_DELETED")


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)
    _seed_builtin_approvals()


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)


def _seed_builtin_approvals() -> None:
    found = {rule.id for rule in BUILTIN}
    if found != set(_BUILTIN_IDS):
        missing = sorted(set(_BUILTIN_IDS) - found)
        extra = sorted(found - set(_BUILTIN_IDS))
        raise RuntimeError(
            f"builtin catalog does not match the launch seed; missing={missing} extra={extra}"
        )
    bind = op.get_bind()
    insert = text(
        """
        INSERT INTO rule_approvals (
            id, rule_id, builtin_version, owner_name, source_url,
            effective_on, review_due_on, test_ids, content_sha256,
            approval_status, submitted_by, approved_by, approval_ticket,
            approved_at
        ) VALUES (
            :id, :rule_id, :builtin_version, :owner_name, :source_url,
            NULL, NULL, '{}'::text[], :content_sha256,
            'PENDING', NULL, NULL, NULL,
            NULL
        )
        """
    )
    for rule in BUILTIN:
        bind.execute(
            insert,
            {
                "id": f"apr_{rule.id}",
                "rule_id": rule.id,
                "builtin_version": RULE_VERSION,
                "owner_name": "Fidelity Compliance",
                "source_url": rule.source,
                "content_sha256": _content_sha256(rule),
            },
        )


def _content_sha256(rule: object) -> str:
    payload = {
        "conditional": rule.conditional,
        "name": rule.name,
        "reason": rule.name,
        "section": rule.section,
        "source": rule.source,
        "trigger": rule.trigger,
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _action_check(actions: tuple[str, ...]) -> str:
    literals = ", ".join(f"'{action}'" for action in actions)
    return f"""
    ALTER TABLE audit_events DROP CONSTRAINT ck_audit_events_action
    """ , f"""
    ALTER TABLE audit_events ADD CONSTRAINT ck_audit_events_action CHECK (
        action IN ({literals})
    )
    """


_NEW_ACTION_DROP, _NEW_ACTION_ADD = _action_check(_AUDIT_ACTIONS)
_OLD_ACTION_DROP, _OLD_ACTION_ADD = _action_check(_AUDIT_ACTIONS_PREVIOUS)

_UPGRADE: tuple[str, ...] = (
    """
    ALTER TABLE users
        ADD COLUMN entra_oid text,
        ADD COLUMN last_login_at timestamptz,
        ADD COLUMN deactivated_at timestamptz
    """,
    """
    ALTER TABLE users
        ADD CONSTRAINT uq_users_entra_oid UNIQUE (entra_oid)
    """,
    """
    ALTER TABLE cases
        ADD COLUMN approved_at timestamptz
    """,
    """
    ALTER TABLE case_documents
        ADD COLUMN cdr_status text NOT NULL DEFAULT 'NOT_APPLICABLE',
        ADD COLUMN relation_status text NOT NULL DEFAULT 'NOT_APPLICABLE'
    """,
    """
    ALTER TABLE case_documents
        ADD CONSTRAINT ck_case_documents_cdr_status CHECK (
            cdr_status IN ('PENDING', 'CLEAN', 'ERROR', 'NOT_APPLICABLE')
        )
    """,
    """
    ALTER TABLE case_documents
        ADD CONSTRAINT ck_case_documents_relation_status CHECK (
            relation_status IN ('PENDING', 'READY', 'FAILED', 'NOT_APPLICABLE')
        )
    """,
    """
    ALTER TABLE upload_slots
        ADD COLUMN multipart_upload_id text
    """,
    """
    ALTER TABLE document_extractions
        ADD COLUMN relation_prompt_version text,
        ADD COLUMN relation_model text
    """,
    _NEW_ACTION_DROP,
    _NEW_ACTION_ADD,
    """
    CREATE TABLE rule_approvals (
        id text NOT NULL,
        rule_id text NOT NULL,
        builtin_version text NOT NULL,
        owner_name text NOT NULL,
        source_url text NOT NULL,
        effective_on date,
        review_due_on date,
        test_ids text[] NOT NULL DEFAULT '{}'::text[],
        content_sha256 char(64) NOT NULL,
        approval_status text NOT NULL DEFAULT 'PENDING',
        submitted_by text,
        approved_by text,
        approval_ticket text,
        submitted_at timestamptz NOT NULL DEFAULT now(),
        approved_at timestamptz,
        CONSTRAINT pk_rule_approvals PRIMARY KEY (id),
        CONSTRAINT uq_rule_approvals_rule_version_hash
            UNIQUE (rule_id, builtin_version, content_sha256),
        CONSTRAINT ck_rule_approvals_status CHECK (
            approval_status IN ('PENDING', 'APPROVED', 'EXPIRED')
        ),
        CONSTRAINT ck_rule_approvals_approved_fields CHECK (
            approval_status = 'PENDING'
            OR (
                effective_on IS NOT NULL
                AND review_due_on IS NOT NULL
                AND approved_by IS NOT NULL
                AND approval_ticket IS NOT NULL
                AND length(btrim(approval_ticket)) >= 1
                AND submitted_by IS NOT NULL
                AND approved_by <> submitted_by
            )
        ),
        CONSTRAINT ck_rule_approvals_review_due CHECK (
            effective_on IS NULL
            OR review_due_on IS NULL
            OR review_due_on > effective_on
        ),
        CONSTRAINT ck_rule_approvals_sha256 CHECK (
            content_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_rule_approvals_owner_name CHECK (
            length(btrim(owner_name)) >= 1
        ),
        CONSTRAINT ck_rule_approvals_source_url CHECK (
            length(btrim(source_url)) >= 1
        ),
        CONSTRAINT fk_rule_approvals_submitted_by
            FOREIGN KEY (submitted_by) REFERENCES users (id) ON DELETE RESTRICT,
        CONSTRAINT fk_rule_approvals_approved_by
            FOREIGN KEY (approved_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_rule_approvals_status ON rule_approvals (approval_status)
    """,
    """
    CREATE TABLE legal_holds (
        id text NOT NULL,
        scope text NOT NULL,
        target_id text NOT NULL,
        reason text NOT NULL,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        released_by text,
        released_at timestamptz,
        CONSTRAINT pk_legal_holds PRIMARY KEY (id),
        CONSTRAINT ck_legal_holds_scope CHECK (scope IN ('CASE', 'DOCUMENT')),
        CONSTRAINT ck_legal_holds_reason CHECK (
            char_length(reason) BETWEEN 1 AND 500
        ),
        CONSTRAINT fk_legal_holds_created_by
            FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
        CONSTRAINT fk_legal_holds_released_by
            FOREIGN KEY (released_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_legal_holds_open ON legal_holds (scope, target_id)
    WHERE released_at IS NULL
    """,
    """
    CREATE TABLE deletion_jobs (
        id text NOT NULL,
        case_id text NOT NULL,
        status text NOT NULL DEFAULT 'PENDING',
        created_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz,
        CONSTRAINT pk_deletion_jobs PRIMARY KEY (id),
        CONSTRAINT ck_deletion_jobs_status CHECK (
            status IN ('PENDING', 'RUNNING', 'COMPLETED')
        ),
        CONSTRAINT fk_deletion_jobs_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_deletion_jobs_pending ON deletion_jobs (created_at)
    WHERE status = 'PENDING'
    """,
    """
    CREATE TABLE audit_outbox (
        event_id text NOT NULL,
        payload jsonb NOT NULL,
        exported_at timestamptz,
        CONSTRAINT pk_audit_outbox PRIMARY KEY (event_id),
        CONSTRAINT fk_audit_outbox_event_id
            FOREIGN KEY (event_id) REFERENCES audit_events (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_audit_outbox_unexported ON audit_outbox (event_id)
    WHERE exported_at IS NULL
    """,
    """
    CREATE TABLE screening_runs (
        id text NOT NULL,
        case_id text NOT NULL,
        party_id text NOT NULL,
        adapter text NOT NULL,
        status text NOT NULL,
        reference text,
        disposition text,
        disposition_by text,
        disposition_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_screening_runs PRIMARY KEY (id),
        CONSTRAINT ck_screening_runs_status CHECK (
            status IN ('CLEAR', 'POTENTIAL_MATCH', 'ERROR')
        ),
        CONSTRAINT ck_screening_runs_disposition CHECK (
            disposition IS NULL
            OR disposition IN ('MATCH_CONFIRMED', 'FALSE_POSITIVE')
        ),
        CONSTRAINT ck_screening_runs_disposition_shape CHECK (
            (
                disposition IS NULL
                AND disposition_by IS NULL
                AND disposition_at IS NULL
            ) OR (
                disposition IS NOT NULL
                AND disposition_by IS NOT NULL
                AND disposition_at IS NOT NULL
            )
        ),
        CONSTRAINT fk_screening_runs_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT,
        CONSTRAINT fk_screening_runs_party
            FOREIGN KEY (case_id, party_id) REFERENCES parties (case_id, id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_screening_runs_disposition_by
            FOREIGN KEY (disposition_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_screening_runs_case ON screening_runs (case_id, party_id)
    """,
    """
    CREATE TABLE ai_suggestions (
        id text NOT NULL,
        case_id text NOT NULL,
        kind text NOT NULL,
        stage text NOT NULL DEFAULT 'SUGGESTED',
        prompt_version text,
        model text,
        body jsonb NOT NULL,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_ai_suggestions PRIMARY KEY (id),
        CONSTRAINT ck_ai_suggestions_kind CHECK (
            kind IN (
                'EXTRACT_FORMATION', 'CLASSIFY_ENTITY', 'PRE_REVIEW', 'ASSISTANT'
            )
        ),
        CONSTRAINT ck_ai_suggestions_stage CHECK (
            stage IN ('SUGGESTED', 'ACCEPTED', 'REJECTED')
        ),
        CONSTRAINT fk_ai_suggestions_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT,
        CONSTRAINT fk_ai_suggestions_created_by
            FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_ai_suggestions_case ON ai_suggestions (case_id)
    """,
    """
    CREATE TABLE document_entities (
        id text NOT NULL,
        extraction_id text NOT NULL,
        document_id text NOT NULL,
        temp_key text NOT NULL,
        kind text NOT NULL,
        legal_name text NOT NULL,
        entity_type text,
        title text,
        country text,
        confidence numeric(4, 3) NOT NULL,
        page_no integer NOT NULL,
        citation text NOT NULL,
        bbox jsonb,
        CONSTRAINT pk_document_entities PRIMARY KEY (id),
        CONSTRAINT uq_document_entities_extraction_temp
            UNIQUE (extraction_id, temp_key),
        CONSTRAINT ck_document_entities_kind CHECK (kind IN ('PERSON', 'ENTITY')),
        CONSTRAINT ck_document_entities_entity_type CHECK (
            entity_type IS NULL OR entity_type IN (
                'corporation', 'charity', 'trust', 'ipp_rca', 'partnership',
                'estate', 'condo', 'pooled_fund', 'association', 'first_nation'
            )
        ),
        CONSTRAINT ck_document_entities_confidence CHECK (
            confidence BETWEEN 0 AND 1
        ),
        CONSTRAINT ck_document_entities_page_no CHECK (page_no >= 1),
        CONSTRAINT ck_document_entities_legal_name CHECK (
            length(btrim(legal_name)) >= 1
        ),
        CONSTRAINT fk_document_entities_extraction_id
            FOREIGN KEY (extraction_id) REFERENCES document_extractions (id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_document_entities_document_id
            FOREIGN KEY (document_id) REFERENCES case_documents (id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_document_entities_document ON document_entities (document_id)
    """,
    """
    CREATE TABLE document_relations (
        id text NOT NULL,
        extraction_id text NOT NULL,
        relation_type text NOT NULL,
        from_temp_key text NOT NULL,
        to_temp_key text NOT NULL,
        ownership_percent numeric(7, 4),
        confidence numeric(4, 3) NOT NULL,
        page_no integer NOT NULL,
        citation text NOT NULL,
        CONSTRAINT pk_document_relations PRIMARY KEY (id),
        CONSTRAINT ck_document_relations_type CHECK (
            relation_type IN (
                'OWNS', 'CONTROLS', 'SIGNS', 'DIRECTOR_OF', 'TRUSTEE_OF',
                'BENEFICIARY_OF', 'OFFICER_OF'
            )
        ),
        CONSTRAINT ck_document_relations_ownership CHECK (
            (ownership_percent IS NULL OR ownership_percent BETWEEN 0 AND 100)
            AND (relation_type <> 'OWNS' OR ownership_percent IS NOT NULL)
        ),
        CONSTRAINT ck_document_relations_confidence CHECK (
            confidence BETWEEN 0 AND 1
        ),
        CONSTRAINT ck_document_relations_page_no CHECK (page_no >= 1),
        CONSTRAINT fk_document_relations_extraction_id
            FOREIGN KEY (extraction_id) REFERENCES document_extractions (id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_document_relations_extraction
    ON document_relations (extraction_id)
    """,
    """
    CREATE TABLE submissions (
        id text NOT NULL,
        case_id text NOT NULL,
        target text NOT NULL,
        version integer NOT NULL,
        status text NOT NULL DEFAULT 'QUEUED',
        vendor_reference text,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_submissions PRIMARY KEY (id),
        CONSTRAINT uq_submissions_case_target_version
            UNIQUE (case_id, target, version),
        CONSTRAINT ck_submissions_target CHECK (
            target IN ('UDIRECT', 'UNIFIDE')
        ),
        CONSTRAINT ck_submissions_status CHECK (
            status IN ('QUEUED', 'SENT', 'ACCEPTED', 'REJECTED')
        ),
        CONSTRAINT ck_submissions_version CHECK (version >= 1),
        CONSTRAINT fk_submissions_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT,
        CONSTRAINT fk_submissions_created_by
            FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE portal_invites (
        id text NOT NULL,
        case_id text NOT NULL,
        email_sha256 char(64) NOT NULL,
        token_hash char(64) NOT NULL,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        CONSTRAINT pk_portal_invites PRIMARY KEY (id),
        CONSTRAINT uq_portal_invites_token_hash UNIQUE (token_hash),
        CONSTRAINT ck_portal_invites_email_sha256 CHECK (
            email_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_portal_invites_token_hash CHECK (
            token_hash ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT fk_portal_invites_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT,
        CONSTRAINT fk_portal_invites_created_by
            FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_portal_invites_case ON portal_invites (case_id)
    """,
)

_DOWNGRADE: tuple[str, ...] = (
    "DROP TABLE IF EXISTS portal_invites",
    "DROP TABLE IF EXISTS submissions",
    "DROP TABLE IF EXISTS document_relations",
    "DROP TABLE IF EXISTS document_entities",
    "DROP TABLE IF EXISTS ai_suggestions",
    "DROP TABLE IF EXISTS screening_runs",
    "DROP TABLE IF EXISTS audit_outbox",
    "DROP TABLE IF EXISTS deletion_jobs",
    "DROP TABLE IF EXISTS legal_holds",
    "DROP TABLE IF EXISTS rule_approvals",
    "ALTER TABLE document_extractions DROP COLUMN IF EXISTS relation_model",
    "ALTER TABLE document_extractions DROP COLUMN IF EXISTS relation_prompt_version",
    "ALTER TABLE upload_slots DROP COLUMN IF EXISTS multipart_upload_id",
    "ALTER TABLE case_documents DROP COLUMN IF EXISTS relation_status",
    "ALTER TABLE case_documents DROP COLUMN IF EXISTS cdr_status",
    "ALTER TABLE cases DROP COLUMN IF EXISTS approved_at",
    "ALTER TABLE users DROP COLUMN IF EXISTS deactivated_at",
    "ALTER TABLE users DROP COLUMN IF EXISTS last_login_at",
    "ALTER TABLE users DROP COLUMN IF EXISTS entra_oid",
    _OLD_ACTION_DROP,
    _OLD_ACTION_ADD,
)
