"""图的路由函数（纯逻辑，不依赖 LLM / DB / Milvus）。

单独成模块的原因：路由是流程正确性的核心，必须能在没有任何外部
依赖的情况下单测；builder 只负责把节点和路由拼起来。
"""

from __future__ import annotations

from app.graph.state import AgentState
from app.infra.config import get_settings
from app.schemas.models import QuestionType


def route_by_type(state: AgentState) -> str:
    c = state.classification
    if c is None:
        return "out_of_scope"
    match c.question_type:
        case QuestionType.OUT_OF_SCOPE:
            return "out_of_scope"
        case QuestionType.CLARIFY:
            # 连续反问达到上限：不再打扰用户，按现有信息直接作答（回答中会注明证据局限）
            if state.clarify_streak >= get_settings().max_clarify_streak:
                return "in_scope"
            return "clarify"
        case _:
            return "in_scope"


def route_by_sufficiency(state: AgentState) -> str:
    """检索 Agent 内部已完成补查循环，这里只读最终结论。"""
    suf = state.sufficiency
    if suf is not None and suf.is_sufficient:
        return "sufficient"
    return "insufficient"


def route_by_verification(state: AgentState) -> str:
    s = get_settings()
    v = state.verification
    if v is None:
        return "unfixable"
    if v.overall == "pass":
        return "pass"
    if v.overall == "fixable" and state.regeneration_count < s.max_regeneration:
        return "fixable"
    return "unfixable"


def route_after_answer(state: AgentState) -> str:
    """生成 / 修正之后的去向：无草稿（结构化输出失败）直接收尾，否则交给验证 Agent。"""
    if state.draft_answer is None:
        return "give_up"
    return "verify"
