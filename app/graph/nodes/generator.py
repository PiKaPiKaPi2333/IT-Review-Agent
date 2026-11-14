"""带引用生成节点（架构 §3.4.4）：只负责生成，引用核验由验证 Agent 负责。"""

from __future__ import annotations

import logging

from app.graph.nodes.common import format_chunks, truncate
from app.graph.state import AgentState
from app.infra.tracing import traceable
from app.llm.client import LLMOutputError, build_chat_model, structured_llm_call
from app.llm.prompts.generator import (
    GENERATOR_SYSTEM,
    GENERATOR_USER,
    REGENERATE_SYSTEM,
    REGENERATE_USER,
)
from app.schemas.models import AnswerWithCitations, SufficiencyResult

logger = logging.getLogger(__name__)


@traceable(run_type="chain", name="node.generate")
def generate_node(state: AgentState) -> dict:
    cls = state.classification
    prompt = GENERATOR_USER.format(
        audit_context=truncate(state.audit_context or "（无）", 1200),
        long_term_memory=truncate(state.long_term_memory or "（暂无）", 1200),
        conversation_history=state.conversation_history or "（无）",
        question=cls.rewritten_question if cls else state.question,
        chunks=format_chunks(state.retrieved_chunks),
    )
    try:
        answer = structured_llm_call(
            build_chat_model("generator"),
            AnswerWithCitations,
            system=GENERATOR_SYSTEM,
            user=prompt,
        )
    except LLMOutputError as e:
        logger.error("生成失败（结构化输出不合规）：%s", e)
        return {"error": f"生成失败：{e}", "draft_answer": None}

    return {"draft_answer": answer, "verification": None, "error": None}


@traceable(run_type="chain", name="node.regenerate")
def regenerate_node(state: AgentState) -> dict:
    if state.draft_answer is None or state.verification is None:
        return {"error": "缺少可修正的草稿或验证结论"}

    cls = state.classification
    failed = [
        f"[{c.citation_index}] {c.verdict}：{c.reason}"
        for c in state.verification.checks
        if c.verdict != "supported"
    ]
    prompt = REGENERATE_USER.format(
        audit_context=truncate(state.audit_context or "（无）", 1200),
        long_term_memory=truncate(state.long_term_memory or "（暂无）", 1200),
        conversation_history=state.conversation_history or "（无）",
        question=cls.rewritten_question if cls else state.question,
        chunks=format_chunks(state.retrieved_chunks),
        previous_answer=state.draft_answer.answer_markdown,
        fix_instruction=state.verification.fix_instruction or "（未提供具体指令）",
        failed_checks="\n".join(failed) or "（无逐条结论，问题在 quote 与原文不一致）",
    )
    try:
        answer = structured_llm_call(
            build_chat_model("generator"),
            AnswerWithCitations,
            system=REGENERATE_SYSTEM,
            user=prompt,
        )
    except LLMOutputError as e:
        logger.error("修正生成失败：%s", e)
        return {
            "error": f"修正生成失败：{e}",
            "regeneration_count": state.regeneration_count + 1,
        }

    return {
        "draft_answer": answer,
        "verification": None,
        "regeneration_count": state.regeneration_count + 1,
        "error": None,
    }


def insufficient_result(state: AgentState) -> SufficiencyResult:
    return state.sufficiency or SufficiencyResult(is_sufficient=False, reason="证据不足")
