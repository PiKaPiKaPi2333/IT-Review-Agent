"""问题拆解 Agent：指代消解 → 子问题拆解 → 分类 → 检索查询改写（一次结构化调用）。"""

from __future__ import annotations

import logging

from app.graph.nodes.common import truncate
from app.graph.state import AgentState
from app.infra.tracing import traceable
from app.llm.client import build_chat_model, structured_llm_call
from app.llm.prompts.classifier import CLASSIFIER_SYSTEM, CLASSIFIER_USER
from app.schemas.models import ClassificationResult, QuestionType

logger = logging.getLogger(__name__)


def build_decompose_graph(*, decompose=None):
    """编译问题拆解子图（单步）：decompose → END。"""
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(AgentState)
    g.add_node("decompose", decompose or decomposition_agent)
    g.add_edge(START, "decompose")
    g.add_edge("decompose", END)
    return g.compile()


@traceable(run_type="chain", name="agent.decompose")
def decomposition_agent(state: AgentState) -> dict:
    prompt = CLASSIFIER_USER.format(
        audit_context=truncate(state.audit_context or "（无）", 1500),
        long_term_memory=truncate(state.long_term_memory or "（暂无）", 1500),
        conversation_history=state.conversation_history or "（无）",
        question=state.question,
    )
    result = structured_llm_call(
        build_chat_model("classifier"),
        ClassificationResult,
        system=CLASSIFIER_SYSTEM,
        user=prompt,
    )
    logger.info(
        "问题拆解 type=%s confidence=%.2f rewritten=%s sub_questions=%s queries=%s",
        result.question_type.value,
        result.confidence,
        result.rewritten_question,
        result.sub_questions,
        result.search_queries,
    )

    update: dict = {"classification": result}
    # OUT_OF_SCOPE 不需要检索查询，避免下游误用
    if result.question_type is QuestionType.OUT_OF_SCOPE:
        update["sufficiency"] = None
    return update
