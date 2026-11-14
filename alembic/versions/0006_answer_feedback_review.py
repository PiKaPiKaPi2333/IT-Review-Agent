"""answer_feedback：关联问题与会话，增加原因、复核状态与标准答案。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_answer_feedback_review"
down_revision: str | None = "0005_message_answer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("answer_feedback", sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("answer_feedback", sa.Column("question", sa.Text(), nullable=True))
    op.add_column("answer_feedback", sa.Column("reason", sa.String(32), nullable=True))
    op.add_column(
        "answer_feedback",
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column("answer_feedback", sa.Column("ground_truth", sa.Text(), nullable=True))
    op.add_column("answer_feedback", sa.Column("review_note", sa.Text(), nullable=True))
    op.add_column("answer_feedback", sa.Column("reviewed_by", sa.String(128), nullable=True))
    op.add_column("answer_feedback", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_answer_feedback_conversation_id", "answer_feedback", ["conversation_id"])
    op.create_index("ix_answer_feedback_status", "answer_feedback", ["status"])


def downgrade() -> None:
    op.drop_index("ix_answer_feedback_status", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_conversation_id", table_name="answer_feedback")
    for col in ("reviewed_at", "reviewed_by", "review_note", "ground_truth", "status", "reason", "question", "conversation_id"):
        op.drop_column("answer_feedback", col)
