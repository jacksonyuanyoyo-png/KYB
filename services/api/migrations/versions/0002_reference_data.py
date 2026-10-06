"""插入内置规则版本和规则库单行。演示用户与案件不在迁移里。

Revision ID: 0002_reference_data
Revises: 0001_initial
Create Date: 2026-10-06
"""

from __future__ import annotations

from alembic import op

revision = "0002_reference_data"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_VERSION = "demo-2026-10-04"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO rule_versions (
            version, kind, builtin_version, extras, disabled, overrides,
            published_by, published_at, parity_report
        ) VALUES (
            '{_VERSION}', 'BUILTIN', '{_VERSION}',
            '[]'::jsonb, '{{}}'::text[], '{{}}'::jsonb,
            NULL, now(), NULL
        )
        """
    )
    op.execute(
        f"""
        INSERT INTO rule_library_state (
            id, published_version, version, updated_by, updated_at
        ) VALUES (
            1, '{_VERSION}', 1, NULL, now()
        )
        """
    )


def downgrade() -> None:
    # 触发器禁止删除 rule_versions。回滚时短暂关掉它，事务结束前再打开。
    op.execute("DELETE FROM rule_library_state WHERE id = 1")
    op.execute("ALTER TABLE rule_versions DISABLE TRIGGER rule_versions_immutable")
    op.execute(f"DELETE FROM rule_versions WHERE version = '{_VERSION}'")
    op.execute("ALTER TABLE rule_versions ENABLE TRIGGER rule_versions_immutable")
