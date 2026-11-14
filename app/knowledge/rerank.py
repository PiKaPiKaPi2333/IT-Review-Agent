"""重排序：硅基流动 /rerank（bge-reranker-v2-m3）。失败自动跳过。

注意分数口径：重排成功时 `score` 是 reranker 的相关性分数（0~1）；
未重排（禁用 / 调用失败）时保留的是上游融合分数（如 RRF，量级约 0.03），
两者不可直接比较，调用方需要根据 `applied` 决定是否按绝对分数过滤。
"""

from __future__ import annotations

import logging

import httpx

from app.infra.config import Settings, get_settings
from app.schemas.models import RetrievedChunk

logger = logging.getLogger(__name__)


async def rerank_with_status(
    query: str,
    chunks: list[RetrievedChunk],
    *,
    top_n: int | None = None,
    settings: Settings | None = None,
) -> tuple[list[RetrievedChunk], bool]:
    """返回 (片段列表, 是否真正完成重排)。"""
    s = settings or get_settings()
    if not s.rerank_enabled or not chunks:
        return chunks[: top_n or len(chunks)], False

    top_n = top_n or min(len(chunks), s.rerank_top_n)
    try:
        async with httpx.AsyncClient(
            base_url=s.siliconflow_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {s.siliconflow_api_key}"},
            timeout=60.0,
        ) as client:
            resp = await client.post(
                "/rerank",
                json={
                    "model": s.rerank_model,
                    "query": query,
                    "documents": [c.text for c in chunks],
                    "top_n": top_n,
                    "return_documents": False,
                },
            )
        if resp.status_code != 200:
            logger.warning("rerank HTTP %s，跳过重排", resp.status_code)
            return chunks[:top_n], False
        results = resp.json().get("results") or []
    except Exception as e:  # noqa: BLE001
        logger.warning("rerank 调用失败（%s），跳过重排", e)
        return chunks[:top_n], False

    reranked: list[RetrievedChunk] = []
    for item in results:
        idx = item.get("index")
        if idx is None or not (0 <= idx < len(chunks)):
            continue
        base = chunks[idx]
        reranked.append(
            base.model_copy(update={"score": float(item.get("relevance_score", base.score))})
        )
    if not reranked:
        return chunks[:top_n], False
    return reranked, True


async def rerank(
    query: str,
    chunks: list[RetrievedChunk],
    *,
    top_n: int | None = None,
    settings: Settings | None = None,
) -> list[RetrievedChunk]:
    reranked, _ = await rerank_with_status(query, chunks, top_n=top_n, settings=settings)
    return reranked
