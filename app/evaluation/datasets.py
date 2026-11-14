"""评估集管理：YAML/JSON 加载、写入 eval_datasets 表。"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.infra.db import session_scope
from app.infra.models import EvalDataset, EvalRun

logger = logging.getLogger(__name__)


class EvalItem(BaseModel):
    question: str
    ground_truth: str = ""
    expected_doc_ids: list[str] = Field(default_factory=list)
    audit_ctx_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class EvalDatasetSpec(BaseModel):
    name: str
    description: str = ""
    items: list[EvalItem]


def load_dataset_file(path: str | Path) -> EvalDatasetSpec:
    """加载评估集：支持本项目 YAML 格式（items），也支持 LangSmith 导出的 JSON（examples）。"""
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "examples" in raw and "items" not in raw:
        raw = _from_langsmith(raw)
    return EvalDatasetSpec.model_validate(raw)


def _from_langsmith(raw: dict) -> dict:
    """LangSmith 数据集结构 → 本项目 EvalDatasetSpec。

    inputs.question → question；outputs.ground_truth → ground_truth；
    metadata.expected_doc_ids / tags / audit_ctx_id 原样保留。
    """
    items = []
    for ex in raw.get("examples") or []:
        inputs = ex.get("inputs") or {}
        outputs = ex.get("outputs") or {}
        meta = ex.get("metadata") or {}
        item = {
            "question": inputs.get("question", ""),
            "ground_truth": outputs.get("ground_truth", "") or "",
            "expected_doc_ids": meta.get("expected_doc_ids") or [],
            "tags": meta.get("tags") or [],
        }
        if meta.get("audit_ctx_id"):
            item["audit_ctx_id"] = meta["audit_ctx_id"]
        items.append(item)
    return {
        "name": raw.get("name") or "langsmith-dataset",
        "description": raw.get("description", ""),
        "items": items,
    }


def load_dataset_dir(dir_path: str | Path) -> list[EvalDatasetSpec]:
    p = Path(dir_path)
    specs: list[EvalDatasetSpec] = []
    for f in sorted(p.glob("*.y*ml")) + sorted(p.glob("*.json")):
        try:
            specs.append(load_dataset_file(f))
        except Exception as e:  # noqa: BLE001
            logger.warning("加载评估集 %s 失败：%s", f.name, e)
    return specs


async def upsert_dataset(spec: EvalDatasetSpec) -> uuid.UUID:
    async with session_scope() as db:
        existing = (
            await db.execute(select(EvalDataset).where(EvalDataset.name == spec.name))
        ).scalar_one_or_none()
        items = [i.model_dump() for i in spec.items]
        if existing is not None:
            existing.items = items
            await db.flush()
            return existing.id
        ds = EvalDataset(id=uuid.uuid4(), name=spec.name, items=items)
        db.add(ds)
        await db.flush()
        return ds.id


async def get_dataset(name_or_id: str) -> EvalDataset | None:
    async with session_scope() as db:
        try:
            ds = await db.get(EvalDataset, uuid.UUID(name_or_id))
            if ds is not None:
                return ds
        except ValueError:
            pass
        return (
            await db.execute(select(EvalDataset).where(EvalDataset.name == name_or_id))
        ).scalar_one_or_none()


async def save_eval_run(
    dataset_id: uuid.UUID, report: dict, langsmith_url: str | None = None
) -> uuid.UUID:
    async with session_scope() as db:
        run = EvalRun(
            id=uuid.uuid4(),
            dataset_id=dataset_id,
            ragas_report=report,
            langsmith_url=langsmith_url,
        )
        db.add(run)
        await db.flush()
        return run.id


async def list_eval_runs(limit: int = 20) -> list[dict]:
    async with session_scope() as db:
        rows = (
            await db.execute(select(EvalRun).order_by(desc(EvalRun.created_at)).limit(limit))
        ).scalars().all()
    return [
        {
            "id": str(r.id),
            "dataset_id": str(r.dataset_id) if r.dataset_id else None,
            "report": r.ragas_report,
            "langsmith_url": r.langsmith_url,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
