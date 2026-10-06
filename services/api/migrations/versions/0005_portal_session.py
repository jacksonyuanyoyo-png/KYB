"""门户邀请增加会话哈希和过期时间。

Revision ID: 0005_portal_session
Revises: 0004_party_updated_at
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_portal_session"
down_revision = "0004_party_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portal_invites", sa.Column("session_token_hash", sa.CHAR(64), nullable=True))
    op.add_column("portal_invites", sa.Column("session_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("uq_portal_invites_session_token_hash", "portal_invites", ["session_token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_portal_invites_session_token_hash", table_name="portal_invites")
    op.drop_column("portal_invites", "session_expires_at")
    op.drop_column("portal_invites", "session_token_hash")
