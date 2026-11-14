"""API 请求 / 响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.models import FinalAnswer


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    user_id: str = "anonymous"
    thread_id: str | None = None
    conversation_id: str | None = None
    audit_ctx_id: str | None = None
    audit_context_text: str = Field(default="", description="审计项目上下文（单位/年度/政策清单）")


class AskResponse(FinalAnswer):
    pass


FeedbackReason = Literal["factual_error", "citation_mismatch", "incomplete", "other"]


class FeedbackRequest(BaseModel):
    answer_id: str
    user_id: str = "anonymous"
    rating: int = Field(ge=-1, le=1)
    reason: FeedbackReason | None = Field(default=None, description="👎 时的原因")
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackReviewRequest(BaseModel):
    status: Literal["pending", "confirmed", "rejected"]
    ground_truth: str | None = Field(default=None, max_length=4000, description="标准答案，确认为坏例时必填")
    review_note: str | None = Field(default=None, max_length=2000)
    reviewed_by: str = "reviewer"


class FeedbackItem(BaseModel):
    id: str
    answer_id: str
    user_id: str
    rating: int
    reason: str | None
    comment: str | None
    status: str
    question: str | None
    answer_markdown: str | None
    citations: list | None
    conversation_id: str | None
    ground_truth: str | None
    review_note: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime


class MemoryDecisionRequest(BaseModel):
    user_id: str = "anonymous"
    accept: bool = Field(description="true=记住这条长期记忆，false=不记住")


class MemoryDecisionResponse(BaseModel):
    id: str
    status: str


class IngestResponse(BaseModel):
    document_id: str
    status: str
    chunks: int = 0
    pages: int | None = None
    tables: int | None = None
    error: str | None = None


class DocumentInfo(BaseModel):
    id: str
    filename: str | None
    status: str
    parse_meta: dict | None = None
    error: str | None = None
