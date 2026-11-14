"""离线冒烟：不连 LLM / Milvus / PostgreSQL，用假 Agent 跑通图拓扑。

用途：改路由或 Agent 编排后快速回归，CI 里也能跑。
    python -m scripts.smoke_graph

拓扑直接复用 app.graph.builder.build_graph，只替换 Agent 的 LLM / 检索调用。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from app.agents import verification  # noqa: E402
from app.graph.builder import build_graph  # noqa: E402
from app.graph.state import AgentState  # noqa: E402
from app.schemas.models import (  # noqa: E402
    AnswerWithCitations,
    Citation,
    CitationCheck,
    ClassificationResult,
    QuestionType,
    RetrievedChunk,
    SufficiencyResult,
)

CHUNK = "采购金额超过一百万元（含）的，须报总经理办公会审批。"
GOOD_QUOTE = "采购金额超过一百万元（含）的，须报总经理办公会审批"
BAD_QUOTE = "采购金额超过五百万元的，须报董事会审批"


class Scenario:
    def __init__(self, *, qtype=QuestionType.FACTUAL, sufficient=True, quotes=(GOOD_QUOTE,),
                 verdicts=(("supported",),), generate_fails=False):
        self.qtype = qtype
        self.sufficient = sufficient
        self.quotes = list(quotes)
        self.verdicts = list(verdicts)
        self.generate_fails = generate_fails
        self.calls: list[str] = []

    def decompose(self, _state):
        self.calls.append("decompose")
        return {"classification": ClassificationResult(
            question_type=self.qtype,
            rewritten_question="一百万元以上采购谁审批",
            sub_questions=[],
            search_queries=[] if self.qtype is QuestionType.OUT_OF_SCOPE else ["审批权限"],
            needs_clarification=self.qtype is QuestionType.CLARIFY,
            clarification_question="请问是哪份采购制度？" if self.qtype is QuestionType.CLARIFY else None,
        )}

    def retrieval(self, state):
        self.calls.append("retrieval")
        suf = (SufficiencyResult(is_sufficient=True, reason="命中直接条款") if self.sufficient
               else SufficiencyResult(is_sufficient=False, reason="仅命中目录"))
        return {
            "retrieval_round": state.retrieval_round + 1,
            "used_queries": [*state.used_queries, "审批权限"],
            "retrieved_chunks": [RetrievedChunk(chunk_id="c1", document_id="doc1", page=3,
                                                text=CHUNK, score=0.9)],
            "sufficiency": suf,
        }

    def _answer(self, quote):
        return AnswerWithCitations(
            answer_markdown="采购金额超过一百万元须报总经理办公会审批[1]。",
            citations=[Citation(index=1, chunk_id="c1", document_id="doc1", page=3, quote=quote)],
            confidence="high",
        )

    def answer(self, _state):
        self.calls.append("answer")
        if self.generate_fails:
            return {"draft_answer": None, "error": "结构化输出不合规"}
        return {"draft_answer": self._answer(self.quotes[0]), "verification": None}

    def regenerate(self, state):
        self.calls.append("regenerate")
        n = state.regeneration_count + 1
        return {"draft_answer": self._answer(self.quotes[min(n, len(self.quotes) - 1)]),
                "verification": None, "regeneration_count": n}

    def verify(self, state):
        self.calls.append("verify")
        batch = self.verdicts[min(state.regeneration_count, len(self.verdicts) - 1)]

        def fake_check(question, answer, citation, chunk):
            verdict = batch[citation.index - 1]
            return CitationCheck(citation_id=citation.chunk_id, citation_index=citation.index,
                                 verdict=verdict, reason=f"mock {verdict}")

        # 溯源检查走真实逻辑，只替换逐条 LLM 核验
        with patch.object(verification, "_check_one", side_effect=fake_check):
            return verification.verification_agent(state)

    async def persist(self, _state):
        self.calls.append("persist")
        return {}

    def build(self):
        return build_graph(InMemorySaver(), overrides={
            "decompose": self.decompose,
            "retrieval": self.retrieval,
            "answer": self.answer,
            "regenerate": self.regenerate,
            "verify": self.verify,
            "persist": self.persist,
        })


async def run(sc: Scenario, question="一百万元以上采购谁审批？"):
    app = sc.build()
    out = await app.ainvoke(
        AgentState(question=question, user_id="u1", thread_id="t1"),
        config={"configurable": {"thread_id": "t1"}},
    )
    return out["final_answer"]


CASES = [
    ("正常通过", Scenario(), "ok", lambda s: s.calls.count("verify") == 1),
    ("伪造引用→修正成功", Scenario(quotes=(BAD_QUOTE, GOOD_QUOTE), verdicts=(("supported",),)),
     "ok", lambda s: s.calls.count("regenerate") == 1),
    ("伪造引用持续→降级", Scenario(quotes=(BAD_QUOTE,)), "degraded",
     lambda s: s.calls.count("regenerate") <= 2),
    ("证据不足", Scenario(sufficient=False), "insufficient_evidence",
     lambda s: s.calls.count("retrieval") == 1 and "answer" not in s.calls),
    ("澄清反问", Scenario(qtype=QuestionType.CLARIFY), "needs_clarification",
     lambda s: "retrieval" not in s.calls),
    ("超出范围", Scenario(qtype=QuestionType.OUT_OF_SCOPE), "out_of_scope",
     lambda s: "retrieval" not in s.calls and "answer" not in s.calls),
    ("生成失败", Scenario(generate_fails=True), "error",
     lambda s: "verify" not in s.calls),
    ("验证不通过→修正→通过", Scenario(verdicts=(("unsupported",), ("supported",))),
     "ok", lambda s: s.calls.count("regenerate") == 1),
]


async def main() -> int:
    failures = 0
    for name, sc, expect_status, extra in CASES:
        final = await run(sc)
        ok = final.status == expect_status and extra(sc)
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}: status={final.status} (期望 {expect_status}) calls={sc.calls}")
        if not ok:
            failures += 1
            print(f"       answer={final.answer_markdown[:120]}")
            print(f"       caveats={final.caveats}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} 通过")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
