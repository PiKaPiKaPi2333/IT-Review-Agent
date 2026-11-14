"""`langgraph dev` / LangGraph Studio 入口（见 langgraph.json）。

持久化由 LangGraph 平台（dev server）负责，因此这里不注入自定义 checkpointer。
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.graph.builder import build_graph


def make_graph(config: RunnableConfig):
    return build_graph()
