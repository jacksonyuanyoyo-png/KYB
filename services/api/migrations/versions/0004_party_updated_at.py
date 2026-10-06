"""股权节点增加 updated_at，供筛查结果与节点修改时间比较。

Revision ID: 0004_party_updated_at
Revises: 0003_launch
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_party_updated_at"
down_revision = "0003_launch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parties",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_column("parties", "updated_at")
