"""数据库连接与会话管理。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infra.config import get_settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"
ALEMBIC_DIR = _PROJECT_ROOT / "alembic"

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(
            s.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            echo=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


def upgrade_to_head() -> None:
    """同步执行 ``alembic upgrade head``（不依赖当前工作目录）。

    schema 的唯一来源是 alembic 迁移脚本，绝不调用 ``Base.metadata.create_all``：
    二者并存时，create_all 建出来的库没有版本记录，之后再跑迁移就会
    DuplicateTable。
    """
    # alembic 在 import 阶段就会打印一串插件装配日志（对使用者没有信息量），
    # 所以必须先降噪，再 import
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)

    from alembic import command
    from alembic.config import Config

    if not ALEMBIC_INI.is_file():
        raise RuntimeError(f"找不到 alembic.ini：{ALEMBIC_INI}")

    cfg = Config(str(ALEMBIC_INI))
    # ini 里的 script_location 是相对路径，改成绝对路径以免受 cwd 影响
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    # 迁移过程中不要重配 logging，否则会覆盖 app 已生效的日志设置
    cfg.attributes["configure_logging"] = False
    command.upgrade(cfg, "head")


async def _check_legacy_schema() -> None:
    """拦截「表已存在但没有 alembic 版本记录」的历史库，给出可操作的提示。"""
    from sqlalchemy import inspect

    async with get_engine().connect() as conn:
        tables = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))

    managed = tables - {"alembic_version"}
    if managed and "alembic_version" not in tables:
        raise RuntimeError(
            "数据库里已有业务表但没有 alembic 版本记录（多半是早期由 "
            "Base.metadata.create_all 建出来的库）。请先核对 schema 后执行：\n"
            "    alembic stamp head\n"
            f"当前已有表：{', '.join(sorted(managed))}"
        )


async def ensure_schema() -> None:
    """启动时把 schema 升级到最新版本，等价于 ``alembic upgrade head``。"""
    await _check_legacy_schema()
    # alembic/psycopg 是同步阻塞的，放线程里跑，别卡住事件循环
    await asyncio.to_thread(upgrade_to_head)
    logger.info("数据库 schema 已是最新版本")


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
