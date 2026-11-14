"""用 LangSmith evaluate 回放 it_audit_langsmith 数据集，目标函数走真实问答链路。

用法：

    python -m scripts.run_langsmith_eval [数据集名] [实验前缀]

LangSmith 凭据从项目根目录的 .env 读取（LANGSMITH_API_KEY / LANGSMITH_ENDPOINT / LANGSMITH_PROJECT）。
已设置的进程环境变量优先于 .env。

评估器说明：
- 问答是自由文本，不能用 exact_match 判对错，这里改用可解释的指标：
  - status_ok：回答状态是否为 ok（未降级、未证据不足）
  - citation_supported：引用中被核验为 supported 的比例
  - ground_truth_overlap：标准答案的字符二元组在回答中的覆盖率（词法近似，仅作参考）
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path
from concurrent.futures import Future
from typing import Any

from langsmith import evaluate

from app.graph.runner import run_question
from app.infra.config import get_settings
from app.infra.db import dispose_engine
from app.infra.loop import selector_loop_factory
from app.memory.short_term import checkpointer_holder

logger = logging.getLogger("run_langsmith_eval")

DATASET_NAME = "it_audit_langsmith"
EXPERIMENT_PREFIX = "it_audit_langsmith experiment"
USER_ID = "eval"
# 本地评测集（YAML）用于补充每条题目的期望状态；题目文本与云端数据集对齐
LOCAL_SPEC = Path(__file__).resolve().parents[1] / "eval_datasets" / "it_audit_qa_50.yaml"


class _LoopThread:
    """后台事件循环：检查点与数据库连接池都绑定在同一个循环上，避免跨循环清理报错。"""

    def __init__(self) -> None:
        self.loop = selector_loop_factory()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name="eval-loop")
        self.thread.start()

    def run(self, coro, timeout: float = 900) -> Any:
        fut: Future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout=timeout)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)


_bg: _LoopThread | None = None


_EXPECTED_BY_TAG = {"out_of_scope": "out_of_scope", "clarify": "needs_clarification"}


def _load_expected_status() -> dict[str, str]:
    """题目 -> 期望状态：越界题期望 out_of_scope，澄清题期望 needs_clarification，其余期望 ok。"""
    if not LOCAL_SPEC.exists():
        return {}
    import yaml

    raw = yaml.safe_load(LOCAL_SPEC.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for it in raw.get("items", []):
        expected = next((_EXPECTED_BY_TAG[t] for t in it.get("tags", []) if t in _EXPECTED_BY_TAG), "ok")
        out[it["question"]] = expected
    return out


EXPECTED_STATUS = _load_expected_status()


def _summarize(final) -> dict[str, Any]:
    checks = final.verification.checks if final.verification else []
    supported = sum(1 for c in checks if c.verdict == "supported")
    return {
        "answer": final.answer_markdown,
        "status": final.status,
        "confidence": final.confidence,
        "n_citations": len(final.citations),
        "supported_ratio": (supported / len(checks)) if checks else None,
    }


def target(inputs: dict) -> dict[str, Any]:
    assert _bg is not None, "后台事件循环未启动"
    question = inputs["question"]
    final = _bg.run(run_question(question=question, user_id=USER_ID))
    out = _summarize(final)
    out["expected_status"] = EXPECTED_STATUS.get(question, "ok")
    return out


def status_ok(outputs: dict, reference_outputs: dict) -> dict:
    return {"key": "status_ok", "score": 1 if outputs.get("status") == "ok" else 0}


def status_as_expected(outputs: dict, reference_outputs: dict) -> dict:
    return {"key": "status_as_expected", "score": 1 if outputs.get("status") == outputs.get("expected_status") else 0}


def citation_supported(outputs: dict, reference_outputs: dict) -> dict:
    ratio = outputs.get("supported_ratio")
    # 没有引用时记 0：没有可核验的依据，不应计为通过
    return {"key": "citation_supported", "score": ratio if ratio is not None else 0}


def _bigrams(text: str) -> set[str]:
    s = "".join(ch for ch in (text or "") if not ch.isspace())
    return {s[i : i + 2] for i in range(len(s) - 1)}


def ground_truth_overlap(outputs: dict, reference_outputs: dict) -> dict:
    # 越界 / 澄清题的标准答案描述的是期望行为，不是答案文本，不做词法比对
    if outputs.get("expected_status") != "ok":
        return {"key": "ground_truth_overlap", "score": None}
    gt = _bigrams((reference_outputs or {}).get("ground_truth", ""))
    if not gt:
        return {"key": "ground_truth_overlap", "score": None}
    ans = _bigrams(outputs.get("answer", ""))
    return {"key": "ground_truth_overlap", "score": len(gt & ans) / len(gt)}


def _load_langsmith_env() -> None:
    """把 .env 中的 LangSmith 配置同步到环境变量（SDK 只读 os.environ，不读 Settings）。

    已存在的环境变量保持不变，因此可以临时用环境变量覆盖 .env。
    """
    s = get_settings()
    if not s.langsmith_api_key:
        raise SystemExit("未找到 LANGSMITH_API_KEY：请在 .env 中配置，或设置同名环境变量")
    os.environ.setdefault("LANGSMITH_API_KEY", s.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_ENDPOINT", s.langsmith_endpoint)
    os.environ.setdefault("LANGSMITH_PROJECT", s.langsmith_project)


def main() -> None:
    global _bg
    logging.basicConfig(level=logging.WARNING)
    _load_langsmith_env()
    dataset = sys.argv[1] if len(sys.argv) > 1 else DATASET_NAME
    prefix = sys.argv[2] if len(sys.argv) > 2 else EXPERIMENT_PREFIX
    _bg = _LoopThread()
    _bg.run(checkpointer_holder.startup())
    try:
        results = evaluate(
            target,
            data=dataset,
            evaluators=[status_ok, status_as_expected, citation_supported, ground_truth_overlap],
            experiment_prefix=prefix,
            max_concurrency=2,
        )
        print(f"\n实验名称：{results.experiment_name}")
    finally:
        _bg.run(checkpointer_holder.shutdown())
        _bg.run(dispose_engine())
        _bg.stop()


if __name__ == "__main__":
    main()
