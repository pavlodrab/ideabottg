"""ideas: store origin chat/message so admin replies can target it

Revision ID: 0006_ideas_origin
Revises: 0005_voting
Create Date: 2026-06-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_ideas_origin"
down_revision: Union[str, None] = "0005_voting"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Both nullable: existing ideas were submitted before we tracked
    # this, so admin replies for them still fall back to a plain DM
    # (the previous behaviour).
    op.add_column(
        "ideas",
        sa.Column("from_chat_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "ideas",
        sa.Column("from_message_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ideas", "from_message_id")
    op.drop_column("ideas", "from_chat_id")
