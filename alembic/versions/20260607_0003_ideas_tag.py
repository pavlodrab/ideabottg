"""ideas.tag column

Revision ID: 0003_ideas_tag
Revises: 0002_chats_last_prompt
Create Date: 2026-06-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_ideas_tag"
down_revision: Union[str, None] = "0002_chats_last_prompt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ideas",
        sa.Column(
            "tag",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'other'"),
        ),
    )
    op.create_index("ix_ideas_tag", "ideas", ["tag"])


def downgrade() -> None:
    op.drop_index("ix_ideas_tag", table_name="ideas")
    op.drop_column("ideas", "tag")
