"""长期记忆候选：提炼出的记忆先待确认，用户确认后才写入 mem0。

候选存放在 memory_proposals 表，状态只能从 pending 流转为 accepted / rejected，且只能处理一次。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from app.infra.db import session_scope
from app.infra.models import MemoryProposalRecord
from app.memory.long_term import save_memory_facts
from app.schemas.models import MemoryProposal

logger = logging.getLogger(__name__)


class ProposalNotFound(Exception):
    """候选不存在，或不属于该用户。"""


class ProposalAlreadyDecided(Exception):
    """候选已经被确认或拒绝过。"""


class ProposalSaveError(Exception):
    """用户确认后写入 mem0 失败；候选保持 pending，可以重试。"""


async def create_proposals(
    *,
    user_id: str,
    audit_ctx_id: str | None,
    answer_id: str,
    facts: Sequence[dict[str, Any]],
) -> list[MemoryProposal]:
    """把提炼出的事实存为 pending 候选，返回给前端展示。"""
    if not facts:
        return []

    records: list[MemoryProposalRecord] = []
    async with session_scope() as db:
        for f in facts:
            record = MemoryProposalRecord(
                id=uuid.uuid4(),
                answer_id=answer_id,
                user_id=user_id,
                audit_ctx_id=_uuid_or_none(audit_ctx_id),
                fact_type=str(f.get("fact_type") or "")[:32],
                content=f["content"],
                importance=float(f.get("importance", 0.5)),
                status="pending",
            )
            db.add(record)
            records.append(record)

    return [
        MemoryProposal(
            id=str(r.id),
            fact_type=r.fact_type,
            content=r.content,
            importance=r.importance,
        )
        for r in records
    ]


async def decide_proposal(*, proposal_id: str, user_id: str, accept: bool) -> str:
    """用户确认（accept=True）或拒绝候选，返回最终状态。

    确认时先写 mem0，写入成功才标记为 accepted；写入失败则抛错，候选保持 pending。
    """
    pid = _uuid_or_none(proposal_id)
    if pid is None:
        raise ProposalNotFound(proposal_id)

    async with session_scope() as db:
        # 行锁：防止同一候选被并发确认两次
        stmt = select(MemoryProposalRecord).where(MemoryProposalRecord.id == pid).with_for_update()
        record = (await db.execute(stmt)).scalar_one_or_none()
        if record is None or record.user_id != user_id:
            raise ProposalNotFound(proposal_id)
        if record.status != "pending":
            raise ProposalAlreadyDecided(record.status)

        if accept:
            saved = await save_memory_facts(
                user_id=record.user_id,
                facts=[
                    {
                        "fact_type": record.fact_type,
                        "content": record.content,
                        "importance": record.importance,
                    }
                ],
                audit_ctx_id=str(record.audit_ctx_id) if record.audit_ctx_id else None,
            )
            if saved == 0:
                raise ProposalSaveError("长期记忆写入失败（记忆服务不可用），请稍后重试")

        record.status = "accepted" if accept else "rejected"
        record.decided_at = datetime.now(UTC)
        logger.info("长期记忆候选 %s 已%s", proposal_id, "记住" if accept else "拒绝")
        return record.status


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None
