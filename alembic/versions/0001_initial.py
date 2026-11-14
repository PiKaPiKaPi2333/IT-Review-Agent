# 初始表结构：会话/消息/长期记忆/文档/评估。
# 手写而不依赖 autogenerate，保证离线可复现；与 app/infra/models.py 一一对应。

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_contexts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_name", sa.String(255)),
        sa.Column("audit_year", sa.Integer),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("audit_ctx_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("audit_contexts.id")),
        sa.Column("title", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id")),
        sa.Column("role", sa.String(16)),
        sa.Column("content", sa.Text),
        sa.Column("citations", postgresql.JSONB),
        sa.Column("verification", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    op.create_table(
        "memory_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("audit_ctx_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("audit_contexts.id")),
        sa.Column("fact_type", sa.String(32)),
        sa.Column("content", sa.Text),
        sa.Column("importance", sa.Float, server_default="0.5"),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_memory_facts_user_id", "memory_facts", ["user_id"])
    op.create_index("ix_memory_facts_audit_ctx_id", "memory_facts", ["audit_ctx_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("filename", sa.String(512)),
        sa.Column("status", sa.String(32)),
        sa.Column("parse_meta", postgresql.JSONB),
        sa.Column("checksum", sa.String(128)),
        sa.Column("source_path", sa.String(1024)),
        sa.Column("audit_ctx_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("audit_contexts.id")),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_documents_checksum", "documents", ["checksum"])
    op.create_index("ix_documents_audit_ctx_id", "documents", ["audit_ctx_id"])

    op.create_table(
        "eval_datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255)),
        sa.Column("items", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("eval_datasets.id")),
        sa.Column("ragas_report", postgresql.JSONB),
        sa.Column("langsmith_url", sa.String(1024)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "answer_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("answer_id", sa.String(64)),
        sa.Column("user_id", sa.String(128)),
        sa.Column("rating", sa.Integer),
        sa.Column("comment", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_answer_feedback_answer_id", "answer_feedback", ["answer_id"])


def downgrade() -> None:
    for table in (
        "answer_feedback",
        "eval_runs",
        "eval_datasets",
        "documents",
        "memory_facts",
        "messages",
        "conversations",
        "audit_contexts",
    ):
        op.drop_table(table)
