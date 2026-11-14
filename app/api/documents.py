"""文档管理路由：上传 → 入库；查询状态；重试。"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sqlalchemy import select

from app.api.schemas import DocumentInfo, IngestResponse
from app.infra.config import get_settings
from app.infra.db import session_scope
from app.infra.models import Document
from app.knowledge.ingest import (
    IngestError,
    ingest_document,
    rebuild_document,
    retry_from_parsing,
)
from app.knowledge.mineru_parser import MinerUParser

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/documents", response_model=IngestResponse, summary="上传并入库文档")
async def upload_document(
    file: UploadFile = File(...),
    audit_ctx_id: str | None = Form(default=None),
    doc_type: str | None = Form(default=None),
    audit_year: int | None = Form(default=None),
    department: str | None = Form(default=None),
) -> IngestResponse:
    s = get_settings()
    ok, detail = MinerUParser().is_available()
    if not ok:
        raise HTTPException(status_code=503, detail=detail)
    upload_dir = Path(s.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "upload.pdf").name
    dest = upload_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"

    try:
        with dest.open("wb") as f:
            while chunk := await file.read(1 << 20):
                f.write(chunk)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"保存上传文件失败：{e}") from e

    try:
        result = await ingest_document(
            file_path=dest,
            filename=safe_name,
            audit_ctx_id=audit_ctx_id,
            doc_type=doc_type,
            audit_year=audit_year,
            department=department,
        )
    except IngestError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return IngestResponse(**result)


@router.get("/documents", response_model=list[DocumentInfo], summary="文档清单")
async def list_documents(limit: int = 100) -> list[DocumentInfo]:
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(Document).order_by(Document.created_at.desc()).limit(limit)
            )
        ).scalars().all()
    return [
        DocumentInfo(
            id=d.id,
            filename=d.filename,
            status=d.status,
            parse_meta=d.parse_meta,
            error=d.error,
        )
        for d in rows
    ]


@router.get("/documents/{document_id}", response_model=DocumentInfo, summary="文档状态")
async def get_document(document_id: str) -> DocumentInfo:
    async with session_scope() as db:
        doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return DocumentInfo(
        id=doc.id, filename=doc.filename, status=doc.status,
        parse_meta=doc.parse_meta, error=doc.error,
    )


@router.post("/documents/{document_id}/retry", response_model=IngestResponse, summary="从解析阶段重试")
async def retry_document(document_id: str) -> IngestResponse:
    """复用 documents.source_path 的原始文件，无需重新上传。"""
    try:
        result = await retry_from_parsing(document_id)
    except IngestError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return IngestResponse(**result)


@router.post(
    "/documents/{document_id}/rebuild",
    response_model=IngestResponse,
    summary="重建向量（强制重新解析并覆盖写入）",
)
async def rebuild_document_route(document_id: str) -> IngestResponse:
    """用户触发的重建：即使文档已 ready 也会重新解析、切分、向量化并覆盖旧 chunk。"""
    try:
        result = await rebuild_document(document_id)
    except IngestError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return IngestResponse(**result)
