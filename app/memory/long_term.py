"""长期记忆：基于 mem0 框架的统一记忆管理 + PostgreSQL 归档。

mem0 提供：
- 自动从对话中提取事实、偏好、术语
- 向量化存储（Qdrant/hosted）
- 语义检索与时间衰减
- 用户/会话/Agent 多级记忆

PostgreSQL 仍保留 conversations / messages 表用于归档与审计追溯。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from functools import lru_cache

from app.infra.config import get_settings
from app.infra.db import session_scope
from app.infra.models import Conversation, Message

logger = logging.getLogger(__name__)


# ── mem0 客户端初始化 ────────────────────────────────────────
@lru_cache(maxsize=1)
def get_mem0_client():
    """进程级单例：根据配置返回 mem0 客户端。"""
    from mem0 import Memory

    s = get_settings()

    if s.mem0_mode == "hosted":
        # 托管模式：使用 mem0 官方服务
        if not s.mem0_api_key:
            logger.warning("mem0 托管模式需要 MEM0_API_KEY，降级为空实现")
            return None
        config = {
            "vector_store": {
                "provider": "mem0",
                "config": {"api_key": s.mem0_api_key}
            }
        }
    else:
        # 自建模式：使用本地 Qdrant + LLM
        config = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "url": s.mem0_qdrant_url,
                    "api_key": s.mem0_qdrant_api_key or None,
                    "collection_name": "reviewagent_memory",
                }
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": s.classifier_model,  # 用轻量模型提取记忆
                    "api_key": s.siliconflow_api_key,
                    "base_url": s.siliconflow_base_url,
                }
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": s.embedding_model,
                    "api_key": s.siliconflow_api_key,
                    "base_url": s.siliconflow_base_url,
                }
            }
        }

    try:
        return Memory.from_config(config)
    except Exception as e:
        logger.warning("mem0 初始化失败：%s，记忆功能降级", e)
        return None


# ── 基于 mem0 的记忆操作 ────────────────────────────────────────
async def save_memory_facts(
    user_id: str,
    facts: list[dict],
    audit_ctx_id: str | None = None,
) -> int:
    """把 persist 节点提炼出的结构化事实直接写入 mem0（不再二次抽取）。

    Args:
        user_id: 用户标识
        facts: [{"fact_type": "...", "content": "...", "importance": 0.5}]
        audit_ctx_id: 审计项目 ID（可选）

    Returns:
        成功写入的条数
    """
    client = get_mem0_client()
    if client is None:
        return 0

    saved = 0
    for fact in facts:
        try:
            meta = {
                "audit_ctx_id": audit_ctx_id or "",
                "fact_type": fact.get("fact_type", ""),
                "importance": fact.get("importance", 0.5),
            }
            client.add(fact["content"], user_id=user_id, metadata=meta, infer=False)
            saved += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("mem0.add(事实) 失败：%s", e)
    logger.info("长期记忆事实写入 user=%s 条数=%d", user_id, saved)
    return saved


async def load_memory_digest(
    user_id: str,
    query: str | None = None,
    audit_ctx_id: str | None = None,
    limit: int = 10,
) -> str:
    """检索相关记忆并格式化为 prompt 注入块。

    Args:
        user_id: 用户标识
        query: 检索查询（通常是当前问题），不提供则返回最近记忆
        audit_ctx_id: 审计项目 ID 过滤
        limit: 最多返回的记忆条数

    Returns:
        格式化的记忆文本块
    """
    client = get_mem0_client()
    if client is None:
        return "（记忆服务不可用）"

    try:
        filters = {"audit_ctx_id": audit_ctx_id} if audit_ctx_id else None
        # mem0.search() 基于语义相似度 + 时间衰减检索
        memories = client.search(
            query=query or "",
            user_id=user_id,
            limit=limit,
            filters=filters,
        )

        if not memories or not memories.get("results"):
            return "（暂无相关记忆）"

        lines: list[str] = []
        for m in memories["results"][:limit]:
            memory_text = m.get("memory", "").strip()
            score = m.get("score", 0.0)
            if memory_text:
                lines.append(f"- {memory_text} (相关度: {score:.2f})")

        return "\n".join(lines) if lines else "（暂无相关记忆）"

    except Exception as e:  # noqa: BLE001
        logger.warning("mem0.search 失败：%s", e)
        return "（记忆检索失败）"


async def get_all_memories(
    user_id: str,
    audit_ctx_id: str | None = None,
) -> list[dict]:
    """获取用户的全部记忆（用于展示/管理）。"""
    client = get_mem0_client()
    if client is None:
        return []

    try:
        result = client.get_all(user_id=user_id)
        memories = result.get("results", [])

        # 按审计项目过滤
        if audit_ctx_id:
            memories = [
                m for m in memories
                if m.get("metadata", {}).get("audit_ctx_id") == audit_ctx_id
            ]

        return memories
    except Exception as e:  # noqa: BLE001
        logger.warning("mem0.get_all 失败：%s", e)
        return []


async def delete_memory(memory_id: str) -> bool:
    """删除指定记忆。"""
    client = get_mem0_client()
    if client is None:
        return False

    try:
        client.delete(memory_id)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("mem0.delete 失败：%s", e)
        return False


# ── PostgreSQL 会话归档（保留原有功能用于审计追溯）────────────────
async def ensure_conversation(
    conversation_id: str | uuid.UUID | None,
    user_id: str,
    title: str | None = None,
    audit_ctx_id: str | uuid.UUID | None = None,
) -> uuid.UUID:
    """按需创建会话记录，返回会话 ID。"""
    ctx_uuid = _as_uuid(audit_ctx_id)
    conv_uuid = _as_uuid(conversation_id)

    async with session_scope() as db:
        if conv_uuid is not None:
            exists = await db.get(Conversation, conv_uuid)
            if exists is not None:
                return exists.id

        conv = Conversation(
            id=conv_uuid or uuid.uuid4(),
            user_id=user_id,
            audit_ctx_id=ctx_uuid,
            title=(title or "")[:255] or None,
        )
        db.add(conv)
        await db.flush()
        return conv.id


async def archive_message(
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    citations: list | None = None,
    verification: dict | None = None,
    answer: dict | None = None,
) -> None:
    """归档单条消息到 PostgreSQL（审计日志）。"""
    async with session_scope() as db:
        db.add(
            Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                citations=citations,
                verification=verification,
                answer=answer,
            )
        )


def _as_uuid(value) -> uuid.UUID | None:
    """将字符串或 UUID 对象转为 UUID，失败返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        logger.warning("非法 UUID：%r，忽略", value)
        return None
