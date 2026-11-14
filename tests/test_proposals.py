"""长期记忆候选：persist 只产出待确认候选，确认前不写入长期记忆。"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes import persist
from app.graph.state import AgentState
from app.memory.proposals import ProposalNotFound, create_proposals, decide_proposal
from app.schemas.models import FinalAnswer, MemoryProposal


def _state() -> AgentState:
    final = FinalAnswer(
        answer_id="ans-1",
        status="ok",
        answer_markdown="采购金额超过一百万元须报总经理办公会审批[1]。",
        confidence="high",
    )
    return AgentState(question="一百万元以上采购谁审批？", user_id="u1", final_answer=final)


def test_persist_returns_proposals_and_never_writes_long_term_memory():
    proposal = MemoryProposal(id="p1", fact_type="terminology", content="总经理办公会审批")
    extraction = persist.MemoryExtraction(
        facts=[persist.ExtractedFact(fact_type="terminology", content="总经理办公会审批")]
    )

    with (
        patch.object(persist, "ensure_conversation", AsyncMock(return_value=uuid.uuid4())),
        patch.object(persist, "archive_message", AsyncMock()),
        patch.object(persist, "build_chat_model", lambda role: None),
        patch.object(persist, "structured_llm_call", lambda *a, **k: extraction),
        patch.object(persist, "create_proposals", AsyncMock(return_value=[proposal])) as create,
    ):
        update = asyncio.run(persist.persist_node(_state()))

    create.assert_awaited_once()
    assert update["final_answer"].memory_proposals == [proposal]
    # 节点本身不再直接写长期记忆
    assert not hasattr(persist, "save_memory_facts")


def test_no_facts_means_no_proposals():
    extraction = persist.MemoryExtraction(facts=[])
    with (
        patch.object(persist, "ensure_conversation", AsyncMock(return_value=uuid.uuid4())),
        patch.object(persist, "archive_message", AsyncMock()),
        patch.object(persist, "build_chat_model", lambda role: None),
        patch.object(persist, "structured_llm_call", lambda *a, **k: extraction),
    ):
        update = asyncio.run(persist.persist_node(_state()))
    assert update["final_answer"].memory_proposals == []


def test_empty_facts_skip_database():
    assert asyncio.run(
        create_proposals(user_id="u1", audit_ctx_id=None, answer_id="a", facts=[])
    ) == []


def test_unknown_proposal_id_is_not_found_without_database():
    with pytest.raises(ProposalNotFound):
        asyncio.run(decide_proposal(proposal_id="not-a-uuid", user_id="u1", accept=True))
