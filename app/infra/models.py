"""SQLAlchemy ORM —— 对应架构 §6 的数据模型。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


class AuditContext(Base):
    __tablename__ = "audit_contexts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_name: Mapped[str | None] = mapped_column(String(255))
    audit_year: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    audit_ctx_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_contexts.id")
    )
    title: Mapped[str | None] = mapped_column(String(255))
    # 连续澄清反问轮数：非澄清回答会清零
    clarify_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list | None] = mapped_column(JSONType)
    verification: Mapped[dict | None] = mapped_column(JSONType)
    # 助手消息的完整回答（status / confidence / caveats / follow_up_questions 等），用于还原历史
    answer: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryFact(Base):
    __tablename__ = "memory_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    audit_ctx_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_contexts.id"), index=True
    )
    fact_type: Mapped[str] = mapped_column(String(32))  # preference / terminology / clarified
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryProposalRecord(Base):
    """待用户确认的长期记忆候选：pending → accepted / rejected，只能处理一次。"""

    __tablename__ = "memory_proposals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    answer_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    audit_ctx_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_contexts.id")
    )
    fact_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # document_id
    filename: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    parse_meta: Mapped[dict | None] = mapped_column(JSONType)
    # 入库参数（doc_type / audit_year / department）：重试与重建时复用，保证 chunk 元数据一致
    ingest_options: Mapped[dict | None] = mapped_column(JSONType)
    checksum: Mapped[str | None] = mapped_column(String(128), index=True)
    # 原始文件落盘路径：失败重试时直接复用，无需重新上传
    source_path: Mapped[str | None] = mapped_column(String(1024))
    audit_ctx_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_contexts.id"), index=True
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EvalDataset(Base):
    __tablename__ = "eval_datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    items: Mapped[list | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("eval_datasets.id"))
    ragas_report: Mapped[dict | None] = mapped_column(JSONType)
    langsmith_url: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnswerFeedback(Base):
    """在线反馈（架构 §8.1）：👍/👎 回传；👎 的坏例经复核后导出到评估集。

    status: pending（待复核）/ confirmed（确认为坏例，需有 ground_truth）/ rejected（不是问题）
    """

    __tablename__ = "answer_feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    answer_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(128))
    rating: Mapped[int] = mapped_column(Integer)  # 1 / -1
    comment: Mapped[str | None] = mapped_column(Text)
    # 关联信息：提交时从会话中回填，便于复核时直接看到问题
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    question: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(String(32))  # factual_error / citation_mismatch / incomplete / other
    # 复核
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    ground_truth: Mapped[str | None] = mapped_column(Text)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
