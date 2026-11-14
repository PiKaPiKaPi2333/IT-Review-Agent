"""检索子图：多查询召回 → 融合 / Rerank → 充分性判断，证据不足且可补查时回到召回。

子图结构：START → recall → judge ─┬─ 继续补查 → recall
                                 └─ 收敛 → END

主图只把它当作一个节点使用；补查循环由子图的条件边控制。
Milvus 客户端是同步的，recall 写成 async；LangGraph 支持 async 节点。
"""

from __future__ import annotations

import asyncio
import logging

from langgraph.graph import END, START, StateGraph

from app.graph.nodes.common import format_chunks
from app.graph.state import AgentState
from app.infra.config import get_settings
from app.infra.tracing import traceable
from app.knowledge.embedder import get_embedder
from app.knowledge.milvus_store import get_store
from app.knowledge.rerank import rerank_with_status
from app.llm.client import build_chat_model, structured_llm_call
from app.llm.prompts.sufficiency import SUFFICIENCY_SYSTEM, SUFFICIENCY_USER
from app.schemas.models import RetrievedChunk, SufficiencyResult

logger = logging.getLogger(__name__)


def build_retrieval_graph():
    """编译检索子图。节点函数在调用时解析，便于测试替换。"""
    g = StateGraph(AgentState)
    g.add_node("recall", recall)
    g.add_node("judge", judge)
    g.add_edge(START, "recall")
    g.add_edge("recall", "judge")
    g.add_conditional_edges("judge", _next_step, {"recall": "recall", "end": END})
    return g.compile()


def _next_step(state: AgentState) -> str:
    """充分则收敛；无补查词、无法再补查、或轮次用尽也收敛。"""
    s = get_settings()
    suf = state.sufficiency
    if (
        suf is None
        or suf.is_sufficient
        or not suf.next_queries
        or state.retrieval_round >= s.max_retrieval_rounds
    ):
        return "end"
    return "recall"


@traceable(run_type="chain", name="agent.retrieval")
async def retrieval_agent(state: AgentState) -> dict:
    """兼容入口：运行检索子图，返回最终状态。主图直接使用 build_retrieval_graph()。"""
    return await build_retrieval_graph().ainvoke(state)


@traceable(run_type="chain", name="retrieval.recall")
async def recall(state: AgentState) -> dict:
    cls = state.classification
    queries: list[str] = []
    if cls is not None:
        queries = list(cls.search_queries)

    # 充分性判断给出的补查查询优先（后续轮次）
    if state.sufficiency and state.sufficiency.next_queries:
        queries = list(state.sufficiency.next_queries) + queries

    # 去掉已经用过的查询，避免同一轮重复召回同一批片段
    used = set(state.used_queries)
    fresh = [q for q in _dedup(queries) if q not in used]
    if not fresh:
        # 没有新查询说明已达检索上限，交给充分性判断收尾
        logger.info("无新增查询，跳过本轮检索")
        return {"retrieval_round": state.retrieval_round + 1, "sufficiency": None}

    embedder = get_embedder()
    store = get_store()
    filter_expr = build_filter(state)

    async def one(query: str) -> list[RetrievedChunk]:
        vector = await embedder.embed_query(query)
        return await store.hybrid_search(
            query_text=query,
            query_vector=vector,
            top_k=max(state_top_k(), 4),
            filter_expr=filter_expr,
        )

    results = await asyncio.gather(*(one(q) for q in fresh), return_exceptions=True)

    merged: dict[str, RetrievedChunk] = {}
    for q, res in zip(fresh, results, strict=True):
        if isinstance(res, Exception):
            logger.warning("查询「%s」检索失败：%s", q, res)
            continue
        for chunk in res:
            prev = merged.get(chunk.chunk_id)
            if prev is None or chunk.score > prev.score:
                merged[chunk.chunk_id] = chunk

    candidates = sorted(merged.values(), key=lambda c: c.score, reverse=True)

    # 融合后的候选交给 Rerank，取最终 Top-K
    reranked, applied = await rerank_with_status(fresh[0], candidates, top_n=state_top_k())

    # 跨轮合并：保留上一轮已召回的高分片段（增量检索语义）
    history = {c.chunk_id: c for c in state.retrieved_chunks}
    for c in reranked:
        prev = history.get(c.chunk_id)
        if prev is None or c.score > prev.score:
            history[c.chunk_id] = c

    final = sorted(history.values(), key=lambda c: c.score, reverse=True)
    # 绝对分数阈值只对重排分数有意义；未重排时分数是 RRF 融合值，直接过滤会把所有片段都丢掉
    if applied:
        final = [c for c in final if c.score >= _min_score()]
    final = final[: state_top_k()]

    logger.info(
        "第 %d 轮检索：queries=%s → 召回 %d 条（保留 %d 条）",
        state.retrieval_round + 1,
        fresh,
        len(merged),
        len(final),
    )

    return {
        "retrieval_round": state.retrieval_round + 1,
        "used_queries": [*state.used_queries, *fresh],
        "retrieved_chunks": final,
        "sufficiency": None,  # 新证据需要重新判断
    }


