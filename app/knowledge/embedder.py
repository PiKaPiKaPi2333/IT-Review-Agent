"""Embedding 客户端（硅基流动 /v1/embeddings，bge-m3）。

稀疏向量不在这里产出：Milvus 2.5 的 BM25 Function 会在服务端
对 `text` 字段分词并生成 sparse vector，检索侧做 dense+sparse 混合。
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.infra.config import Settings, get_settings

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    pass


class Embedder:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(4)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.s.siliconflow_base_url.rstrip("/"),
                headers={"Authorization": f"Bearer {self.s.siliconflow_api_key}"},
                timeout=120.0,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, EmbeddingError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, max=20),
        reraise=True,
    )
    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        async with self._sem:
            resp = await self.client.post(
                "/embeddings",
                json={
                    "model": self.s.embedding_model,
                    "input": texts,
                    "encoding_format": "float",
                },
            )
        if resp.status_code != 200:
            raise EmbeddingError(f"embedding HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json().get("data") or []
        if len(data) != len(texts):
            raise EmbeddingError(f"embedding 返回数量不匹配：{len(data)} != {len(texts)}")

        vectors: list[list[float]] = []
        for item in data:
            vec = item.get("embedding")
            if not vec:
                raise EmbeddingError("embedding 返回空向量")
            if len(vec) != self.s.embedding_dim:
                logger.warning(
                    "embedding 维度 %d 与配置 %d 不一致，以实际为准",
                    len(vec),
                    self.s.embedding_dim,
                )
            vectors.append(vec)
        return vectors

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        size = max(1, self.s.embedding_batch_size)
        batches = [texts[i : i + size] for i in range(0, len(texts), size)]
        results: list[list[float]] = []
        for idx, batch in enumerate(batches, 1):
            vectors = await self._embed_batch(batch)
            results.extend(vectors)
            logger.debug("embedding batch %d/%d 完成", idx, len(batches))
        return results

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed_batch([text])
        return vectors[0]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
