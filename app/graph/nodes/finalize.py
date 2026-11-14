"""finalize 节点：汇总输出 + 归档 + 提炼长期记忆（架构 §3.3 / §4.3）。

主图的收尾节点，覆盖以下来源：
- verify 子图结束（通过 / 不可修复）：可能带草稿 + 验证结论
- retrieval 子图证据不足：无草稿
- decompose 判定为澄清型 / 超范围
"""

from __future__ import annotations

import logging
import uuid

from app.graph.nodes.clarify import build_clarify_answer
from app.graph.nodes.persist import persist_node
from app.graph.state import AgentState
from app.infra.tracing import traceable
from app.schemas.models import FinalAnswer, QuestionType

logger = logging.getLogger(__name__)


@traceable(run_type="chain", name="node.finalize")
async def finalize_node(state: AgentState) -> dict:
    update = _build_final_update(state)
    # 归档与长期记忆提炼基于本次的最终回答
    merged = state.model_copy(update=update)
    persisted = await persist_node(merged)
    return {**update, **persisted}


def _build_final_update(state: AgentState) -> dict:
    answer_id = uuid.uuid4().hex
    cls = state.classification

    # ---------- 澄清反问 ----------
    if cls is not None and cls.question_type is QuestionType.CLARIFY:
        return {"final_answer": build_clarify_answer(state, answer_id)}

    # ---------- 超范围 ----------
    if cls is not None and cls.question_type is QuestionType.OUT_OF_SCOPE:
        return {
            "final_answer": FinalAnswer(
                answer_id=answer_id,
                status="out_of_scope",
                answer_markdown=(
                    "这个问题超出了当前审计知识库的范围，我无法基于库内材料作答。\n\n"
                    "本系统只回答与审计资料（制度、合同、台账、报告等）相关的问题。"
                ),
                confidence="low",
                follow_up_questions=[
                    "你想了解的是哪份审计资料中的内容？",
                    "是否需要我列出知识库中已有的文档清单？",
                ],
            )
        }

    # ---------- 证据不足 ----------
    if state.draft_answer is None:
        return {"final_answer": _insufficient_answer(state, answer_id)}

    # ---------- 有草稿：按验证结论分级 ----------
    answer = state.draft_answer
    verification = state.verification

    if verification is not None and verification.overall == "pass":
        status, confidence = "ok", answer.confidence
        caveats = list(answer.caveats)
        # 部分支持的引用不阻断回答，但要在 caveats 中说明，并适度下调置信度
        partial = [c for c in verification.checks if c.verdict == "partially_supported"]
        if partial:
            caveats.append(
                "以下引用仅部分支持结论，请注意核对："
                + "；".join(f"[{c.citation_index}] {c.reason}" for c in partial)
            )
            if confidence == "high":
                confidence = "medium"
    elif verification is not None:
        # 降级：存在无法支撑的引用（unsupported）
        status = "degraded"
        confidence = "low"
        failed = [c for c in verification.checks if c.verdict == "unsupported"]
        caveats = list(answer.caveats) + [
            "以下引用未能完全核验，请以原始文件为准："
            + "；".join(f"[{c.citation_index}] {c.reason}" for c in failed)
        ]
    else:
        # 生成成功但未走验证（理论上不该出现）
        status, confidence = "degraded", "low"
        caveats = list(answer.caveats) + ["本次回答未经引用核验。"]

    logger.info(
        "finalize status=%s confidence=%s citations=%d",
        status,
        confidence,
        len(answer.citations),
    )
    return {
        "final_answer": FinalAnswer(
            answer_id=answer_id,
            status=status,
            answer_markdown=answer.answer_markdown,
            citations=answer.citations,
            confidence=confidence,
            caveats=caveats,
            verification=verification,
            evidence=state.retrieved_chunks,
        )
    }


def _insufficient_answer(state: AgentState, answer_id: str) -> FinalAnswer:
    suf = state.sufficiency

    # 区分两种"没有草稿"：证据本身不足 vs 生成阶段失败
    generation_failed = bool(suf and suf.is_sufficient) or bool(state.error)
    if generation_failed:
        headline = "**结论：系统未能生成合规回答。**"
        reason = (
            f"检索已获得 {len(state.retrieved_chunks)} 条相关材料，"
            f"但生成阶段的结构化输出未通过校验：{state.error or '未知原因'}"
        )
        missing: list[str] = []
    else:
        headline = "**结论：现有证据不足，无法给出可靠回答。**"
        reason = suf.reason if suf else "未能检索到足够证据。"
        missing = list(suf.missing_aspects) if suf else []

    lines = [headline, "", f"原因：{reason}"]
    if missing:
        lines += ["", "缺失的证据维度："] + [f"- {m}" for m in missing]
    if state.retrieved_chunks:
        lines += ["", "已检索到以下相关材料，供你人工查阅："]

    return FinalAnswer(
        answer_id=answer_id,
        status="error" if generation_failed else "insufficient_evidence",
        answer_markdown="\n".join(lines),
        citations=[],
        confidence="low",
        caveats=["本回答未包含任何推断性结论。"],
        verification=state.verification,
        evidence=state.retrieved_chunks,
        follow_up_questions=[
            f"是否可以补充上传相关年度/部门的文件？（已尝试检索：{'、'.join(state.used_queries[:3])}）"
        ]
        if state.used_queries and not generation_failed
        else (["是否需要重试一次？"] if generation_failed else []),
    )
