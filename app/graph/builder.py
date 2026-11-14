"""LangGraph 组装：主图 4 个节点，每个 Agent 是自己的子图。

主图拓扑：

    START → decompose ─┬─ in_scope      → retrieval ─┬─ sufficient   → verify → finalize
                       │                             └─ insufficient ─────────→ finalize
                       ├─ clarify      ─────────────────────────────────────────→ finalize
                       └─ out_of_scope ─────────────────────────────────────────→ finalize
    finalize → END

子图（实现见 app/agents/）：
- decompose：问题拆解子图（单步：指代消解 / 子问题拆解 / 分类 / 查询改写）
- retrieval：检索子图（recall ⇄ judge，充分性不足且可补查时循环）
- verify：验证子图（answer → check ⇄ regenerate，核验不通过且可修正时循环）

finalize：汇总输出（澄清 / 超范围 / 证据不足 / 草稿）→ 归档 → 提炼长期记忆。

overrides 按节点名替换实现，供测试与离线冒烟使用：
- "decompose" / "retrieval"：替换整个子图节点；
- "answer" / "regenerate" / "verify"：替换验证子图内部步骤（分别为生成、重生成、逐条核验）；
- "finalize"：替换收尾节点。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.decomposition import build_decompose_graph
from app.agents.retrieval import build_retrieval_graph
from app.agents.verification import build_verify_graph
from app.graph.nodes.finalize import finalize_node
from app.graph.routing import route_by_sufficiency, route_by_type
from app.graph.state import AgentState

logger = logging.getLogger(__name__)


def build_graph(
    checkpointer=None,
    *,
    overrides: dict[str, Callable[..., Any]] | None = None,
):
    """组装主图。overrides 的含义见模块文档。"""
    ov = dict(overrides or {})

    g = StateGraph(AgentState)
    g.add_node("decompose", ov.get("decompose") or build_decompose_graph())
    g.add_node("retrieval", ov.get("retrieval") or build_retrieval_graph())
    g.add_node(
        "verify",
        build_verify_graph(
            answer=ov.get("answer"),
            regenerate=ov.get("regenerate"),
            check=ov.get("verify"),
        ),
    )
    g.add_node("finalize", ov.get("finalize") or finalize_node)

    g.add_edge(START, "decompose")
    g.add_conditional_edges(
        "decompose",
        route_by_type,
        {"in_scope": "retrieval", "clarify": "finalize", "out_of_scope": "finalize"},
    )
    g.add_conditional_edges(
        "retrieval",
        route_by_sufficiency,
        {"sufficient": "verify", "insufficient": "finalize"},
    )
    g.add_edge("verify", "finalize")
    g.add_edge("finalize", END)

    compiled = g.compile(checkpointer=checkpointer)
    logger.info("LangGraph 编译完成（主图 4 节点：decompose / retrieval / verify / finalize）")
    return compiled


_graph = None


def get_graph():
    """进程级单例。checkpointer 由 short_term 模块在 lifespan 中初始化。"""
    global _graph
    if _graph is None:
        from app.memory.short_term import get_checkpointer

        _graph = build_graph(get_checkpointer())
    return _graph


def reset_graph() -> None:
    global _graph
    _graph = None
