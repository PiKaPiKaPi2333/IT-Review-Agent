"""测试全局配置：不依赖 .env，不触网。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True, scope="session")
def _env() -> None:
    os.environ.setdefault("SILICONFLOW_API_KEY", "test-key")
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("RERANK_ENABLED", "false")
    os.environ.setdefault("AUTO_CREATE_TABLES", "false")
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@127.0.0.1:1/none")
    yield


@pytest.fixture
def sample_chunk():
    from app.schemas.models import RetrievedChunk

    return RetrievedChunk(
        chunk_id="doc1-abc",
        document_id="doc1",
        page=3,
        text="第三章 审批权限\n采购金额超过一百万元（含）的，须报总经理办公会审批。",
        score=0.92,
        metadata={"filename": "采购管理办法.pdf", "section": "第三章 审批权限"},
    )
