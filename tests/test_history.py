"""会话历史压缩：超出 token 预算时，更早消息滚动摘要、最近消息保留原文。"""

from __future__ import annotations

import asyncio

from app.memory.history import HistoryMessage, compose_history


def _msgs(n: int, size: int = 200) -> list[HistoryMessage]:
    return [
        HistoryMessage(
            id=f"m{i}",
            role="user" if i % 2 == 0 else "assistant",
            content=f"第{i}条" + "文" * size,
        )
        for i in range(n)
    ]


def _fake_summarizer(calls: list):
    async def summarize(previous: str, messages, max_tokens: int) -> str:
        calls.append((previous, [m.id for m in messages]))
        return f"摘要{len(calls)}"

    return summarize


def test_fits_within_budget_without_summary():
    calls: list = []
    out = asyncio.run(
        compose_history("conv-fit", _msgs(2, size=5), budget=1000,
                        summarize=_fake_summarizer(calls))
    )
    assert calls == []
    assert "第0条" in out
    assert "摘要" not in out


def test_overflow_summarizes_older_and_keeps_recent_verbatim():
    calls: list = []
    msgs = _msgs(8)
    out = asyncio.run(
        compose_history("conv-overflow", msgs, budget=300, summarize=_fake_summarizer(calls))
    )
    assert "【更早对话摘要】\n摘要1" in out
    assert "【最近对话】" in out
    assert msgs[-1].content[:30] in out
    # 第一次压缩应覆盖除最近消息外的全部更早消息，且没有旧摘要
    prev, covered = calls[0]
    assert prev == ""
    assert "m0" in covered
    assert msgs[-1].id not in covered


def test_rolling_summary_only_merges_newly_aged_messages():
    calls: list = []
    conv = "conv-rolling"
    msgs = _msgs(8)
    asyncio.run(compose_history(conv, msgs, budget=300, summarize=_fake_summarizer(calls)))
    first_covered = calls[0][1]

    newer = HistoryMessage(id="m8", role="user", content="新问题" + "文" * 200)
    out = asyncio.run(
        compose_history(conv, [*msgs, newer], budget=300, summarize=_fake_summarizer(calls))
    )
    assert len(calls) == 2
    prev, new_ids = calls[1]
    assert prev == "摘要1"
    assert new_ids and set(new_ids).isdisjoint(first_covered)
    assert "摘要2" in out


def test_summary_failure_falls_back_to_recent_only():
    async def broken(previous, messages, max_tokens):
        raise RuntimeError("summarizer down")

    out = asyncio.run(compose_history("conv-fail", _msgs(8), budget=300, summarize=broken))
    assert "【更早对话摘要】" not in out
    assert "【最近对话】" in out


def test_keeps_last_message_even_when_it_alone_exceeds_budget():
    msgs = [HistoryMessage(id="x", role="user", content="文" * 900)]
    out = asyncio.run(compose_history("conv-huge", msgs, budget=100, summarize=_fake_summarizer([])))
    assert "审计方：" in out
