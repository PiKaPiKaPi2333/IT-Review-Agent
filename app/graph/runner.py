"""问答执行入口：装载记忆 → 跑图 → 返回 FinalAnswer。

API 与 CLI 共用；评估流水线也复用这里的 run_question。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from app.graph.builder import get_graph
from app.graph.nodes.clarify import load_clarify_streak
from app.graph.state import AgentState
from app.infra.tracing import traceable
from app.memory.history import build_conversation_history
from app.memory.long_term import load_memory_digest
from app.schemas.models import FinalAnswer

logger = logging.getLogger(__name__)


@traceable(run_type="chain", name="run_question")
async def run_question(
    *,
    question: str,
    user_id: str = "anonymous",
    thread_id: str | None = None,
    conversation_id: str | None = None,
    audit_ctx_id: str | None = None,
    audit_context_text: str = "",
) -> FinalAnswer:
    """执行一次完整问答。thread_id 决定短时记忆归属。"""
    thread_id = thread_id or uuid.uuid4().hex
    started = time.perf_counter()

    # ---- 装载记忆（mem0 长期记忆 + PostgreSQL 会话历史） ----
    try:
        # mem0 基于语义相似度检索相关记忆
        memory_digest = await load_memory_digest(
            user_id=user_id,
            query=question,  # 用当前问题做语义检索
            audit_ctx_id=audit_ctx_id,
            limit=10,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("长期记忆装载失败：%s", e)
        memory_digest = "（暂无）"

    try:
        # 超出预算的更早消息压缩成摘要，最近的消息保留原文
        conversation_history = await build_conversation_history(conversation_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("会话历史装载失败：%s", e)
        conversation_history = "（无）"

    try:
        clarify_streak = await load_clarify_streak(conversation_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("澄清轮数装载失败：%s", e)
        clarify_streak = 0

    state = AgentState(
        thread_id=thread_id,
        user_id=user_id,
        conversation_id=conversation_id,
        audit_ctx_id=audit_ctx_id,
        question=question,
        audit_context=audit_context_text,
        long_term_memory=memory_digest,
        conversation_history=conversation_history,
        clarify_streak=clarify_streak,
    )

    graph = get_graph()
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    # 每轮从"干净"的阶段性字段开始，但保留会话标识与记忆
    result = await graph.ainvoke(state, config=config)

    final: FinalAnswer | None = result.get("final_answer")
    elapsed = int((time.perf_counter() - started) * 1000)

    if final is None:
        logger.error("图执行完毕但没有产出 final_answer：error=%s", result.get("error"))
        final = FinalAnswer(
            answer_id=uuid.uuid4().hex,
            status="error",
            answer_markdown="系统暂时无法生成合规回答，请稍后重试。",
            confidence="low",
            caveats=[str(result.get("error") or "未知错误")],
        )

    # 长期记忆候选已由 persist 节点写入待确认表，这里不直接写 mem0

    final.latency_ms = elapsed
    final.thread_id = thread_id
    final.conversation_id = str(result.get("conversation_id") or conversation_id or "")
    return final
