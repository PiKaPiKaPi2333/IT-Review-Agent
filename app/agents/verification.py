"""验证 Agent：先做 quote 溯源（确定性），再逐条校验引用是否蕴含答案断言。

每条引用独立成一个校验单元（并行子调用），避免长上下文互相干扰。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from app.graph.nodes.common import truncate
from app.graph.state import AgentState
from app.infra.tracing import traceable
from app.llm.client import LLMOutputError, build_chat_model, structured_llm_call
from app.llm.prompts.verifier import VERIFIER_SYSTEM, VERIFIER_USER
from app.schemas.models import (
    AnswerWithCitations,
    Citation,
    CitationCheck,
    RetrievedChunk,
    VerificationResult,
    validate_quote_grounding,
)

logger = logging.getLogger(__name__)

MAX_PARALLEL_CHECKS = 6
MAX_CHUNK_CHARS = 3000


class _SingleCheckPayload(VerificationResult):
    """单条校验复用 VerificationResult 容器，便于共用 structured_llm_call。"""


@traceable(run_type="chain", name="agent.verify")
def verification_agent(state: AgentState) -> dict:
    answer = state.draft_answer
    if answer is None:
        return {"verification": VerificationResult(overall="unfixable", checks=[],
                                                   fix_instruction="无草稿可验证")}

    # ---- 第一步：quote 溯源，确定性检查，不通过则不花 LLM 调用 ----
    problems = validate_quote_grounding(answer, state.retrieved_chunks)
    if problems:
        logger.warning("引用溯源校验未通过：%s", problems)
        return {"verification": VerificationResult(
            overall="fixable",
            checks=[],
            fix_instruction="以下引用的 quote 不在原文片段中，请改用片段中真实存在的原句：\n"
            + "\n".join(problems),
        )}

    # ---- 第二步：逐条校验断言是否被对应片段蕴含 ----
    chunks_by_id = {c.chunk_id: c for c in state.retrieved_chunks}
    question = (
        state.classification.rewritten_question if state.classification else state.question
    )

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_CHECKS) as pool:
        futures = [
            pool.submit(_check_one, question, answer, cit, chunks_by_id.get(cit.chunk_id))
            for cit in answer.citations
        ]
        checks: list[CitationCheck] = []
        for fut in futures:
            try:
                checks.append(fut.result())
            except Exception as e:  # noqa: BLE001
                logger.warning("单条引用校验失败：%s", e)

    checks.sort(key=lambda c: c.citation_index)
    result = VerificationResult(overall="pass", checks=checks)
    # 校验缺失（子调用失败）视为不能通过，降级处理
    covered = {c.citation_index for c in checks}
    missing = [c.index for c in answer.citations if c.index not in covered]
    if missing:
        result.overall = "fixable"
        result.fix_instruction = (
            f"以下引用未能完成核验，请移除或替换为可核验的引用：{missing}"
        )

    if result.overall != "pass" and not result.fix_instruction:
        bad = [c for c in checks if c.verdict == "unsupported"]
        result.fix_instruction = "\n".join(
            f"[{c.citation_index}] {c.verdict}：{c.reason}" for c in bad
        )

    logger.info(
        "验证结论 overall=%s 通过 %d/%d",
        result.overall,
        sum(1 for c in checks if c.verdict == "supported"),
        len(checks),
    )
    return {"verification": result}


def build_verify_graph(*, answer=None, regenerate=None, check=None):
    """编译验证子图：answer → check ⇄ regenerate。

    - answer：基于检索结果生成带引用草稿（默认 generate_node）
    - check：引用溯源 + 逐条蕴含校验（默认 verification_agent）
    - regenerate：按修正指令重写草稿（默认 regenerate_node）
    子图输出草稿与核验结论，由主图的 finalize 收尾。
    """
    from langgraph.graph import END, START, StateGraph

    from app.graph.nodes.generator import generate_node, regenerate_node
    from app.graph.routing import route_after_answer, route_by_verification

    g = StateGraph(AgentState)
    g.add_node("answer", answer or generate_node)
    g.add_node("check", check or verification_agent)
    g.add_node("regenerate", regenerate or regenerate_node)
    g.add_edge(START, "answer")
    g.add_conditional_edges("answer", route_after_answer, {"verify": "check", "give_up": END})
    g.add_conditional_edges(
        "check",
        route_by_verification,
        {"pass": END, "fixable": "regenerate", "unfixable": END},
    )
    g.add_conditional_edges("regenerate", route_after_answer, {"verify": "check", "give_up": END})
    return g.compile()


def _check_one(
    question: str,
    answer: AnswerWithCitations,
    citation: Citation,
    chunk: RetrievedChunk | None,
) -> CitationCheck:
    if chunk is None:
        return CitationCheck(
            citation_id=citation.chunk_id,
            citation_index=citation.index,
            verdict="unsupported",
            reason="该引用的 chunk_id 不在本次检索结果中，无法核验。",
        )

    material = (
        f"### 引用 [{citation.index}]\n"
        f"chunk_id：{citation.chunk_id}\n"
        f"出处：{chunk.metadata.get('filename', chunk.document_id)}"
        f"{f' 第{chunk.page}页' if chunk.page else ''}\n"
        f"引用的 quote：\n<<<\n{citation.quote}\n>>>\n"
        f"所在片段全文：\n<<<\n{truncate(chunk.text, MAX_CHUNK_CHARS)}\n>>>"
    )
    prompt = VERIFIER_USER.format(
        question=question,
        answer_markdown=answer.answer_markdown,
        citation_materials=material,
    )
    try:
        partial = structured_llm_call(
            build_chat_model("verifier"),
            _SingleCheckPayload,
            system=VERIFIER_SYSTEM,
            user=prompt,
        )
        if partial.checks:
            c = partial.checks[0]
            c.citation_index = citation.index  # 以输入为准，防模型改号
            c.citation_id = citation.chunk_id
            return c
    except LLMOutputError as e:
        logger.warning("引用 [%d] 校验输出非法：%s", citation.index, e)

    return CitationCheck(
        citation_id=citation.chunk_id,
        citation_index=citation.index,
        verdict="partially_supported",
        reason="校验子调用未能给出可解析结论，按未完全核验处理。",
    )
