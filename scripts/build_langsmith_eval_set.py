"""生成 IT 审计评测集（10 条），输出 LangSmith 可导入的 JSON 与 CSV。

- JSON：LangSmith Dataset 的 inputs/outputs 结构，可直接在 Web 端 "Upload JSON" 或 SDK 导入
- CSV：列名 inputs.* / outputs.*，LangSmith 导入 CSV 时会自动映射
- 同时保留项目原有的 YAML 字段（expected_doc_ids / tags / audit_ctx_id）

用法：python scripts/build_langsmith_eval_set.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval_datasets"
DATASET_NAME = "it-audit-qa-10"
DESCRIPTION = "IT 审计问答评测集（10 条），标准答案均摘自 samples/it_audit 中的材料。"

ITEMS = [
    {
        "question": "员工离职后，HR 应在什么时间通知信息中心？信息中心应在什么时间注销账号？",
        "ground_truth": "人力资源部应于离职当日通知信息中心，信息中心应于当日注销其全部系统账号。",
        "source": "信息安全管理制度_节选.docx",
        "tags": ["factual", "access-control"],
    },
    {
        "question": "口令的最小长度和更换周期是多少？",
        "ground_truth": "口令长度不少于 8 位，须包含字母与数字，每 90 天更换一次。",
        "source": "信息安全管理制度_节选.docx",
        "tags": ["factual", "access-control"],
    },
    {
        "question": "生产系统变更上线前需要满足哪些条件？",
        "ground_truth": "须提交变更申请，经业务负责人与信息中心负责人审批，并在测试环境验证通过后方可上线。",
        "source": "信息安全管理制度_节选.docx",
        "tags": ["factual", "change-management"],
    },
    {
        "question": "紧急变更的审批要求是什么？",
        "ground_truth": "紧急变更须经信息中心负责人口头批准，并在 3 个工作日内补办书面审批。",
        "source": "信息安全管理制度_节选.docx",
        "tags": ["factual", "change-management"],
    },
    {
        "question": "核心系统的备份策略和备份保留期限是怎样的？",
        "ground_truth": "核心系统每日执行增量备份，每周执行全量备份，备份数据保留不少于 180 天。",
        "source": "信息安全管理制度_节选.docx",
        "tags": ["factual", "backup"],
    },
    {
        "question": "恢复演练的频率和恢复时间目标是多少？",
        "ground_truth": "每季度至少执行一次恢复演练，演练结果形成书面报告，恢复时间须满足 RTO 4 小时的要求。",
        "source": "信息安全管理制度_节选.docx",
        "tags": ["factual", "backup"],
    },
    {
        "question": "发生数据泄露事件时，事件负责人须在多长时间内向信息安全负责人报告？",
        "ground_truth": "事件负责人应在 2 小时内向信息安全负责人报告。",
        "source": "信息安全事件管理办法_节选.docx",
        "tags": ["factual", "incident-response"],
    },
    {
        "question": "为什么外包开发项目的验收结论被认定存在问题？",
        "ground_truth": "第 2 期验收报告由信息中心单方签署，验收职责不相容，建议由业务部门参与验收签署。",
        "source": "外包开发商管理审计底稿.docx",
        "tags": ["analytical", "outsourcing"],
    },
    {
        "question": "机房环境检查中有哪些需要整改的事项？",
        "ground_truth": "气体灭火系统检测超期（上次检测为 2026-01）、视频保存期限不足（90 天，低于制度要求的 180 天）、"
                        "B 区 14 个机柜缺少标签，整改责任人为运维主管，计划 2026 年 12 月底前完成。",
        "source": "数据中心机房环境检查表.docx",
        "tags": ["analytical", "data-center"],
    },
    {
        "question": "离职人员账号注销目前的执行方式存在什么问题？",
        "ground_truth": "HR 离职流程通知信息中心依赖人工通知，缺少与 HR 系统的自动对接，注销实际执行可能不及时。",
        "source": "IT总体控制访谈纪要.docx",
        "tags": ["analytical", "access-control"],
    },
]


def build_records() -> list[dict]:
    records = []
    for i, item in enumerate(ITEMS, start=1):
        records.append(
            {
                "id": f"it-audit-{i:02d}",
                "inputs": {"question": item["question"]},
                "outputs": {"ground_truth": item["ground_truth"]},
                "metadata": {
                    "source_document": item["source"],
                    "tags": item["tags"],
                    "expected_doc_ids": [],  # 文档入库后再回填真实 document_id
                },
            }
        )
    return records


def write_json(records: list[dict]) -> Path:
    path = OUT / "it_audit_langsmith.json"
    payload = {"name": DATASET_NAME, "description": DESCRIPTION, "examples": records}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_csv(records: list[dict]) -> Path:
    path = OUT / "it_audit_langsmith.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["inputs.question", "outputs.ground_truth", "metadata.source_document", "metadata.tags"])
        for r in records:
            writer.writerow([
                r["inputs"]["question"],
                r["outputs"]["ground_truth"],
                r["metadata"]["source_document"],
                ",".join(r["metadata"]["tags"]),
            ])
    return path


def write_project_yaml(records: list[dict]) -> Path:
    """同时输出项目原有格式（load_dataset_file 可直接加载）。"""
    spec = {
        "name": DATASET_NAME,
        "description": DESCRIPTION,
        "items": [
            {
                "question": r["inputs"]["question"],
                "ground_truth": r["outputs"]["ground_truth"],
                "expected_doc_ids": [],
                "tags": r["metadata"]["tags"],
            }
            for r in records
        ],
    }
    path = OUT / "it_audit_qa.yaml"
    path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    records = build_records()
    for p in (write_json(records), write_csv(records), write_project_yaml(records)):
        print(f"已写入 {p}")


if __name__ == "__main__":
    main()
