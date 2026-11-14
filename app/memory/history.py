"""会话历史装载与压缩（短时记忆）。

超出 token 预算时：最近的消息保留原文，更早的消息滚动压缩成摘要，两者一起注入 prompt。

滚动摘要按 conversation_id 缓存在进程内：每轮只把新滑出窗口的消息并入已有摘要，
不重复压缩全部历史。缓存校验最后一条被压缩消息的 id，历史若有变化则从头重算。
进程重启后缓存为空，会在下一轮重新压缩。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import desc, select

from app.infra.config import get_settings
from app.infra.db import session_scope
from app.infra.models import Message
from app.knowledge.chunker import count_tokens
from app.llm.client import build_chat_model
from app.llm.prompts.history import HISTORY_SUMMARY_SYSTEM, HISTORY_SUMMARY_USER

logger = logging.getLogger(__name__)

# 单条消息渲染进 prompt 的字符上限，防止一条长回答独占整个预算
MAX_MESSAGE_CHARS = 800
EMPTY = "（无）"


@dataclass(frozen=True)
class HistoryMessage:
    id: str
    role: str
    content: str


@dataclass
class _RollingSummary:
    covered: int  # 已并入摘要的消息条数（从最早一条算起）
    last_id: str  # 最后一条被并入的消息 id，用于校验历史未变
    summary: str


SummarizeFn = Callable[[str, Sequence[HistoryMessage], int], Awaitable[str]]

_cache: dict[str, _RollingSummary] = {}


async def build_conversation_history(conversation_id) -> str:
    """装载会话历史并按预算压缩，返回可直接注入 prompt 的文本。"""
    s = get_settings()
    conv_uuid = _conv_uuid(conversation_id)
    if conv_uuid is None:
        return EMPTY
    messages = await _load_messages(conv_uuid, s.history_max_messages)
    return await compose_history(
        str(conv_uuid),
        messages,
        budget=s.history_budget_tokens,
        summary_ratio=s.history_summary_ratio,
    )


async def compose_history(
    conv_key: str,
    messages: Sequence[HistoryMessage],
    *,
    budget: int,
    summary_ratio: float = 0.35,
    summarize: SummarizeFn | None = None,
) -> str:
    """在 token 预算内组装历史：超出部分压缩为摘要，最近的消息保留原文。"""
    if not messages:
        return EMPTY

    lines = [_render_line(m) for m in messages]
    if sum(count_tokens(line) for line in lines) <= budget:
        return "\n".join(lines)

    summary_budget = int(budget * summary_ratio)
    split = _recent_split(lines, budget - summary_budget)
    older, recent = messages[:split], messages[split:]

    summary = await _rolling_summary(
        conv_key, older, summary_budget, summarize or _summarize
    )

    parts: list[str] = []
    if summary:
        parts.append(f"【更早对话摘要】\n{summary}")
    parts.append("【最近对话】\n" + "\n".join(_render_line(m) for m in recent))
    return "\n\n".join(parts)


def _recent_split(lines: Sequence[str], recent_budget: int) -> int:
    """返回最近消息段的起始下标；至少保留最后一条。"""
    used = 0
    split = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        cost = count_tokens(lines[i])
        if used + cost > recent_budget and split < len(lines):
            break
        used += cost
        split = i
    return min(split, len(lines) - 1)


async def _rolling_summary(
    conv_key: str,
    older: Sequence[HistoryMessage],
    max_tokens: int,
    summarize: SummarizeFn,
) -> str:
    if not older:
        return ""

    cached = _cache.get(conv_key)
    base, new_part = "", list(older)
    if (
        cached is not None
        and cached.covered <= len(older)
        and older[cached.covered - 1].id == cached.last_id
    ):
        base, new_part = cached.summary, list(older[cached.covered :])

    if not new_part:
        return base

    try:
        summary = await summarize(base, new_part, max_tokens)
    except Exception as e:  # noqa: BLE001
        # 摘要失败不能影响问答：退化为只保留最近消息，下一轮重试
        logger.warning("会话历史摘要失败，仅保留最近消息：%s", e)
        return base

    summary = _cap_tokens(summary, max_tokens)
    _cache[conv_key] = _RollingSummary(
        covered=len(older), last_id=older[-1].id, summary=summary
    )
    logger.info("会话摘要已更新 conv=%s 覆盖 %d 条消息", conv_key, len(older))
    return summary


async def _summarize(
    previous: str, messages: Sequence[HistoryMessage], max_tokens: int
) -> str:
    llm = build_chat_model("classifier")
    user = HISTORY_SUMMARY_USER.format(
        previous_summary=previous or EMPTY,
        new_turns="\n".join(_render_line(m) for m in messages),
        max_chars=max(50, max_tokens // 2),
    )
    resp = await llm.ainvoke(
        [SystemMessage(content=HISTORY_SUMMARY_SYSTEM), HumanMessage(content=user)]
    )
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    return text.strip()


def _cap_tokens(text: str, max_tokens: int) -> str:
    """摘要超出预算时按比例截断（模型偶尔不守字数约束）。"""
    n = count_tokens(text)
    if n <= max_tokens:
        return text
    return text[: int(len(text) * max_tokens / n)]


def _render_line(m: HistoryMessage) -> str:
    who = "审计方" if m.role == "user" else "助手"
    content = " ".join((m.content or "").split())
    if len(content) > MAX_MESSAGE_CHARS:
        content = content[:MAX_MESSAGE_CHARS] + "…"
    return f"{who}：{content}"


async def _load_messages(conv_uuid: uuid.UUID, limit: int) -> list[HistoryMessage]:
    """按时间正序返回会话消息，最多 limit 条（取最新的 limit 条）。"""
    async with session_scope() as db:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv_uuid)
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
        rows = list((await db.execute(stmt)).scalars().all())
    rows.reverse()
    return [
        HistoryMessage(id=str(m.id), role=m.role, content=m.content or "") for m in rows
    ]


def _conv_uuid(value) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        logger.warning("非法会话 ID：%r，忽略历史", value)
        return None
