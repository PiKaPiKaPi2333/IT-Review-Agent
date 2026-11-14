"""短时记忆：LangGraph Checkpointer（优先 PostgreSQL 后端）。

每个 thread_id 对应一次审计咨询会话。PostgresSaver 自建
`checkpoints` / `checkpoint_writes` / `checkpoint_blobs` 等表，
放在独立 schema（默认 `langgraph`）里，与业务表隔离（架构 §6 末注）。

降级顺序：PostgreSQL → SQLite → 内存。每次降级都会在日志里写明原因，
`backend` 记录的是真正生效的后端（供 /health 展示）。
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from app.infra.config import get_settings

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_SQLITE_PATH = _DATA_DIR / "checkpoints.sqlite"
# schema 名会拼进 search_path / CREATE SCHEMA，先校验成合法标识符
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CheckpointerHolder:
    """进程级单例：持有 saver 与连接池，供 FastAPI lifespan 管理。"""

    def __init__(self) -> None:
        self.saver = None
        self.backend: str = "none"
        self._pool = None
        self._conn = None

    async def startup(self) -> None:
        if self.saver is not None:
            return
        s = get_settings()
        try:
            await self._startup_postgres(s)
            self.backend = "postgres"
        except Exception as e:  # noqa: BLE001
            logger.warning("Postgres checkpointer 不可用（%s），退化为 SQLite", e)
            await self._startup_sqlite()
        logger.info("checkpointer backend=%s", self.backend)

    async def _startup_postgres(self, s) -> None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg import AsyncConnection, sql
        from psycopg_pool import AsyncConnectionPool

        schema = s.langgraph_schema
        if not _SCHEMA_RE.match(schema):
            raise ValueError(f"非法的 LANGGRAPH_SCHEMA：{schema!r}")

        dsn = s.psycopg_dsn
        # langgraph-checkpoint-postgres 3.x 的 AsyncPostgresSaver 不再接受 schema=，
        # 改为在连接参数里设 search_path，表仍落在独立 schema 内。
        async with await AsyncConnection.connect(dsn, autocommit=True) as conn:
            await conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
            )

        self._pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=2,
            max_size=10,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "options": f"-c search_path={schema},public",
            },
        )
        await self._pool.open()
        try:
            saver = AsyncPostgresSaver(self._pool)
            await saver.setup()
        except Exception:
            await self._pool.close()
            self._pool = None
            raise
        self.saver = saver

    async def _startup_sqlite(self) -> None:
        try:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(str(_SQLITE_PATH))
            self.saver = AsyncSqliteSaver(self._conn)
            await self.saver.setup()
            self.backend = "sqlite"
        except Exception as e:  # noqa: BLE001
            logger.warning("SQLite checkpointer 也不可用（%s），使用内存 saver", e)
            if self._conn is not None:
                await self._conn.close()
                self._conn = None
            from langgraph.checkpoint.memory import InMemorySaver

            self.saver = InMemorySaver()
            self.backend = "memory"

    async def shutdown(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        self.saver = None
        self.backend = "none"


checkpointer_holder = CheckpointerHolder()


@asynccontextmanager
async def checkpointer_lifespan():
    await checkpointer_holder.startup()
    try:
        yield checkpointer_holder.saver
    finally:
        await checkpointer_holder.shutdown()


def get_checkpointer():
    return checkpointer_holder.saver
