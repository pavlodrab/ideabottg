"""admin delivery mode + digest schedule

Revision ID: 0004_admins_delivery
Revises: 0003_ideas_tag
Create Date: 2026-06-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_admins_delivery"
down_revision: Union[str, None] = "0003_ideas_tag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "admins",
        sa.Column(
            "delivery_mode",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'stream'"),
        ),
    )
    op.add_column(
        "admins",
        sa.Column(
            "digest_cron",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'0 9 * * 1'"),
        ),
    )
    op.add_column(
        "admins",
        sa.Column(
            "last_digest_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("admins", "last_digest_at")
    op.drop_column("admins", "digest_cron")
    op.drop_column("admins", "delivery_mode")
