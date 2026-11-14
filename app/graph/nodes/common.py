"""节点的公共工具：prompt 填参、片段格式化、上下文预算。"""

from __future__ import annotations

from app.infra.config import get_settings
from app.schemas.models import RetrievedChunk

MAX_SNIPPET_CHARS = 1200


def format_chunks(chunks: list[RetrievedChunk], max_tokens: int | None = None) -> str:
    """把片段渲染成 prompt 块，按 token 预算裁剪（架构 §3.4.2 上下文压缩）。"""
    if not chunks:
        return "（无）"
    budget = max_tokens or get_settings().max_context_tokens
    from app.knowledge.chunker import count_tokens

    out: list[str] = []
    used = 0
    for i, c in enumerate(chunks, 1):
        meta = c.metadata or {}
        filename = meta.get("filename") or c.document_id
        loc = f"{filename}"
        if c.page:
            loc += f" 第{c.page}页"
        if c.section:
            loc += f" · {c.section}"
        body = c.text.strip()
        if len(body) > MAX_SNIPPET_CHARS:
            body = body[:MAX_SNIPPET_CHARS] + "…"
        block = (
            f"[片段{i}] chunk_id={c.chunk_id}\n"
            f"出处：{loc}\n"
            f"内容：\n{body}"
        )
        cost = count_tokens(block)
        if used + cost > budget and out:
            break
        out.append(block)
        used += cost
    return "\n\n---\n\n".join(out)


def truncate(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + "…"
