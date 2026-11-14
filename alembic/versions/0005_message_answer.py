"""messages.answer：保存助手消息的完整回答，供前端还原历史对话。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_message_answer"
down_revision: str | None = "0004_conversation_clarify_streak"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("messages", sa.Column("answer", JSONType, nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "answer")
