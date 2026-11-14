"""Milvus collection 管理与混合检索（dense + BM25 sparse）。

依赖 Milvus 2.5+：SPARSE 字段由 BM25 Function 服务端生成，
检索侧用 `hybrid_search` + RRFRanker 融合。MilvusClient 是同步的，
在异步链路里统一用 `asyncio.to_thread` 包一层。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.infra.config import Settings, get_settings
from app.infra.tracing import traceable
from app.schemas.models import RetrievedChunk

logger = logging.getLogger(__name__)

DENSE_FIELD = "dense_vector"
SPARSE_FIELD = "sparse_vector"
TEXT_FIELD = "text"

# 标量字段：用于过滤与回填
SCALAR_FIELDS: dict[str, tuple[str, int]] = {
    "document_id": ("VARCHAR", 128),
    "page": ("INT64", 0),
    "section": ("VARCHAR", 512),
    "doc_type": ("VARCHAR", 64),
    "audit_year": ("INT64", 0),
    "department": ("VARCHAR", 128),
    "audit_ctx_id": ("VARCHAR", 64),
    "chunk_type": ("VARCHAR", 32),
    "filename": ("VARCHAR", 512),
}

FILTERABLE_FIELDS = (
    "document_id",
    "doc_type",
    "audit_year",
    "department",
    "audit_ctx_id",
    "chunk_type",
)

OUTPUT_FIELDS = [TEXT_FIELD, *SCALAR_FIELDS.keys()]


class MilvusStore:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self._client = None
        self._ready = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 客户端 / schema
    # ------------------------------------------------------------------
    def _get_client(self):
        if self._client is None:
            from pymilvus import MilvusClient

            kwargs: dict[str, Any] = {"uri": self.s.milvus_uri}
            if self.s.milvus_token:
                kwargs["token"] = self.s.milvus_token
            self._client = MilvusClient(**kwargs)
        return self._client

    def _build_schema(self, dim: int):
        from pymilvus import DataType, Function, FunctionType, MilvusClient

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field(
            TEXT_FIELD,
            DataType.VARCHAR,
            max_length=8192,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
        )
        schema.add_field(DENSE_FIELD, DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field(SPARSE_FIELD, DataType.SPARSE_FLOAT_VECTOR)
        for name, (dtype, max_len) in SCALAR_FIELDS.items():
            if dtype == "VARCHAR":
                schema.add_field(name, DataType.VARCHAR, max_length=max_len)
            else:
                schema.add_field(name, DataType.INT64)
        schema.add_function(
            Function(
                name="text_bm25",
                input_field_names=[TEXT_FIELD],
                output_field_names=[SPARSE_FIELD],
                function_type=FunctionType.BM25,
            )
        )
        return schema

    def _build_index_params(self):
        from pymilvus import MilvusClient

        idx = MilvusClient.prepare_index_params()
        idx.add_index(
            field_name=DENSE_FIELD,
            index_type="HNSW",
            metric_type="IP",
            params={"M": 16, "efConstruction": 200},
        )
        idx.add_index(field_name=SPARSE_FIELD, index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
        for name in FILTERABLE_FIELDS:
            idx.add_index(field_name=name, index_type="INVERTED")
        return idx

    # ------------------------------------------------------------------
    # 集合生命周期
    # ------------------------------------------------------------------
    def _ensure_collection_sync(self, dim: int) -> None:
        client = self._get_client()
        name = self.s.milvus_collection
        if client.has_collection(name):
            return
        logger.info("创建 Milvus collection %s (dim=%d)", name, dim)
        client.create_collection(
            collection_name=name,
            schema=self._build_schema(dim),
            index_params=self._build_index_params(),
            consistency_level="Bounded",
        )
        client.load_collection(name)

    async def ensure_collection(self, dim: int | None = None) -> None:
        async with self._lock:
            if self._ready:
                return
            await asyncio.to_thread(self._ensure_collection_sync, dim or self.s.embedding_dim)
            self._ready = True

    def _drop_document_sync(self, document_id: str) -> int:
        client = self._get_client()
        expr = f'document_id == "{_escape(document_id)}"'
        res = client.delete(collection_name=self.s.milvus_collection, filter=expr)
        return int(res.get("delete_count", 0)) if isinstance(res, dict) else 0

    # ------------------------------------------------------------------
    # 写入（幂等：先删后插该文档全部 chunk）
    # ------------------------------------------------------------------
    @traceable(run_type="tool", name="milvus.upsert_document")
    async def upsert_document(
        self,
        *,
        document_id: str,
        rows: list[dict[str, Any]],
        dim: int | None = None,
    ) -> int:
        if not rows:
            return 0
        await self.ensure_collection(dim or self.s.embedding_dim)
        await asyncio.to_thread(self._drop_document_sync, document_id)
        await asyncio.to_thread(self._insert_sync, rows)
        logger.info("Milvus 写入 %d 条 chunk (document_id=%s)", len(rows), document_id)
        return len(rows)

    def _insert_sync(self, rows: list[dict[str, Any]]) -> None:
        client = self._get_client()
        cleaned: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            item[TEXT_FIELD] = (item.get(TEXT_FIELD) or "")[:8000]
            item["section"] = (item.get("section") or "")[:500]
            item["filename"] = (item.get("filename") or "")[:500]
            item["document_id"] = str(item.get("document_id") or "")[:120]
            item["chunk_id"] = str(item.get("chunk_id") or "")[:120]
            # SPARSE_FIELD 由 BM25 Function 服务端生成，插入时**不能**出现在数据里，
            # 否则 Milvus 会报 "field is generated by function"。
            item.pop(SPARSE_FIELD, None)
            cleaned.append(item)
        client.insert(collection_name=self.s.milvus_collection, data=cleaned)
        client.flush(self.s.milvus_collection)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    @traceable(run_type="retriever", name="milvus.hybrid_search")
    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_vector: list[float],
        top_k: int | None = None,
        candidates: int | None = None,
        filter_expr: str | None = None,
        dim: int | None = None,
    ) -> list[RetrievedChunk]:
        await self.ensure_collection(dim or self.s.embedding_dim)
        top_k = top_k or self.s.retrieval_top_k
        candidates = candidates or self.s.retrieval_candidates
        raw = await asyncio.to_thread(
            self._hybrid_search_sync,
            query_text,
            query_vector,
            top_k,
            candidates,
            filter_expr,
        )
        return [_to_chunk(item) for item in raw]

    def _hybrid_search_sync(
        self,
        query_text: str,
        query_vector: list[float],
        top_k: int,
        candidates: int,
        filter_expr: str | None,
    ) -> list[dict[str, Any]]:
        from pymilvus import AnnSearchRequest, RRFRanker

        client = self._get_client()
        reqs = [
            AnnSearchRequest(
                data=[query_vector],
                anns_field=DENSE_FIELD,
                param={"metric_type": "IP", "params": {"ef": max(64, candidates)}},
                limit=candidates,
                expr=filter_expr,
            ),
            AnnSearchRequest(
                data=[query_text],
                anns_field=SPARSE_FIELD,
                param={"metric_type": "BM25", "params": {}},
                limit=candidates,
                expr=filter_expr,
            ),
        ]
        try:
            res = client.hybrid_search(
                collection_name=self.s.milvus_collection,
                reqs=reqs,
                ranker=RRFRanker(60),
                limit=top_k,
                output_fields=OUTPUT_FIELDS,
            )
        except Exception as e:  # noqa: BLE001
            # 稀疏/BM25 不可用时退化为纯向量检索，保证链路不中断
            logger.warning("混合检索失败，退化为向量检索：%s", e)
            res = client.search(
                collection_name=self.s.milvus_collection,
                data=[query_vector],
                anns_field=DENSE_FIELD,
                search_params={"metric_type": "IP", "params": {"ef": max(64, candidates)}},
                limit=top_k,
                filter=filter_expr or "",
                output_fields=OUTPUT_FIELDS,
            )
        return res[0] if res else []


def _to_chunk(item: dict[str, Any]) -> RetrievedChunk:
    entity = item.get("entity", item)
    metadata = {
        k: entity.get(k)
        for k in (
            "section",
            "doc_type",
            "audit_year",
            "department",
            "audit_ctx_id",
            "chunk_type",
            "filename",
        )
        if entity.get(k) not in (None, "")
    }
    return RetrievedChunk(
        chunk_id=str(entity.get("chunk_id") or entity.get("id") or ""),
        document_id=str(entity.get("document_id") or ""),
        page=entity.get("page"),
        text=entity.get(TEXT_FIELD, "") or "",
        score=float(item.get("distance", item.get("score", 0.0)) or 0.0),
        metadata=metadata,
    )


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


_store: MilvusStore | None = None


def get_store() -> MilvusStore:
    global _store
    if _store is None:
        _store = MilvusStore()
    return _store
