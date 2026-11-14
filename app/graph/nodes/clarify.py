"""澄清反问：答案构造 + 会话连续澄清轮数的读写。

澄清不再是独立节点：decompose 判定为澄清型后直接进入 finalize，由 finalize 调用 build_clarify_answer。
"""

from __future__ import annotations

import logging
import uuid

from app.graph.state import AgentState
from app.infra.db import session_scope
from app.infra.models import Conversation
from app.schemas.models import FinalAnswer

logger = logging.getLogger(__name__)


def build_clarify_answer(state: AgentState, answer_id: str | None = None) -> FinalAnswer:
    c = state.classification
    question = (c.clarification_question if c else None) or (
        "您指的是哪个具体对象？例如第几条、哪份文件，可以再说明一下吗？"
    )
    follow_ups = _suggest_follow_ups(c)

    body = (
        "为了准确检索知识库，还需要你补充一点信息：\n\n"
        f"> {question}\n\n"
        "补充后我会重新检索并给出带引用的回答。"
    )
    return FinalAnswer(
        answer_id=answer_id or uuid.uuid4().hex,
        status="needs_clarification",
        answer_markdown=body,
        citations=[],
        confidence="low",
        follow_up_questions=follow_ups,
    )


def _suggest_follow_ups(c) -> list[str]:
    """把模型给出的检索查询反推成可选追问方向。"""
    if c is None:
        return []
    qs = [q for q in (c.search_queries or []) if q.strip()]
    if not qs:
        return []
    rq = c.rewritten_question
    return [f"你是指「{rq}」（检索方向：{qs[0]}）吗？"] if rq else []


async def load_clarify_streak(conversation_id) -> int:
    """读取会话当前的连续澄清轮数（无会话则为 0）。"""
    if not conversation_id:
        return 0
    async with session_scope() as db:
        conv = await db.get(Conversation, uuid.UUID(str(conversation_id)))
        return int(conv.clarify_streak or 0) if conv is not None else 0


async def update_clarify_streak(conversation_id, *, clarified: bool) -> None:
    """本轮若是澄清反问则 +1，否则清零。"""
    if not conversation_id:
        return
    async with session_scope() as db:
        conv = await db.get(Conversation, uuid.UUID(str(conversation_id)))
        if conv is not None:
            conv.clarify_streak = (conv.clarify_streak or 0) + 1 if clarified else 0
