"""图编排测试：用 mock 的 LLM 与检索步骤跑通全部分支。

覆盖：正常通过、修正后通过、连续失败降级、证据不足、澄清、超范围、生成失败；
以及检索 Agent 的补查循环、验证 Agent 的溯源短路。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents import retrieval, verification
from app.graph.builder import build_graph
from app.graph.routing import (
    route_after_answer,
    route_by_sufficiency,
    route_by_type,
    route_by_verification,
)
from app.graph.state import AgentState
from app.infra.config import get_settings
from app.schemas.models import (
    AnswerWithCitations,
    Citation,
    CitationCheck,
    ClassificationResult,
    FinalAnswer,
    QuestionType,
    RetrievedChunk,
    SufficiencyResult,
    VerificationResult,
)

CHUNK_TEXT = "采购金额超过一百万元（含）的，须报总经理办公会审批。"
QUOTE = "采购金额超过一百万元（含）的，须报总经理办公会审批"


def _answer(quote: str = QUOTE, confidence: str = "high") -> AnswerWithCitations:
    return AnswerWithCitations(
        answer_markdown="采购金额超过一百万元须报总经理办公会审批[1]。",
        citations=[
            Citation(index=1, chunk_id="c1", document_id="doc1", page=3, quote=quote)
        ],
        confidence=confidence,  # type: ignore[arg-type]
    )


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(chunk_id="c1", document_id="doc1", page=3, text=CHUNK_TEXT, score=0.9)


class Harness:
    """用假实现替换各 Agent / 节点，验证编排与路由。验证 Agent 保留真实的溯源逻辑。"""

    def __init__(
        self,
        *,
        qtype: QuestionType = QuestionType.FACTUAL,
        sufficient: bool = True,
        answer_quotes: list[str] | None = None,
        verdicts: list[list[str]] | None = None,
        generate_fails: bool = False,
    ):
        self.qtype = qtype
        self.sufficient = sufficient
        self.answer_quotes = answer_quotes or [QUOTE]
        # verdicts: 每一轮验证的逐条结论序列
        self.verdicts = verdicts or [["supported"]]
        self.generate_fails = generate_fails
        self.calls: list[str] = []

    # ---- Agent 替身 ----
    def decompose(self, state: AgentState) -> dict:
        self.calls.append("decompose")
        return {
            "classification": ClassificationResult(
                question_type=self.qtype,
                rewritten_question="审批权限是多少",
                sub_questions=[],
                search_queries=["审批权限"] if self.qtype is not QuestionType.OUT_OF_SCOPE else [],
                needs_clarification=self.qtype is QuestionType.CLARIFY,
                clarification_question="请说明是哪份采购制度？"
                if self.qtype is QuestionType.CLARIFY
                else None,
                confidence=0.9,
            )
        }

    def retrieval(self, state: AgentState) -> dict:
        self.calls.append("retrieval")
        suf = (
            SufficiencyResult(is_sufficient=True, reason="命中直接条款")
            if self.sufficient
            else SufficiencyResult(is_sufficient=False, reason="只命中了目录")
        )
        return {
            "retrieval_round": state.retrieval_round + 1,
            "used_queries": [*state.used_queries, "审批权限"],
            "retrieved_chunks": [_chunk()],
            "sufficiency": suf,
        }

    def answer(self, state: AgentState) -> dict:
        self.calls.append("answer")
        if self.generate_fails:
            return {"error": "结构化输出不合规", "draft_answer": None}
        return {"draft_answer": _answer(self.answer_quotes[0]), "verification": None}

    def regenerate(self, state: AgentState) -> dict:
        self.calls.append("regenerate")
        n = state.regeneration_count + 1
        quote = self.answer_quotes[min(n, len(self.answer_quotes) - 1)]
        return {
            "draft_answer": _answer(quote),
            "verification": None,
            "regeneration_count": n,
        }

    def verify(self, state: AgentState) -> dict:
        self.calls.append(f"verify#{state.regeneration_count}")
        batch = self.verdicts[min(state.regeneration_count, len(self.verdicts) - 1)]

        def fake_check(question, answer, citation, chunk):
            verdict = batch[citation.index - 1]
            return CitationCheck(
                citation_id=citation.chunk_id,
                citation_index=citation.index,
                verdict=verdict,  # type: ignore[arg-type]
                reason=f"mock {verdict}",
            )

        # 只替换逐条 LLM 核验，溯源检查走真实逻辑
        with patch.object(verification, "_check_one", side_effect=fake_check):
            return verification.verification_agent(state)

    def build(self):
        return build_graph(
            InMemorySaver(),
            overrides={
                "decompose": self.decompose,
                "retrieval": self.retrieval,
                "answer": self.answer,
                "regenerate": self.regenerate,
                "verify": self.verify,
                "persist": _noop_async,
            },
        )


async def _noop_async(state: AgentState) -> dict:
    return {}


async def _run(harness: Harness, question: str = "一百万元以上采购谁审批？") -> FinalAnswer:
    app = harness.build()
    out: dict[str, Any] = await app.ainvoke(
        AgentState(question=question, user_id="u1", thread_id="t1"),
        config={"configurable": {"thread_id": "t1"}},
    )
    final = out.get("final_answer")
    assert final is not None
    return final


class TestHappyPath:
    async def test_pass_produces_ok_answer_with_verification(self):
        final = await _run(Harness())
        assert final.status == "ok"
        assert final.confidence == "high"
        assert final.citations and final.citations[0].chunk_id == "c1"
        assert final.verification is not None
        assert final.verification.overall == "pass"
        assert final.evidence


class TestInsufficientEvidence:
    async def test_insufficient_goes_straight_to_finalize(self):
        h = Harness(sufficient=False)
        final = await _run(h)
        assert final.status == "insufficient_evidence"
        assert final.citations == []
        assert "证据不足" in final.answer_markdown
        assert "answer" not in h.calls


class TestClarify:
    async def test_clarify_asks_back_without_retrieval(self):
        h = Harness(qtype=QuestionType.CLARIFY)
        final = await _run(h)
        assert final.status == "needs_clarification"
        assert "采购制度" in final.answer_markdown
        assert "retrieval" not in h.calls


class TestOutOfScope:
    async def test_out_of_scope_skips_retrieval(self):
        h = Harness(qtype=QuestionType.OUT_OF_SCOPE)
        final = await _run(h, question="今天天气怎么样")
        assert final.status == "out_of_scope"
        assert "retrieval" not in h.calls
        assert "answer" not in h.calls


class TestFabricatedQuote:
    async def test_bad_quote_is_fixed_by_regenerate(self):
        h = Harness(
            answer_quotes=["采购金额超过五百万元的，须报董事会审批", QUOTE],
            verdicts=[["supported"]],
        )
        final = await _run(h)
        assert "regenerate" in h.calls
        assert final.status == "ok"

    async def test_persistent_bad_quote_degrades(self):
        h = Harness(answer_quotes=["完全编造的引用内容"])
        final = await _run(h)
        assert final.status == "degraded"
        assert final.confidence == "low"
        assert any("未能完全核验" in c for c in final.caveats)
        assert h.calls.count("regenerate") <= 2


class TestVerificationFailure:
    async def test_partial_passes_without_regeneration(self):
        # partially_supported 不阻断回答：直接通过，只在 caveats 中提示
        h = Harness(verdicts=[["partially_supported"]])
        final = await _run(h)
        assert final.status == "ok"
        assert h.calls.count("regenerate") == 0
        assert any("部分支持" in c for c in final.caveats)

    async def test_fixable_then_pass(self):
        # unsupported 触发修正，第二轮通过
        h = Harness(verdicts=[["unsupported"], ["supported"]])
        final = await _run(h)
        assert final.status == "ok"
        assert h.calls.count("regenerate") == 1

    async def test_unfixable_degrades_with_reasons(self):
        h = Harness(verdicts=[["unsupported"]])
        final = await _run(h)
        assert final.status == "degraded"
        assert any("[1]" in c for c in final.caveats)


class TestGenerationFailure:
    async def test_no_draft_reports_error_not_evidence_gap(self):
        h = Harness(generate_fails=True)
        final = await _run(h)
        assert final.status == "error"
        assert "未能生成合规回答" in final.answer_markdown
        assert final.evidence, "生成失败也应把已检索材料交给审计方"
        assert "verify" not in " ".join(h.calls)


class TestRetrievalAgentLoop:
    async def test_loop_stops_at_round_limit(self, monkeypatch):
        rounds: list[int] = []

        async def fake_recall(state: AgentState) -> dict:
            rounds.append(state.retrieval_round)
            return {"retrieval_round": state.retrieval_round + 1, "sufficiency": None}

        def fake_judge(state: AgentState) -> dict:
            return {"sufficiency": SufficiencyResult(
                is_sufficient=False, reason="不足", next_queries=["补查"])}

        monkeypatch.setattr(retrieval, "recall", fake_recall)
        monkeypatch.setattr(retrieval, "judge", fake_judge)

        out = await retrieval.retrieval_agent(AgentState(question="q"))
        assert len(rounds) == get_settings().max_retrieval_rounds
        assert out["retrieval_round"] == len(rounds)

    async def test_loop_stops_when_sufficient(self, monkeypatch):
        rounds: list[int] = []

        async def fake_recall(state: AgentState) -> dict:
            rounds.append(state.retrieval_round)
            return {"retrieval_round": state.retrieval_round + 1, "sufficiency": None}

        def fake_judge(state: AgentState) -> dict:
            return {"sufficiency": SufficiencyResult(is_sufficient=True, reason="够了")}

        monkeypatch.setattr(retrieval, "recall", fake_recall)
        monkeypatch.setattr(retrieval, "judge", fake_judge)

        out = await retrieval.retrieval_agent(AgentState(question="q"))
        assert len(rounds) == 1
        assert out["sufficiency"].is_sufficient


class TestVerificationAgent:
    async def test_bad_quote_rejected_without_llm_call(self, monkeypatch):
        def must_not_call(*_args, **_kwargs):
            raise AssertionError("溯源失败时不应调用 LLM 核验")

        monkeypatch.setattr(verification, "_check_one", must_not_call)
        state = AgentState(
            question="q",
            draft_answer=_answer("完全编造的引用内容"),
            retrieved_chunks=[_chunk()],
        )
        out = verification.verification_agent(state)
        assert out["verification"].overall == "fixable"
        assert "不在原文片段中" in (out["verification"].fix_instruction or "")


@pytest.mark.parametrize(
    "qtype,expected",
    [
        (QuestionType.FACTUAL, "in_scope"),
        (QuestionType.ANALYTICAL, "in_scope"),
        (QuestionType.CLARIFY, "clarify"),
        (QuestionType.OUT_OF_SCOPE, "out_of_scope"),
    ],
)
def test_route_by_type(qtype, expected):
    state = AgentState(
        question="q",
        classification=ClassificationResult(
            question_type=qtype, rewritten_question="q", search_queries=["x"]
        ),
    )
    assert route_by_type(state) == expected


def test_route_by_type_without_classification():
    assert route_by_type(AgentState(question="q")) == "out_of_scope"


def test_route_by_sufficiency_only_reads_final_verdict():
    sufficient = AgentState(question="q", sufficiency=SufficiencyResult(is_sufficient=True))
    missing = AgentState(question="q", sufficiency=SufficiencyResult(is_sufficient=False))
    assert route_by_sufficiency(sufficient) == "sufficient"
    assert route_by_sufficiency(missing) == "insufficient"
    assert route_by_sufficiency(AgentState(question="q")) == "insufficient"


def test_route_after_answer_gives_up_without_draft():
    assert route_after_answer(AgentState(question="q")) == "give_up"
    assert route_after_answer(AgentState(question="q", draft_answer=_answer())) == "verify"


def test_route_by_verification_respects_regeneration_limit():
    limit = get_settings().max_regeneration
    fixable = VerificationResult(overall="fixable", checks=[], fix_instruction="x")
    assert route_by_verification(AgentState(question="q", verification=fixable)) == "fixable"
    assert route_by_verification(
        AgentState(question="q", verification=fixable, regeneration_count=limit)
    ) == "unfixable"
