"""RAGAS 离线评估流水线（架构 §8.2）。

流程：拉取评估集 → 逐条跑图 → 组装 RAGAS 样本 → 计算指标 → 落库 eval_runs。

RAGAS 的指标需要 LLM 与 embedding，这里统一走硅基流动：
用 langchain_openai 的 ChatOpenAI / OpenAIEmbeddings 包一层即可。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from app.evaluation.datasets import EvalDatasetSpec, EvalItem, dump_json, save_eval_run
from app.graph.runner import run_question
from app.infra.config import get_settings

logger = logging.getLogger(__name__)


async def _run_one(item: EvalItem, user_id: str) -> dict[str, Any]:
    final = await run_question(
        question=item.question,
        user_id=user_id,
        audit_ctx_id=item.audit_ctx_id,
    )
    return {
        "question": item.question,
        "answer": final.answer_markdown,
        "contexts": [c.text for c in final.evidence],
        "ground_truth": item.ground_truth,
        "status": final.status,
        "confidence": final.confidence,
        # 自定义指标：验证 Agent 聚合出的引用通过率
        "citation_pass_rate": _citation_pass_rate(final),
        "citations": [c.model_dump() for c in final.citations],
        "expected_doc_ids": item.expected_doc_ids,
        "retrieved_doc_ids": sorted({c.document_id for c in final.evidence}),
    }


def _citation_pass_rate(final) -> float | None:
    v = final.verification
    if v is None or not v.checks:
        return None
    ok = sum(1 for c in v.checks if c.verdict == "supported")
    return ok / len(v.checks)


def _build_langchain_models():
    """RAGAS 需要 langchain 形态的 LLM / embeddings。"""
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    s = get_settings()
    llm = ChatOpenAI(
        model=s.generator_model,
        api_key=s.siliconflow_api_key or "EMPTY",
        base_url=s.siliconflow_base_url,
        temperature=0.0,
        timeout=s.llm_timeout,
    )
    embeddings = OpenAIEmbeddings(
        model=s.embedding_model,
        api_key=s.siliconflow_api_key or "EMPTY",
        base_url=s.siliconflow_base_url,
        check_embedding_ctx_length=False,
    )
    return llm, embeddings


async def run_evaluation(
    spec: EvalDatasetSpec,
    *,
    dataset_id=None,
    metrics: list[str] | None = None,
    max_concurrency: int = 3,
    user_id: str = "eval",
    checkpoint_path: str | Path | None = None,
) -> dict[str, Any]:
    """跑完整评估，返回报告 dict。"""
    sem = asyncio.Semaphore(max_concurrency)

    async def guarded(item: EvalItem) -> dict[str, Any]:
        async with sem:
            try:
                return await _run_one(item, user_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("评估样本失败：%s", e)
                return {
                    "question": item.question,
                    "answer": "",
                    "contexts": [],
                    "ground_truth": item.ground_truth,
                    "status": "error",
                    "citation_pass_rate": None,
                    "error": str(e),
                }

    samples = await asyncio.gather(*(guarded(i) for i in spec.items))
    # 问答结果先落盘：RAGAS 计算很慢且容易失败，不能让它吞掉已经跑完的问答
    if checkpoint_path is not None:
        Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
        Path(checkpoint_path).write_text(
            json.dumps({"dataset": spec.name, "samples": samples}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("问答样本已写入检查点 %s（共 %d 条）", checkpoint_path, len(samples))
    report: dict[str, Any] = {
        "dataset": spec.name,
        "n_items": len(samples),
        "status_counts": _count(samples, "status"),
        "custom": {
            "citation_pass_rate_avg": _avg([s.get("citation_pass_rate") for s in samples]),
            "context_hit_rate": _context_hit_rate(samples),
        },
        "ragas": {},
        "samples": samples,
    }

    try:
        report["ragas"] = _compute_ragas(samples, metrics)
    except Exception as e:  # noqa: BLE001
        logger.warning("RAGAS 计算失败：%s", e)
        report["ragas_error"] = str(e)

    if dataset_id is not None:
        report["eval_run_id"] = str(await save_eval_run(dataset_id, report))
    return report


def _compute_ragas(samples: list[dict], metrics: list[str] | None) -> dict:
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    usable = [s for s in samples if s.get("answer") and s.get("contexts")]
    if not usable:
        return {"skipped": "无可用样本（answer/contexts 为空）"}

    ds = Dataset.from_dict(
        {
            "question": [s["question"] for s in usable],
            "answer": [s["answer"] for s in usable],
            "contexts": [s["contexts"] for s in usable],
            "ground_truth": [s.get("ground_truth") or "" for s in usable],
        }
    )
    registry = {
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        "context_precision": context_precision,
        "context_recall": context_recall,
    }
    chosen = [registry[m] for m in (metrics or registry.keys()) if m in registry]
    llm, embeddings = _build_langchain_models()

    result = evaluate(dataset=ds, metrics=chosen, llm=llm, embeddings=embeddings)
    # ragas 0.4 的 EvaluationResult 没有 items()，统一经 to_pandas() 取各指标均值
    df = result.to_pandas()
    names = [m.name for m in chosen]
    return {n: _safe_float(df[n].mean()) for n in names if n in df.columns}


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return None if f != f else round(f, 4)  # NaN → None
    except (TypeError, ValueError):
        return None


def _avg(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    return round(sum(nums) / len(nums), 4) if nums else None


def _count(samples: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in samples:
        k = str(s.get(key))
        out[k] = out.get(k, 0) + 1
    return out


def _context_hit_rate(samples: list[dict]) -> float | None:
    """检索是否命中期望文档（context_recall 的粗粒度替代）。"""
    scored: list[float] = []
    for s in samples:
        expected = set(s.get("expected_doc_ids") or [])
        if not expected:
            continue
        got = set(s.get("retrieved_doc_ids") or [])
        scored.append(len(expected & got) / len(expected))
    return round(sum(scored) / len(scored), 4) if scored else None


def format_report(report: dict) -> str:
    lines = [
        f"# 评估报告：{report.get('dataset')}",
        "",
        f"- 样本数：{report.get('n_items')}",
        f"- 状态分布：{report.get('status_counts')}",
        f"- 自定义指标：{dump_json(report.get('custom'))}",
        f"- RAGAS：{dump_json(report.get('ragas'))}",
    ]
    if report.get("ragas_error"):
        lines.append(f"- RAGAS 错误：{report['ragas_error']}")
    if report.get("eval_run_id"):
        lines.append(f"- 已写入 eval_runs：{report['eval_run_id']}")
    return "\n".join(lines)
