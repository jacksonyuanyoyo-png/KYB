"""建全部业务表、约束、索引、扩展和只追加触发器。

手写 SQL，与 ``fcc_api.db.models`` 的表、CHECK、部分唯一索引、可延迟外键和
触发器一致。``pg_trgm``、``DEFERRABLE``、``ON DELETE SET NULL (party_id)``、
触发器和 ``fcc_app`` 授权自动生成抽不到，所以不走 ``create_all()``。

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-06
"""

from __future__ import annotations

import os

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # 审计表只允许在本地或测试环境拆掉。未设置时跟随 Settings 的默认 local。
    app_env = _app_env()
    if app_env not in {"local", "test"}:
        raise RuntimeError(
            "audit_events downgrade is only allowed when APP_ENV is local or test"
        )
    for statement in _DOWNGRADE:
        op.execute(statement)


def _app_env() -> str:
    raw = os.environ.get("APP_ENV", "").strip()
    if raw:
        return raw
    try:
        from fcc_api.config import Settings

        return Settings().app_env
    except Exception:
        return ""


_UPGRADE: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    """
    CREATE FUNCTION rule_versions_immutable()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'rule_versions is immutable (rejected %)', TG_OP;
    END;
    $$
    """,
    """
    CREATE FUNCTION audit_events_append_only()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'audit_events is append-only (rejected %)', TG_OP;
    END;
    $$
    """,
    """
    CREATE TABLE users (
        id text NOT NULL,
        name text NOT NULL,
        email text NOT NULL,
        role text NOT NULL,
        team text NOT NULL,
        active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_users PRIMARY KEY (id),
        CONSTRAINT ck_users_role CHECK (
            role IN ('ADVISOR', 'OPERATIONS', 'COMPLIANCE', 'ADMIN')
        )
    )
    """,
    "CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))",
    """
    CREATE TABLE rule_versions (
        version text NOT NULL,
        kind text NOT NULL,
        builtin_version text NOT NULL,
        extras jsonb NOT NULL DEFAULT '[]'::jsonb,
        disabled text[] NOT NULL DEFAULT '{}'::text[],
        overrides jsonb NOT NULL DEFAULT '{}'::jsonb,
        published_by text,
        published_at timestamptz NOT NULL,
        parity_report jsonb,
        CONSTRAINT pk_rule_versions PRIMARY KEY (version),
        CONSTRAINT ck_rule_versions_kind CHECK (kind IN ('BUILTIN', 'PUBLISHED')),
        CONSTRAINT ck_rule_versions_builtin_plain CHECK (
            kind = 'PUBLISHED' OR (
                extras = '[]'::jsonb
                AND disabled = '{}'
                AND overrides = '{}'::jsonb
            )
        ),
        CONSTRAINT ck_rule_versions_published_actor CHECK (
            kind = 'BUILTIN' OR published_by IS NOT NULL
        ),
        CONSTRAINT fk_rule_versions_published_by
            FOREIGN KEY (published_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    COMMENT ON TABLE rule_versions IS
    '不可变。迁移必须创建 BEFORE UPDATE OR DELETE 触发器 rule_versions_immutable。'
    """,
    """
    CREATE TRIGGER rule_versions_immutable
    BEFORE UPDATE OR DELETE ON rule_versions
    FOR EACH ROW
    EXECUTE FUNCTION rule_versions_immutable()
    """,
    """
    CREATE TABLE rule_library_state (
        id smallint NOT NULL,
        published_version text NOT NULL,
        version integer NOT NULL DEFAULT 1,
        updated_by text,
        updated_at timestamptz NOT NULL,
        CONSTRAINT pk_rule_library_state PRIMARY KEY (id),
        CONSTRAINT ck_rule_library_state_id CHECK (id = 1),
        CONSTRAINT ck_rule_library_state_version CHECK (version >= 1),
        CONSTRAINT fk_rule_library_state_published_version
            FOREIGN KEY (published_version) REFERENCES rule_versions (version)
            ON DELETE RESTRICT,
        CONSTRAINT fk_rule_library_state_updated_by
            FOREIGN KEY (updated_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE rule_drafts (
        id text NOT NULL,
        version text NOT NULL,
        base_version text NOT NULL,
        extras jsonb NOT NULL DEFAULT '[]'::jsonb,
        disabled text[] NOT NULL DEFAULT '{}'::text[],
        overrides jsonb NOT NULL DEFAULT '{}'::jsonb,
        status text NOT NULL,
        started_by text NOT NULL,
        started_at timestamptz NOT NULL,
        updated_by text NOT NULL,
        updated_at timestamptz NOT NULL,
        closed_by text,
        closed_at timestamptz,
        published_version text,
        CONSTRAINT pk_rule_drafts PRIMARY KEY (id),
        CONSTRAINT uq_rule_drafts_version UNIQUE (version),
        CONSTRAINT ck_rule_drafts_status CHECK (
            status IN ('OPEN', 'PUBLISHED', 'DISCARDED')
        ),
        CONSTRAINT ck_rule_drafts_open_closed CHECK (
            (status = 'OPEN') = (closed_at IS NULL)
        ),
        CONSTRAINT ck_rule_drafts_published_version CHECK (
            status <> 'PUBLISHED' OR published_version IS NOT NULL
        ),
        CONSTRAINT fk_rule_drafts_base_version
            FOREIGN KEY (base_version) REFERENCES rule_versions (version)
            ON DELETE RESTRICT,
        CONSTRAINT fk_rule_drafts_started_by
            FOREIGN KEY (started_by) REFERENCES users (id) ON DELETE RESTRICT,
        CONSTRAINT fk_rule_drafts_updated_by
            FOREIGN KEY (updated_by) REFERENCES users (id) ON DELETE RESTRICT,
        CONSTRAINT fk_rule_drafts_closed_by
            FOREIGN KEY (closed_by) REFERENCES users (id) ON DELETE RESTRICT,
        CONSTRAINT fk_rule_drafts_published_version
            FOREIGN KEY (published_version) REFERENCES rule_versions (version)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE UNIQUE INDEX uq_rule_drafts_one_open
    ON rule_drafts ((true))
    WHERE status = 'OPEN'
    """,
    """
    CREATE TABLE case_reference_counters (
        year integer NOT NULL,
        last_value integer NOT NULL,
        CONSTRAINT pk_case_reference_counters PRIMARY KEY (year),
        CONSTRAINT ck_case_reference_counters_last_value CHECK (last_value >= 0)
    )
    """,
    """
    CREATE TABLE cases (
        id text NOT NULL,
        reference text NOT NULL,
        version integer NOT NULL DEFAULT 1,
        legal_name text NOT NULL,
        entity_type text NOT NULL,
        status text NOT NULL DEFAULT 'BUILDING',
        rule_version text NOT NULL,
        owner_id text NOT NULL,
        created_by text NOT NULL,
        jurisdiction text NOT NULL DEFAULT '',
        registration_number text NOT NULL DEFAULT '',
        province text NOT NULL DEFAULT '',
        tax_residency text,
        features text[] NOT NULL DEFAULT '{}'::text[],
        trusted_contact boolean,
        trusted_contact_name text NOT NULL DEFAULT '',
        due_date timestamptz NOT NULL,
        submitted_at timestamptz,
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL,
        CONSTRAINT pk_cases PRIMARY KEY (id),
        CONSTRAINT uq_cases_reference UNIQUE (reference),
        CONSTRAINT ck_cases_reference CHECK (
            reference ~ '^FCC-[0-9]{4}-[0-9]{4,}$'
        ),
        CONSTRAINT ck_cases_version CHECK (version >= 1),
        CONSTRAINT ck_cases_legal_name CHECK (
            length(btrim(legal_name)) BETWEEN 1 AND 300
        ),
        CONSTRAINT ck_cases_entity_type CHECK (
            entity_type IN (
                'corporation', 'charity', 'trust', 'ipp_rca', 'partnership',
                'estate', 'condo', 'pooled_fund', 'association', 'first_nation'
            )
        ),
        CONSTRAINT ck_cases_status CHECK (
            status IN (
                'BUILDING', 'DOCS_REQUESTED', 'READY_FOR_COMPLIANCE',
                'RETURNED', 'APPROVED'
            )
        ),
        CONSTRAINT ck_cases_province CHECK (
            province = '' OR province IN (
                'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE',
                'QC', 'SK', 'YT'
            )
        ),
        CONSTRAINT ck_cases_tax_residency CHECK (
            tax_residency IN ('CANADA', 'US', 'INTERNATIONAL', 'MIXED')
        ),
        CONSTRAINT ck_cases_features CHECK (
            features <@ ARRAY['MARGIN', 'OPTIONS', 'COD_DVP', 'FPL']::text[]
        ),
        CONSTRAINT fk_cases_rule_version
            FOREIGN KEY (rule_version) REFERENCES rule_versions (version)
            ON DELETE RESTRICT,
        CONSTRAINT fk_cases_owner_id
            FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE RESTRICT,
        CONSTRAINT fk_cases_created_by
            FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_cases_status_updated ON cases (status, updated_at DESC)
    """,
    "CREATE INDEX ix_cases_owner ON cases (owner_id)",
    "CREATE INDEX ix_cases_created_by ON cases (created_by)",
    "CREATE INDEX ix_cases_entity_type ON cases (entity_type)",
    "CREATE INDEX ix_cases_rule_version ON cases (rule_version)",
    """
    CREATE INDEX ix_cases_search ON cases USING gin (
        (lower(legal_name || ' ' || reference || ' ' || registration_number))
        gin_trgm_ops
    )
    """,
    """
    CREATE TABLE parties (
        case_id text NOT NULL,
        id text NOT NULL,
        parent_id text,
        position integer NOT NULL,
        kind text NOT NULL,
        legal_name text NOT NULL,
        entity_type text,
        country text,
        title text,
        us_tax_class text,
        ownership_percent numeric(7, 4) NOT NULL,
        is_controller boolean NOT NULL DEFAULT false,
        is_signing_authority boolean NOT NULL DEFAULT false,
        is_us_person boolean NOT NULL DEFAULT false,
        is_pep_hio boolean NOT NULL DEFAULT false,
        CONSTRAINT pk_parties PRIMARY KEY (case_id, id),
        CONSTRAINT uq_parties_case_position UNIQUE (case_id, position)
            DEFERRABLE INITIALLY DEFERRED,
        CONSTRAINT ck_parties_id_format CHECK (id ~ '^[A-Za-z0-9_-]{1,64}$'),
        CONSTRAINT ck_parties_kind CHECK (kind IN ('ENTITY', 'PERSON')),
        CONSTRAINT ck_parties_legal_name CHECK (
            length(btrim(legal_name)) BETWEEN 1 AND 300
        ),
        CONSTRAINT ck_parties_entity_type CHECK (
            entity_type IN (
                'corporation', 'charity', 'trust', 'ipp_rca', 'partnership',
                'estate', 'condo', 'pooled_fund', 'association', 'first_nation'
            )
        ),
        CONSTRAINT ck_parties_us_tax_class CHECK (
            us_tax_class IN ('complex', 'simple', 'unsure')
        ),
        CONSTRAINT ck_parties_ownership_percent CHECK (
            ownership_percent BETWEEN 0 AND 100
        ),
        CONSTRAINT ck_parties_root_is_entity CHECK (
            parent_id IS NOT NULL OR kind = 'ENTITY'
        ),
        CONSTRAINT ck_parties_parent_not_self CHECK (
            parent_id IS NULL OR parent_id <> id
        ),
        CONSTRAINT ck_parties_person_shape CHECK (
            kind = 'ENTITY' OR (entity_type IS NULL AND us_tax_class IS NULL)
        ),
        CONSTRAINT ck_parties_entity_has_type CHECK (
            kind = 'PERSON' OR entity_type IS NOT NULL
        ),
        CONSTRAINT fk_parties_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT,
        CONSTRAINT fk_parties_parent
            FOREIGN KEY (case_id, parent_id) REFERENCES parties (case_id, id)
            DEFERRABLE INITIALLY DEFERRED
    )
    """,
    """
    CREATE UNIQUE INDEX uq_parties_one_root ON parties (case_id)
    WHERE parent_id IS NULL
    """,
    "CREATE INDEX ix_parties_case_position ON parties (case_id, position)",
    """
    CREATE INDEX ix_parties_pep ON parties (case_id) WHERE is_pep_hio
    """,
    """
    CREATE INDEX ix_parties_us ON parties (case_id) WHERE is_us_person
    """,
    """
    CREATE INDEX ix_parties_name ON parties USING gin (
        (lower(legal_name)) gin_trgm_ops
    )
    """,
    """
    CREATE TABLE checklist_items (
        case_id text NOT NULL,
        requirement_id text NOT NULL,
        status text NOT NULL,
        updated_at timestamptz NOT NULL,
        updated_by text NOT NULL,
        CONSTRAINT pk_checklist_items PRIMARY KEY (case_id, requirement_id),
        CONSTRAINT ck_checklist_items_status CHECK (
            status IN ('MISSING', 'REQUESTED', 'RECEIVED', 'VERIFIED', 'REJECTED')
        ),
        CONSTRAINT fk_checklist_items_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT,
        CONSTRAINT fk_checklist_items_updated_by
            FOREIGN KEY (updated_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_checklist_case_status ON checklist_items (case_id, status)
    """,
    """
    CREATE TABLE custom_requirements (
        id text NOT NULL,
        case_id text NOT NULL,
        name text NOT NULL,
        position integer NOT NULL,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL,
        CONSTRAINT pk_custom_requirements PRIMARY KEY (id),
        CONSTRAINT uq_custom_requirements_case_position UNIQUE (case_id, position),
        CONSTRAINT ck_custom_requirements_name CHECK (
            length(btrim(name)) BETWEEN 1 AND 200
        ),
        CONSTRAINT fk_custom_requirements_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT,
        CONSTRAINT fk_custom_requirements_created_by
            FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE upload_slots (
        id text NOT NULL,
        case_id text NOT NULL,
        batch_id text NOT NULL,
        requirement_id text,
        file_name text NOT NULL,
        declared_size bigint NOT NULL,
        declared_mime text NOT NULL,
        object_key text NOT NULL,
        status text NOT NULL DEFAULT 'PENDING',
        created_by text NOT NULL,
        created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        completed_at timestamptz,
        reject_reason text,
        CONSTRAINT pk_upload_slots PRIMARY KEY (id),
        CONSTRAINT uq_upload_slots_object_key UNIQUE (object_key),
        CONSTRAINT ck_upload_slots_file_name CHECK (
            length(file_name) BETWEEN 1 AND 255
        ),
        CONSTRAINT ck_upload_slots_declared_size CHECK (
            declared_size BETWEEN 1 AND 25000000
        ),
        CONSTRAINT ck_upload_slots_status CHECK (
            status IN ('PENDING', 'COMPLETED', 'EXPIRED', 'REJECTED')
        ),
        CONSTRAINT fk_upload_slots_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT,
        CONSTRAINT fk_upload_slots_created_by
            FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_upload_slots_case_batch ON upload_slots (case_id, batch_id)
    """,
    """
    CREATE INDEX ix_upload_slots_pending ON upload_slots (expires_at)
    WHERE status = 'PENDING'
    """,
    """
    CREATE TABLE case_documents (
        id text NOT NULL,
        case_id text NOT NULL,
        requirement_id text,
        file_name text NOT NULL,
        size_bytes bigint NOT NULL,
        mime_type text NOT NULL,
        uploaded_by text NOT NULL,
        uploaded_at timestamptz NOT NULL,
        extraction text NOT NULL DEFAULT 'NONE',
        storage_state text NOT NULL,
        scan_status text NOT NULL,
        object_key text,
        sha256 char(64),
        detected_mime text,
        upload_slot_id text,
        scanned_at timestamptz,
        scanned_by text,
        CONSTRAINT pk_case_documents PRIMARY KEY (id),
        CONSTRAINT uq_case_documents_object_key UNIQUE (object_key),
        CONSTRAINT uq_case_documents_upload_slot_id UNIQUE (upload_slot_id),
        CONSTRAINT ck_case_documents_size_bytes CHECK (size_bytes >= 0),
        CONSTRAINT ck_case_documents_extraction CHECK (
            extraction IN ('NONE', 'PROCESSING', 'EXTRACTED', 'FAILED')
        ),
        CONSTRAINT ck_case_documents_storage_state CHECK (
            storage_state IN (
                'QUARANTINED', 'ACCEPTED', 'REJECTED', 'METADATA_ONLY'
            )
        ),
        CONSTRAINT ck_case_documents_scan_status CHECK (
            scan_status IN (
                'PENDING', 'CLEAN', 'INFECTED', 'ERROR', 'NOT_APPLICABLE'
            )
        ),
        CONSTRAINT ck_case_documents_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_case_documents_metadata_only_key CHECK (
            (storage_state = 'METADATA_ONLY') = (object_key IS NULL)
        ),
        CONSTRAINT ck_case_documents_sha256_required CHECK (
            storage_state = 'METADATA_ONLY' OR sha256 IS NOT NULL
        ),
        CONSTRAINT ck_case_documents_accepted_clean CHECK (
            storage_state <> 'ACCEPTED' OR scan_status = 'CLEAN'
        ),
        CONSTRAINT fk_case_documents_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT,
        CONSTRAINT fk_case_documents_uploaded_by
            FOREIGN KEY (uploaded_by) REFERENCES users (id) ON DELETE RESTRICT,
        CONSTRAINT fk_case_documents_upload_slot_id
            FOREIGN KEY (upload_slot_id) REFERENCES upload_slots (id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_documents_case_requirement
    ON case_documents (case_id, requirement_id)
    """,
    """
    CREATE INDEX ix_documents_unassigned ON case_documents (case_id)
    WHERE requirement_id IS NULL
    """,
    """
    CREATE INDEX ix_documents_extraction ON case_documents (extraction)
    WHERE extraction = 'PROCESSING'
    """,
    """
    CREATE INDEX ix_documents_uploaded_at ON case_documents (uploaded_at DESC)
    """,
    """
    CREATE TABLE document_extractions (
        id text NOT NULL,
        document_id text NOT NULL,
        status text NOT NULL,
        adapter text NOT NULL,
        model text,
        page_count integer,
        error_code text,
        started_at timestamptz NOT NULL,
        finished_at timestamptz,
        CONSTRAINT pk_document_extractions PRIMARY KEY (id),
        CONSTRAINT ck_document_extractions_status CHECK (
            status IN ('PROCESSING', 'EXTRACTED', 'FAILED')
        ),
        CONSTRAINT fk_document_extractions_document_id
            FOREIGN KEY (document_id) REFERENCES case_documents (id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_extractions_document
    ON document_extractions (document_id, started_at DESC)
    """,
    """
    CREATE TABLE extraction_pages (
        extraction_id text NOT NULL,
        page_no integer NOT NULL,
        text text NOT NULL,
        CONSTRAINT pk_extraction_pages PRIMARY KEY (extraction_id, page_no),
        CONSTRAINT ck_extraction_pages_page_no CHECK (page_no >= 1),
        CONSTRAINT fk_extraction_pages_extraction_id
            FOREIGN KEY (extraction_id) REFERENCES document_extractions (id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE extraction_fields (
        id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
        extraction_id text NOT NULL,
        group_key text NOT NULL,
        field_key text NOT NULL,
        value text NOT NULL,
        confidence numeric(4, 3) NOT NULL,
        page_no integer,
        citation text,
        bbox jsonb,
        CONSTRAINT pk_extraction_fields PRIMARY KEY (id),
        CONSTRAINT ck_extraction_fields_confidence CHECK (
            confidence BETWEEN 0 AND 1
        ),
        CONSTRAINT fk_extraction_fields_extraction_id
            FOREIGN KEY (extraction_id) REFERENCES document_extractions (id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX ix_extraction_fields_group
    ON extraction_fields (extraction_id, group_key)
    """,
    """
    CREATE TABLE review_tasks (
        id text NOT NULL,
        case_id text NOT NULL,
        title text NOT NULL,
        party_id text,
        requirement_id text,
        done boolean NOT NULL DEFAULT false,
        source text NOT NULL,
        created_by text NOT NULL,
        created_at timestamptz NOT NULL,
        done_by text,
        done_at timestamptz,
        CONSTRAINT pk_review_tasks PRIMARY KEY (id),
        CONSTRAINT ck_review_tasks_title CHECK (
            length(btrim(title)) BETWEEN 1 AND 500
        ),
        CONSTRAINT ck_review_tasks_source CHECK (
            source IN ('COMPLIANCE', 'AI', 'MANUAL')
        ),
        CONSTRAINT fk_review_tasks_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT,
        CONSTRAINT fk_review_tasks_party
            FOREIGN KEY (case_id, party_id) REFERENCES parties (case_id, id)
            ON DELETE SET NULL (party_id),
        CONSTRAINT fk_review_tasks_created_by
            FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
        CONSTRAINT fk_review_tasks_done_by
            FOREIGN KEY (done_by) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX ix_tasks_case ON review_tasks (case_id, created_at)",
    """
    CREATE INDEX ix_tasks_open ON review_tasks (created_at) WHERE NOT done
    """,
    """
    CREATE TABLE audit_events (
        seq bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
        id text NOT NULL,
        scope text NOT NULL,
        case_id text,
        actor_id text NOT NULL,
        action text NOT NULL,
        summary text NOT NULL,
        at timestamptz NOT NULL,
        version integer NOT NULL,
        changes jsonb,
        ai_model text,
        ai_accepted boolean,
        ai_rule_version text,
        rule_version text,
        before_value jsonb,
        after_value jsonb,
        correlation_id text NOT NULL,
        request_id text NOT NULL,
        CONSTRAINT pk_audit_events PRIMARY KEY (id),
        CONSTRAINT uq_audit_events_seq UNIQUE (seq),
        CONSTRAINT ck_audit_events_scope CHECK (scope IN ('CASE', 'RULE_LIBRARY')),
        CONSTRAINT ck_audit_events_action CHECK (
            action IN (
                'CASE_CREATED', 'OWNERSHIP_UPDATED', 'PROFILE_UPDATED',
                'DOCUMENT_UPLOADED', 'CHECKLIST_UPDATED', 'REQUIREMENT_ADDED',
                'STATUS_CHANGED', 'COMPLIANCE_DECISION', 'AI_SUGGESTION',
                'TASK_UPDATED', 'RULE_LIBRARY'
            )
        ),
        CONSTRAINT ck_audit_events_summary CHECK (
            length(summary) BETWEEN 1 AND 500
        ),
        CONSTRAINT ck_audit_events_version CHECK (version >= 1),
        CONSTRAINT ck_audit_events_case_scope CHECK (
            (scope = 'CASE') = (case_id IS NOT NULL)
        ),
        CONSTRAINT ck_audit_events_rule_library_action CHECK (
            (scope = 'RULE_LIBRARY') = (action = 'RULE_LIBRARY')
        ),
        CONSTRAINT ck_audit_events_ai_trio CHECK (
            (ai_model IS NULL) = (ai_accepted IS NULL)
            AND (ai_model IS NULL) = (ai_rule_version IS NULL)
        ),
        CONSTRAINT ck_audit_events_ai_action CHECK (
            ai_model IS NULL OR action = 'AI_SUGGESTION'
        ),
        CONSTRAINT fk_audit_events_case_id
            FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE RESTRICT,
        CONSTRAINT fk_audit_events_actor_id
            FOREIGN KEY (actor_id) REFERENCES users (id) ON DELETE RESTRICT
    )
    """,
    """
    COMMENT ON TABLE audit_events IS
    '只追加。禁止 UPDATE、DELETE、TRUNCATE。迁移必须创建语句级触发器 audit_events_append_only。'
    """,
    """
    CREATE INDEX ix_audit_case_seq ON audit_events (case_id, seq DESC)
    """,
    """
    CREATE INDEX ix_audit_actor_seq ON audit_events (actor_id, seq DESC)
    """,
    """
    CREATE INDEX ix_audit_action_seq ON audit_events (action, seq DESC)
    """,
    "CREATE INDEX ix_audit_seq ON audit_events (seq DESC)",
    """
    CREATE TRIGGER audit_events_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
    FOR EACH STATEMENT
    EXECUTE FUNCTION audit_events_append_only()
    """,
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fcc_app') THEN
            REVOKE ALL ON TABLE audit_events FROM fcc_app;
            GRANT SELECT, INSERT ON TABLE audit_events TO fcc_app;
        END IF;
    END
    $$
    """,
)

_DOWNGRADE: tuple[str, ...] = (
    "DROP TABLE IF EXISTS audit_events",
    "DROP TABLE IF EXISTS review_tasks",
    "DROP TABLE IF EXISTS extraction_fields",
    "DROP TABLE IF EXISTS extraction_pages",
    "DROP TABLE IF EXISTS document_extractions",
    "DROP TABLE IF EXISTS case_documents",
    "DROP TABLE IF EXISTS upload_slots",
    "DROP TABLE IF EXISTS custom_requirements",
    "DROP TABLE IF EXISTS checklist_items",
    "DROP TABLE IF EXISTS parties",
    "DROP TABLE IF EXISTS cases",
    "DROP TABLE IF EXISTS case_reference_counters",
    "DROP TABLE IF EXISTS rule_drafts",
    "DROP TABLE IF EXISTS rule_library_state",
    "DROP TABLE IF EXISTS rule_versions",
    "DROP TABLE IF EXISTS users",
    "DROP FUNCTION IF EXISTS audit_events_append_only()",
    "DROP FUNCTION IF EXISTS rule_versions_immutable()",
    "DROP EXTENSION IF EXISTS pg_trgm",
)
