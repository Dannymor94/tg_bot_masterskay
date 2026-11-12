"""Separate access revocation from Telegram delivery blocks."""

import sqlalchemy as sa

from alembic import op

revision = "c201a"
down_revision = "499d79fa6668"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("access_revoked", sa.Boolean(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("users", "access_revoked")
