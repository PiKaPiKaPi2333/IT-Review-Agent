"""FastAPI 入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import documents, memory, qa
from app.graph.builder import reset_graph
from app.infra.config import get_settings
from app.infra.db import dispose_engine, ensure_schema
from app.infra.logging import setup_logging
from app.infra.tracing import init_tracing
from app.memory.short_term import checkpointer_holder

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_tracing()
    s = get_settings()
    if s.auto_migrate:
        try:
            await ensure_schema()
        except Exception as e:  # noqa: BLE001
            logger.warning("数据库迁移失败（可能数据库未就绪）：%s", e)
    await checkpointer_holder.startup()
    reset_graph()
    logger.info("API 启动完成 (env=%s)", s.app_env)
    yield
    await checkpointer_holder.shutdown()
    await dispose_engine()
    logger.info("API 已关闭")


async def require_api_key(authorization: str | None = Header(default=None)) -> None:
    """可选 API 鉴权：.env 里配了 API_KEY 才校验。"""
    expected = get_settings().api_key
    if not expected:
        return
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="无效的 API Key")


def create_app() -> FastAPI:
    app = FastAPI(
        title="审计智能问答系统",
        version="0.1.0",
        description="分类 → 检索 → 充分性判断 → 带引用生成 → 引用核验",
        lifespan=lifespan,
    )

    # CORS：开发时前端跑在 5173，生产时同源走静态文件。允许带凭据。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(qa.router, prefix="/api/v1", tags=["qa"], dependencies=[Depends(require_api_key)])
    app.include_router(
        documents.router, prefix="/api/v1", tags=["documents"], dependencies=[Depends(require_api_key)]
    )
    app.include_router(
        memory.router, prefix="/api/v1", tags=["memory"], dependencies=[Depends(require_api_key)]
    )

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        return {
            "status": "ok",
            "checkpointer": checkpointer_holder.backend,
        }

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # noqa: ANN001
        logger.exception("未处理异常 %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "内部错误，请查看服务日志"})

    # 生产模式：若 frontend/dist 存在，挂载 SPA 静态资源
    dist_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if dist_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            # 非 API 路径回退到 index.html，支持前端路由刷新
            index = dist_dir / "index.html"
            candidate = dist_dir / full_path
            if full_path and not full_path.startswith("api") and not full_path.startswith("health") and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

    return app


app = create_app()
