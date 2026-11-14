"""conversations.clarify_streak：记录会话内连续澄清反问轮数。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_conversation_clarify_streak"
down_revision: str | None = "0003_document_ingest_options"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("clarify_streak", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("conversations", "clarify_streak")
