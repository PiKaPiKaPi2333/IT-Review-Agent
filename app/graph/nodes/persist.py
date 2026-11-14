"""持久化节点：会话归档 + 长期记忆候选提炼（架构 §4.3）。

提炼出的记忆只生成待确认的候选（memory_proposals），不直接写入长期记忆；
用户确认后，经 /memory/proposals/{id}/decision 才写入 mem0。

单独成节点而不是塞进 finalize，是为了让 finalize 保持纯函数、可单测；
这里的任何失败都不能影响已经生成的答案，因此整体 try/except 吞掉异常。
"""

from __future__ import annotations

import logging

from app.graph.nodes.clarify import update_clarify_streak
from app.graph.state import AgentState
from app.infra.config import get_settings
from app.infra.tracing import traceable
from app.llm.client import build_chat_model, structured_llm_call
from app.llm.prompts.memory import MEMORY_EXTRACT_SYSTEM, MEMORY_EXTRACT_USER
from app.memory.long_term import archive_message, ensure_conversation
from app.memory.proposals import create_proposals
from app.schemas.models import FinalAnswer, MemoryProposal
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ExtractedFact(BaseModel):
    fact_type: str = Field(description="preference / terminology / clarified")
    content: str
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryExtraction(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)


@traceable(run_type="chain", name="node.persist")
async def persist_node(state: AgentState) -> dict:
    final = state.final_answer
    if final is None:
        return {}

    try:
        conversation_id = await ensure_conversation(
            conversation_id=state.conversation_id,
            user_id=state.user_id,
            title=state.question[:120],
            audit_ctx_id=state.audit_ctx_id,
        )
        await archive_message(conversation_id, "user", state.question)
        await archive_message(
            conversation_id,
            "assistant",
            final.answer_markdown,
            citations=[c.model_dump() for c in final.citations],
            verification=final.verification.model_dump() if final.verification else None,
            # 完整回答（不含待确认的长期记忆候选），供前端还原历史
            answer=final.model_dump(mode="json", exclude={"memory_proposals"}),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("会话归档失败：%s", e)
        conversation_id = None

    proposals: list[MemoryProposal] = []
    if conversation_id is not None and _should_extract(final):
        try:
            proposals = await _extract_facts(state, final)
        except Exception as e:  # noqa: BLE001
            logger.warning("长期记忆提炼失败：%s", e)

    if conversation_id is not None:
        try:
            await update_clarify_streak(
                conversation_id, clarified=final.status == "needs_clarification"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("澄清轮数更新失败：%s", e)

    update: dict = {
        "final_answer": final.model_copy(update={"memory_proposals": proposals}),
    }
    if conversation_id is not None:
        update["conversation_id"] = str(conversation_id)
    return update


def _should_extract(final: FinalAnswer) -> bool:
    # 只在有实质问答时提炼，避免把"证据不足"当成事实
    return final.status in ("ok", "degraded") and bool(final.answer_markdown)


async def _extract_facts(state: AgentState, final: FinalAnswer) -> list[MemoryProposal]:
    prompt = MEMORY_EXTRACT_USER.format(
        question=state.question,
        answer_summary=final.answer_markdown[:1500],
        verification_status=final.verification.overall if final.verification else "unknown",
        existing_facts=state.long_term_memory or "（暂无）",
    )
    result = structured_llm_call(
        build_chat_model("classifier"),
        MemoryExtraction,
        system=MEMORY_EXTRACT_SYSTEM,
        user=prompt,
        max_retries=1,
    )
    if not result.facts:
        return []
    # 只生成待确认候选，确认前不写入长期记忆
    return await create_proposals(
        user_id=state.user_id,
        audit_ctx_id=state.audit_ctx_id,
        answer_id=final.answer_id,
        facts=[f.model_dump() for f in result.facts],
    )
