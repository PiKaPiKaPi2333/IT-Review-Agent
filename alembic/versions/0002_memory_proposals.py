"""memory_proposals：待确认的长期记忆候选。

与 app/infra/models.py 中的 MemoryProposalRecord 一一对应。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_memory_proposals"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("answer_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("audit_ctx_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("audit_contexts.id")),
        sa.Column("fact_type", sa.String(32)),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("importance", sa.Float, server_default="0.5"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_memory_proposals_answer_id", "memory_proposals", ["answer_id"])
    op.create_index("ix_memory_proposals_user_id", "memory_proposals", ["user_id"])


def downgrade() -> None:
    op.drop_table("memory_proposals")
