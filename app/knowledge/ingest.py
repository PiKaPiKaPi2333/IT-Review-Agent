"""文档入库编排：MinerU 解析 → 切分 → Embedding → Milvus。

状态机（架构 §5.3）：uploaded → parsing → indexing → ready / failed
以 document_id 为粒度幂等：重复导入 = 先删后插该文档全部 chunk。
失败可从 parsing 阶段重试，无需重新上传。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.infra.config import get_settings
from app.infra.db import session_scope
from app.infra.models import Document
from app.infra.tracing import traceable
from app.knowledge.chunker import Chunk, ParsedBlock, SemanticChunker
from app.knowledge.embedder import get_embedder
from app.knowledge.mineru_parser import MinerUParser, MinerUError, extract_parse_meta
from app.knowledge.milvus_store import get_store
from app.knowledge.spreadsheet_parser import (
    SPREADSHEET_SUFFIXES,
    SpreadsheetError,
    SpreadsheetParser,
    extract_spreadsheet_meta,
)

logger = logging.getLogger(__name__)

# MinerU API 支持的格式
MINERU_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp", ".html",
}

# 明确不支持、也不该入库的（隐藏目录里的编辑器配置等）
IGNORED_SUFFIXES = {".json", ".lock", ".log", ".tmp", ".md"}


class IngestError(RuntimeError):
    pass


def detect_route(file_path: str | Path) -> str:
    """按扩展名决定解析后端：spreadsheet / mineru / unsupported。"""
    suffix = Path(file_path).suffix.lower()
    if suffix in SPREADSHEET_SUFFIXES:
        return "spreadsheet"
    if suffix in MINERU_SUFFIXES:
        return "mineru"
    return "unsupported"


async def parse_file(
    file_path: str | Path, *, document_id: str
) -> tuple[list[ParsedBlock], dict]:
    """解析单个文件，返回 (blocks, meta)。自动按格式路由。

    - xlsx / xlsb / csv → 本地表格解析（MinerU API 不支持）
    - pdf / docx / pptx / 图片 / html → MinerU 官方 API
    """
    path = Path(file_path)
    route = detect_route(path)

    if route == "spreadsheet":
        parser = SpreadsheetParser()
        ok, detail = parser.is_available()
        if not ok:
            raise IngestError(detail)
        try:
            blocks = parser.parse(path, document_id=document_id)
        except SpreadsheetError as e:
            raise IngestError(str(e)) from e
        return blocks, extract_spreadsheet_meta(blocks)

    if route == "mineru":
        parser = MinerUParser()
        ok, detail = parser.is_available()
        if not ok:
            raise IngestError(detail)
        try:
            blocks = await parser.parse(path, document_id=document_id)
        except MinerUError as e:
            raise IngestError(str(e)) from e
        return blocks, extract_parse_meta(blocks)

    raise IngestError(
        f"不支持的文件格式：{path.suffix or '（无扩展名）'}。"
        f"支持：PDF/Word/PPT/图片/HTML（MinerU）、xlsx/xlsb/csv（本地）。"
    )


def compute_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


async def register_document(
    *,
    filename: str,
    local_path: Path,
    document_id: str | None = None,
    audit_ctx_id: str | None = None,
    options: dict[str, Any] | None = None,
    force: bool = False,
) -> Document:
    """登记文档。

    - 传入已存在的 document_id（重试 / 重建）：复用原行，重置为 uploaded，不新建记录。
    - 否则按 checksum 去重：已 ready 且未 force 时直接返回，实现增量上传。
    """
    checksum = compute_checksum(local_path)

    async with session_scope() as db:
        if document_id:
            doc = await db.get(Document, document_id)
            if doc is not None:
                doc.status = "uploaded"
                doc.error = None
                doc.checksum = checksum
                doc.source_path = str(local_path.resolve())
                doc.filename = filename
                if options is not None:
                    doc.ingest_options = options
                await db.flush()
                return doc

        if not force:
            existing = (
                await db.execute(
                    select(Document).where(
                        Document.checksum == checksum, Document.status == "ready"
                    )
                )
            ).scalars().first()
            if existing is not None:
                logger.info("文档已存在且 ready，跳过：%s", existing.id)
                return existing

        doc = Document(
            id=document_id or uuid.uuid4().hex[:24],
            filename=filename,
            status="uploaded",
            checksum=checksum,
            source_path=str(local_path.resolve()),
            audit_ctx_id=_uuid_or_none(audit_ctx_id),
            ingest_options=options,
        )
        db.add(doc)
        await db.flush()
        return doc


async def _set_status(document_id: str, status: str, *, error: str | None = None, meta: dict | None = None) -> None:
    async with session_scope() as db:
        doc = await db.get(Document, document_id)
        if doc is None:
            return
        doc.status = status
        doc.error = error
        if meta is not None:
            doc.parse_meta = meta


async def get_document_source(document_id: str) -> Path | None:
    """取出原始文件路径（失败重试时复用，不必重新上传）。"""
    async with session_scope() as db:
        doc = await db.get(Document, document_id)
        if doc is None or not doc.source_path:
            return None
        p = Path(doc.source_path)
        return p if p.exists() else None


@traceable(run_type="chain", name="ingest.document")
async def ingest_document(
    *,
    file_path: str | Path,
    document_id: str | None = None,
    filename: str | None = None,
    audit_ctx_id: str | None = None,
    doc_type: str | None = None,
    audit_year: int | None = None,
    department: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """完整入库流程。返回 {document_id, status, chunks, ...}。

    默认增量：checksum 相同且已 ready 的文档直接跳过；force=True 时强制重新解析并写入。
    """
    s = get_settings()
    path = Path(file_path)
    if not path.exists():
        raise IngestError(f"文件不存在：{path}")

    doc = await register_document(
        filename=filename or path.name,
        local_path=path,
        document_id=document_id,
        audit_ctx_id=audit_ctx_id,
        options={"doc_type": doc_type, "audit_year": audit_year, "department": department},
        force=force,
    )
    doc_id = doc.id

    if doc.status == "ready":
        return {"document_id": doc_id, "status": "ready", "chunks": 0, "skipped": True}

    # ---------- parsing（按格式路由：表格本地解析 / 其余走 MinerU API） ----------
    await _set_status(doc_id, "parsing")
    try:
        blocks, meta = await parse_file(path, document_id=doc_id)
    except IngestError as e:
        await _set_status(doc_id, "failed", error=str(e))
        raise
    await _set_status(doc_id, "parsing", meta=meta)

    # ---------- indexing ----------
    await _set_status(doc_id, "indexing", meta=meta)
    try:
        chunker = SemanticChunker()
        chunks: list[Chunk] = chunker.split(
            blocks,
            document_id=doc_id,
            extra_meta={
                "doc_type": doc_type or "",
                "audit_year": int(audit_year or 0),
                "department": department or "",
                "audit_ctx_id": audit_ctx_id or "",
                "filename": doc.filename or "",
            },
        )
        if not chunks:
            raise IngestError("切分后无内容")

        embedder = get_embedder()
        vectors = await embedder.embed_documents([c.text for c in chunks])
        if len(vectors) != len(chunks):
            raise IngestError("embedding 数量与 chunk 数量不匹配")

        rows = [_to_row(c, v) for c, v in zip(chunks, vectors, strict=True)]
        store = get_store()
        await store.upsert_document(
            document_id=doc_id, rows=rows, dim=len(vectors[0]) if vectors else s.embedding_dim
        )
    except Exception as e:  # noqa: BLE001
        await _set_status(doc_id, "failed", error=str(e), meta=meta)
        raise IngestError(f"索引失败：{e}") from e

    await _set_status(doc_id, "ready", meta={**meta, "chunks": len(chunks)})
    logger.info("入库完成 document_id=%s chunks=%d", doc_id, len(chunks))
    return {"document_id": doc_id, "status": "ready", "chunks": len(chunks), **meta}


def _to_row(chunk: Chunk, vector: list[float]) -> dict[str, Any]:
    m = chunk.metadata or {}
    return {
        "chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "text": chunk.text,
        "dense_vector": vector,
        "page": int(chunk.page or 0),
        "section": chunk.section,
        "doc_type": str(m.get("doc_type") or ""),
        "audit_year": int(m.get("audit_year") or 0),
        "department": str(m.get("department") or ""),
        "audit_ctx_id": str(m.get("audit_ctx_id") or ""),
        "chunk_type": str(m.get("chunk_type") or "text"),
        "filename": str(m.get("filename") or ""),
    }


def _uuid_or_none(value: str | None):
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def _load_reingest_args(document_id: str) -> dict[str, Any]:
    """从数据库取回原始文件与入库参数，供重试 / 重建使用。"""
    async with session_scope() as db:
        doc = await db.get(Document, document_id)
        if doc is None:
            raise IngestError("文档不存在")
        if not doc.source_path or not Path(doc.source_path).exists():
            raise IngestError("找不到该文档的原始文件，请重新上传")
        opts = doc.ingest_options or {}
        return {
            "file_path": Path(doc.source_path),
            "document_id": doc.id,
            "filename": doc.filename,
            "audit_ctx_id": str(doc.audit_ctx_id) if doc.audit_ctx_id else None,
            "doc_type": opts.get("doc_type"),
            "audit_year": opts.get("audit_year"),
            "department": opts.get("department"),
        }


async def retry_from_parsing(document_id: str) -> dict[str, Any]:
    """失败重试：不重新上传，复用原始文件与入库参数，从解析阶段重来。"""
    args = await _load_reingest_args(document_id)
    return await ingest_document(**args)


async def rebuild_document(document_id: str) -> dict[str, Any]:
    """重建向量：无论当前状态如何，强制重新解析、切分、向量化并覆盖写入该文档的 chunk。

    注意：embedding 在删除旧向量之前完成，embedding 失败时旧向量保持不变。
    """
    args = await _load_reingest_args(document_id)
    return await ingest_document(**args, force=True)

