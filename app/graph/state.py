"""LangGraph 全局状态（架构 §3.2）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.models import (
    AnswerWithCitations,
    ClassificationResult,
    FinalAnswer,
    RetrievedChunk,
    SufficiencyResult,
    VerificationResult,
)


class AgentState(BaseModel):
    # —— 会话标识 ——
    thread_id: str = ""
    user_id: str = "anonymous"
    conversation_id: str | None = None
    audit_ctx_id: str | None = None

    # —— 输入 ——
    question: str = ""
    attachments: list[str] = Field(default_factory=list)

    # —— 记忆注入（每轮开始时装载，不落库） ——
    audit_context: str = ""
    long_term_memory: str = ""
    conversation_history: str = ""
    # 同一会话内连续澄清反问轮数（由 runner 从会话表装载）
    clarify_streak: int = 0

    # —— 分类阶段 ——
    classification: ClassificationResult | None = None

    # —— 检索阶段 ——
    retrieval_round: int = 0
    used_queries: list[str] = Field(default_factory=list)
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)
    sufficiency: SufficiencyResult | None = None

    # —— 生成阶段 ——
    draft_answer: AnswerWithCitations | None = None
    regeneration_count: int = 0

    # —— 验证阶段 ——
    verification: VerificationResult | None = None

    # —— 输出 ——
    final_answer: FinalAnswer | None = None
    error: str | None = None


class GraphConfig(BaseModel):
    """图级运行参数（可被 API 覆盖，用于评估时调参）。"""

    max_retrieval_rounds: int = 2
    max_regeneration: int = 2
    top_k: int = 8
