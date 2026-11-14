"""把已确认的 bad case（👎 + 复核确认 + 标准答案）导出为评估集 YAML。

用法：python -m scripts.export_bad_cases [--out eval_datasets/bad_cases.yaml]

每次运行都从数据库全量重新生成文件（同一问题只保留最近一次复核），
因此可以重复执行；导出后用 `make eval DATASET=eval_datasets/bad_cases.yaml` 回放验证。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import yaml
from sqlalchemy import select

from app.evaluation.datasets import load_dataset_file
from app.infra.db import session_scope
from app.infra.models import AnswerFeedback, Conversation

logger = logging.getLogger("export_bad_cases")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "eval_datasets" / "bad_cases.yaml"

REASON_TAG = {
    "factual_error": "事实错误",
    "citation_mismatch": "引用不符",
    "incomplete": "回答不完整",
    "other": "其他",
}


async def collect_items() -> tuple[list[dict], int]:
    """返回 (评估集条目, 因缺少问题而跳过的数量)。"""
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(AnswerFeedback, Conversation.audit_ctx_id)
                .outerjoin(Conversation, Conversation.id == AnswerFeedback.conversation_id)
                .where(
                    AnswerFeedback.status == "confirmed",
                    AnswerFeedback.rating == -1,
                    AnswerFeedback.ground_truth.isnot(None),
                )
                .order_by(AnswerFeedback.reviewed_at.desc(), AnswerFeedback.created_at.desc())
            )
        ).all()

    items: list[dict] = []
    seen: set[str] = set()
    skipped = 0
    for fb, audit_ctx_id in rows:
        question = (fb.question or "").strip()
        if not question:
            skipped += 1
            continue
        if question in seen:  # 同一问题只保留最近一次复核
            continue
        seen.add(question)

        tags = ["bad_case"]
        if fb.reason:
            tags.append(REASON_TAG.get(fb.reason, fb.reason))
        item: dict = {
            "question": question,
            "ground_truth": fb.ground_truth,
            "expected_doc_ids": [],
            "tags": tags,
        }
        if audit_ctx_id:
            item["audit_ctx_id"] = str(audit_ctx_id)
        items.append(item)
    return items, skipped


def write_dataset(items: list[dict], out: Path) -> None:
    spec = {
        "name": "bad-cases",
        "description": "线上 👎 反馈经复核确认的坏例（自动导出，请勿手工编辑）",
        "items": items,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )
    # 用评估集的加载器校验一次，保证导出结果可以直接回放
    load_dataset_file(out)


async def main_async(out: Path) -> int:
    items, skipped = await collect_items()
    write_dataset(items, out)
    print(f"已导出 {len(items)} 条坏例到 {out}")
    if skipped:
        print(f"跳过 {skipped} 条：缺少问题文本")
    return len(items)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出已确认的 bad case 到评估集")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    asyncio.run(main_async(args.out))


if __name__ == "__main__":
    main()
