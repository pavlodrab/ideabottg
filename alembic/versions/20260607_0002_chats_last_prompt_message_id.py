"""chats.last_prompt_message_id

Revision ID: 0002_chats_last_prompt
Revises: 0001_init
Create Date: 2026-06-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_chats_last_prompt"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("last_prompt_message_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chats", "last_prompt_message_id")
