"""全部 LLM 输出 / 状态模型。语义校验在这里落地（架构 §7）。"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ============================================================
# 分类
# ============================================================


class QuestionType(str, Enum):
    FACTUAL = "factual"
    ANALYTICAL = "analytical"
    CLARIFY = "clarify"
    OUT_OF_SCOPE = "out_of_scope"


class ClassificationResult(BaseModel):
    question_type: QuestionType
    rewritten_question: str = Field(description="归一化后的问题，供检索使用")
    sub_questions: list[str] = Field(
        default_factory=list, description="拆解后的子问题，单一问题时留空"
    )
    search_queries: list[str] = Field(
        default_factory=list, description="检索查询变体，2~4 个"
    )
    needs_clarification: bool = False
    clarification_question: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _normalize(self) -> "ClassificationResult":
        # 去空白、限制数量
        self.sub_questions = [q.strip() for q in self.sub_questions if q and q.strip()][:5]
        # 去重、去空白、限制数量
        seen: set[str] = set()
        queries: list[str] = []
        for q in self.search_queries:
            q = (q or "").strip()
            if q and q not in seen:
                seen.add(q)
                queries.append(q)
        if not queries:
            queries = [self.rewritten_question]
        self.search_queries = queries[:4]

        if self.question_type is QuestionType.CLARIFY and not self.needs_clarification:
            self.needs_clarification = True
        if self.question_type is QuestionType.OUT_OF_SCOPE:
            self.search_queries = self.search_queries[:0] or []
        return self


# ============================================================
# 检索
# ============================================================


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    page: int | None = None
    text: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def section(self) -> str | None:
        v = self.metadata.get("section")
        return str(v) if v else None


class SufficiencyResult(BaseModel):
    is_sufficient: bool
    reason: str = ""
    missing_aspects: list[str] = Field(default_factory=list)
    next_queries: list[str] = Field(default_factory=list)


# ============================================================
# 答案与引用
# ============================================================

_CITATION_MARK = re.compile(r"\[(\d+)\]")


class Citation(BaseModel):
    index: int = Field(ge=1, description="正文中的 [n] 角标")
    chunk_id: str
    document_id: str
    page: int | None = None
    quote: str = Field(description="被引用的原文关键句")

    @model_validator(mode="after")
    def _quote_not_empty(self) -> "Citation":
        self.quote = (self.quote or "").strip()
        if not self.quote:
            raise ValueError("quote 不能为空")
        return self


class AnswerWithCitations(BaseModel):
    answer_markdown: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _citations_must_exist(self) -> "AnswerWithCitations":
        if not self.citations:
            raise ValueError("citations 不能为空：禁止无引用回答")
        return self

    @model_validator(mode="after")
    def _index_consistency(self) -> "AnswerWithCitations":
        """正文角标集合必须与 citations.index 集合完全一致。"""
        body_marks = {int(n) for n in _CITATION_MARK.findall(self.answer_markdown)}
        cited = {c.index for c in self.citations}
        if not body_marks:
            raise ValueError("answer_markdown 中没有任何 [n] 角标")
        if body_marks != cited:
            missing = sorted(body_marks - cited)
            unused = sorted(cited - body_marks)
            raise ValueError(
                f"角标与 citations 不一致：正文有但未定义 {missing}，"
                f"定义了但正文未使用 {unused}"
            )
        return self

    @model_validator(mode="after")
    def _index_unique(self) -> "AnswerWithCitations":
        seen: set[int] = set()
        for c in self.citations:
            if c.index in seen:
                raise ValueError(f"citation index 重复：{c.index}")
            seen.add(c.index)
        return self


def validate_quote_grounding(
    answer: AnswerWithCitations,
    chunks: list[RetrievedChunk],
    *,
    fuzzy_min_ratio: float = 0.85,
) -> list[str]:
    """校验 quote 是否真实出自召回片段（防引用伪造）。

    与 `RetrievedChunk` 绑定的检查放在函数里，因为 Pydantic 模型看不到检索结果。
    返回问题列表；空列表表示全部通过。

    策略：先做严格子串匹配（忽略空白归一化）；不通过时退化为
    「最相似窗口」模糊匹配，避免模型只改了标点/空格就被判死。
    """
    by_id = {c.chunk_id: c for c in chunks}
    problems: list[str] = []

    for cit in answer.citations:
        chunk = by_id.get(cit.chunk_id)
        if chunk is None:
            problems.append(
                f"[{cit.index}] chunk_id={cit.chunk_id} 不在检索结果中"
            )
            continue

        haystack = _normalize_ws(chunk.text)
        needle = _normalize_ws(cit.quote)
        if not needle:
            problems.append(f"[{cit.index}] quote 为空")
            continue
        if needle in haystack:
            continue
        ratio = _best_window_ratio(needle, haystack)
        if ratio < fuzzy_min_ratio:
            problems.append(
                f"[{cit.index}] quote 与片段 {cit.chunk_id} 不匹配（相似度 {ratio:.2f}）"
            )
    return problems


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _best_window_ratio(needle: str, haystack: str, step: int = 4) -> float:
    """粗粒度滑动窗口相似度，够用且便宜。"""
    from difflib import SequenceMatcher

    n = len(needle)
    if n == 0 or n > len(haystack):
        ratio = SequenceMatcher(None, needle, haystack).ratio() if haystack else 0.0
        return ratio
    best = 0.0
    for start in range(0, max(1, len(haystack) - n + 1), step):
        window = haystack[start : start + n]
        r = SequenceMatcher(None, needle, window).ratio()
        if r > best:
            best = r
            if best >= 0.99:
                break
    return best


# ============================================================
# 验证
# ============================================================


class CitationCheck(BaseModel):
    citation_id: str = ""
    citation_index: int = Field(ge=1)
    verdict: Literal["supported", "unsupported", "partially_supported"]
    reason: str = ""


class VerificationResult(BaseModel):
    overall: Literal["pass", "fixable", "unfixable"] = "unfixable"
    checks: list[CitationCheck] = Field(default_factory=list)
    fix_instruction: str | None = None

    @model_validator(mode="after")
    def _derive_overall(self) -> "VerificationResult":
        """overall 由逐条结论推出，模型给的值只在 checks 为空时保留。

        只有 unsupported 视为不通过；partially_supported 视为可接受（由调用方以 caveat 形式呈现）。
        """
        if not self.checks:
            return self
        bad = [c for c in self.checks if c.verdict == "unsupported"]
        if not bad:
            self.overall = "pass"
            self.fix_instruction = None
        elif len(bad) > max(1, len(self.checks) // 2):
            self.overall = "unfixable"
        else:
            self.overall = "fixable"
        return self


# ============================================================
# 最终输出
# ============================================================

AnswerStatus = Literal[
    "ok",                      # 生成 + 验证通过
    "degraded",                # 生成成功但引用未完全核验
    "insufficient_evidence",   # 证据不足
    "needs_clarification",     # 需反问
    "out_of_scope",            # 超范围
    "error",                   # 系统/校验失败
]


class MemoryProposal(BaseModel):
    """本轮提炼出的长期记忆候选，等待用户确认后才写入。"""

    id: str
    fact_type: str
    content: str
    importance: float = 0.5


class FinalAnswer(BaseModel):
    answer_id: str
    status: AnswerStatus
    answer_markdown: str = ""
    citations: list[Citation] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    caveats: list[str] = Field(default_factory=list)
    verification: VerificationResult | None = None
    evidence: list[RetrievedChunk] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    # 长期记忆候选：需要用户确认，确认前不写入长期记忆
    memory_proposals: list[MemoryProposal] = Field(default_factory=list)
    # 会话标识：多轮追问时回传给客户端
    thread_id: str = ""
    conversation_id: str = ""
    latency_ms: int | None = None
