"""Schema 语义校验测试：这些规则是防幻觉的第一道闸门，必须覆盖。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.models import (
    AnswerWithCitations,
    Citation,
    CitationCheck,
    ClassificationResult,
    QuestionType,
    RetrievedChunk,
    SufficiencyResult,
    VerificationResult,
    validate_quote_grounding,
)


def _cit(index: int, chunk_id: str = "c1", quote: str = "审批金额超过一百万元") -> Citation:
    return Citation(index=index, chunk_id=chunk_id, document_id="doc1", page=3, quote=quote)


class TestAnswerWithCitations:
    def test_accepts_consistent_marks(self):
        ans = AnswerWithCitations(
            answer_markdown="审批权限为一百万元[1]。",
            citations=[_cit(1)],
            confidence="high",
        )
        assert ans.citations[0].index == 1

    def test_rejects_empty_citations(self):
        with pytest.raises(ValidationError, match="禁止无引用回答"):
            AnswerWithCitations(answer_markdown="没有引用的回答。", citations=[])

    def test_rejects_mark_without_citation(self):
        with pytest.raises(ValidationError, match="不一致"):
            AnswerWithCitations(answer_markdown="结论见[1]与[2]。", citations=[_cit(1)])

    def test_rejects_citation_not_used_in_body(self):
        with pytest.raises(ValidationError, match="不一致"):
            AnswerWithCitations(
                answer_markdown="结论见[1]。", citations=[_cit(1), _cit(2)]
            )

    def test_rejects_missing_marks_entirely(self):
        with pytest.raises(ValidationError, match="没有任何"):
            AnswerWithCitations(answer_markdown="平铺直叙没有角标。", citations=[_cit(1)])

    def test_rejects_duplicate_index(self):
        with pytest.raises(ValidationError, match="重复"):
            AnswerWithCitations(
                answer_markdown="甲[1]乙[1]。",
                citations=[_cit(1), _cit(1)],
            )

    def test_multi_citation_single_sentence(self):
        ans = AnswerWithCitations(
            answer_markdown="综合来看结论成立[1][2]。",
            citations=[_cit(1), _cit(2)],
        )
        assert len(ans.citations) == 2


class TestQuoteGrounding:
    CHUNK = RetrievedChunk(
        chunk_id="c1",
        document_id="doc1",
        page=3,
        text="第三章 审批权限\n采购金额超过一百万元（含）的，须报总经理办公会审批。",
        score=0.9,
    )

    def test_exact_quote_passes(self):
        ans = AnswerWithCitations(
            answer_markdown="超过一百万元须报总经理办公会审批[1]。",
            citations=[_cit(1, quote="采购金额超过一百万元（含）的，须报总经理办公会审批")],
        )
        assert validate_quote_grounding(ans, [self.CHUNK]) == []

    def test_whitespace_difference_tolerated(self):
        ans = AnswerWithCitations(
            answer_markdown="须报总经理办公会审批[1]。",
            citations=[_cit(1, quote="采购金额 超过一百万元（含）的，\n须报总经理办公会审批")],
        )
        assert validate_quote_grounding(ans, [self.CHUNK]) == []

    def test_fabricated_quote_rejected(self):
        ans = AnswerWithCitations(
            answer_markdown="须报董事会审批[1]。",
            citations=[_cit(1, quote="采购金额超过五百万元的，须报董事会审批")],
        )
        problems = validate_quote_grounding(ans, [self.CHUNK])
        assert problems and "不匹配" in problems[0]

    def test_unknown_chunk_rejected(self):
        ans = AnswerWithCitations(
            answer_markdown="结论[1]。",
            citations=[_cit(1, chunk_id="ghost", quote="任意文本")],
        )
        problems = validate_quote_grounding(ans, [self.CHUNK])
        assert problems and "不在检索结果中" in problems[0]


class TestClassification:
    def test_fills_query_when_missing(self):
        r = ClassificationResult(
            question_type=QuestionType.FACTUAL,
            rewritten_question="审批权限是多少",
            search_queries=[],
        )
        assert r.search_queries == ["审批权限是多少"]

    def test_dedups_and_caps_queries(self):
        r = ClassificationResult(
            question_type=QuestionType.ANALYTICAL,
            rewritten_question="x",
            search_queries=["a", "a", " b ", "c", "d", "e"],
        )
        assert r.search_queries == ["a", "b", "c", "d"]

    def test_clarify_forces_flag(self):
        r = ClassificationResult(
            question_type=QuestionType.CLARIFY,
            rewritten_question="这个怎么算",
            search_queries=["x"],
        )
        assert r.needs_clarification is True

    def test_out_of_scope_drops_queries(self):
        r = ClassificationResult(
            question_type=QuestionType.OUT_OF_SCOPE,
            rewritten_question="今天天气",
            search_queries=["天气"],
        )
        assert r.search_queries == []


class TestVerification:
    def test_all_supported_is_pass(self):
        v = VerificationResult(
            checks=[
                CitationCheck(citation_index=1, verdict="supported", reason="原文一致"),
                CitationCheck(citation_index=2, verdict="supported", reason="原文一致"),
            ]
        )
        assert v.overall == "pass"
        assert v.fix_instruction is None

    def test_single_partial_passes(self):
        # partially_supported 不阻断回答，只有 unsupported 计入不通过
        v = VerificationResult(
            checks=[
                CitationCheck(citation_index=1, verdict="supported", reason="ok"),
                CitationCheck(citation_index=2, verdict="partially_supported", reason="漏例外"),
            ]
        )
        assert v.overall == "pass"

    def test_single_unsupported_of_two_is_fixable(self):
        v = VerificationResult(
            checks=[
                CitationCheck(citation_index=1, verdict="supported", reason="ok"),
                CitationCheck(citation_index=2, verdict="unsupported", reason="无依据"),
            ]
        )
        assert v.overall == "fixable"

    def test_majority_unsupported_is_unfixable(self):
        v = VerificationResult(
            checks=[
                CitationCheck(citation_index=1, verdict="unsupported", reason="无关"),
                CitationCheck(citation_index=2, verdict="unsupported", reason="无关"),
                CitationCheck(citation_index=3, verdict="supported", reason="ok"),
            ]
        )
        assert v.overall == "unfixable"

    def test_empty_checks_keeps_model_verdict(self):
        v = VerificationResult(overall="unfixable", checks=[])
        assert v.overall == "unfixable"


def test_sufficiency_defaults():
    s = SufficiencyResult(is_sufficient=False, reason="缺文件")
    assert s.next_queries == []
    assert s.missing_aspects == []