@traceable(run_type="chain", name="retrieval.judge")
def judge(state: AgentState) -> dict:
    """充分性判断：规则 + LLM 混合（架构 §3.4.2 / §13 风险对策）。"""
    s = get_settings()
    chunks = state.retrieved_chunks

    # ---- 规则层：拦掉明显不充分的情况，省一次 LLM 调用 ----
    rule = _rule_check(chunks, state.retrieval_round, s.max_retrieval_rounds)
    if rule is not None:
        return {"sufficiency": rule}

    cls = state.classification
    sub_questions = (
        "\n".join(f"- {q}" for q in cls.sub_questions)
        if cls and cls.sub_questions
        else "（无，按原问题整体判断）"
    )
    prompt = SUFFICIENCY_USER.format(
        question=cls.rewritten_question if cls else state.question,
        sub_questions=sub_questions,
        question_type=cls.question_type.value if cls else "unknown",
        used_queries="、".join(state.used_queries) or "（无）",
        round=state.retrieval_round,
        max_rounds=s.max_retrieval_rounds,
        n=len(chunks),
        chunks=format_chunks(chunks),
    )
    result = structured_llm_call(
        build_chat_model("classifier"),
        SufficiencyResult,
        system=SUFFICIENCY_SYSTEM,
        user=prompt,
    )

    # 轮次用尽时不再补查：已有相关片段则基于现有证据作答，缺口在回答中说明，引用仍由校验环节把关
    if state.retrieval_round >= s.max_retrieval_rounds and not result.is_sufficient:
        result.next_queries = []
        if chunks:
            result.is_sufficient = True
            result.reason = (result.reason or "") + "（检索轮次已用尽，基于现有片段作答，缺口需在回答中说明）"

    logger.info(
        "充分性判断 sufficient=%s rounds=%d/%d missing=%s",
        result.is_sufficient,
        state.retrieval_round,
        s.max_retrieval_rounds,
        result.missing_aspects,
    )
    return {"sufficiency": result}


def build_filter(state: AgentState) -> str | None:
    """审计项目隔离：只检索当前审计上下文内的文档（架构 §11 安全）。"""
    if state.audit_ctx_id:
        escaped = state.audit_ctx_id.replace('"', '\\"')
        return f'audit_ctx_id == "{escaped}"'
    return None


def _rule_check(chunks, round_no: int, max_rounds: int) -> SufficiencyResult | None:
    if not chunks:
        if round_no >= max_rounds:
            return SufficiencyResult(
                is_sufficient=False,
                reason="多轮检索后仍未召回任何相关片段，判定为证据不足。",
                next_queries=[],
            )
        return SufficiencyResult(
            is_sufficient=False,
            reason="本轮未召回任何相关片段。",
            missing_aspects=["知识库中未命中相关内容"],
            next_queries=[],
        )
    return None


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in items:
        i = (i or "").strip()
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _min_score() -> float:
    return get_settings().retrieval_min_score


def state_top_k() -> int:
    return get_settings().retrieval_top_k
