from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.graph.runner import run_question
from app.infra.logging import setup_logging
from app.infra.loop import use_selector_event_loop


def _setup_env() -> None:
    setup_logging()
    from app.infra.tracing import init_tracing

    init_tracing()


async def _cmd_ask(args) -> int:
    final = await run_question(
        question=args.question,
        user_id=args.user,
        thread_id=args.thread,
        conversation_id=args.conversation,
        audit_ctx_id=args.audit_ctx,
        audit_context_text=args.context_text or "",
    )
    if args.json:
        print(final.model_dump_json(indent=2))
    else:
        print(f"\n[status={final.status} confidence={final.confidence} "
              f"latency={final.latency_ms}ms]\n")
        print(final.answer_markdown)
        if final.citations:
            print("\n引用：")
            for c in final.citations:
                page = f" 第{c.page}页" if c.page else ""
                print(f"  [{c.index}] {c.document_id}{page} — {c.quote[:80]}")
        if final.caveats:
            print("\n注意事项：")
            for c in final.caveats:
                print(f"  - {c}")
    return 0


async def _cmd_ingest(args) -> int:
    from app.infra.db import ensure_schema
    from app.knowledge.ingest import (
        IGNORED_SUFFIXES,
        IngestError,
        detect_route,
        ingest_document,
    )

    await ensure_schema()

    # 展开目录：递归收集支持的文件，跳过隐藏目录（如 .claude）与不支持格式
    targets: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    for raw in args.files:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                if any(part.startswith(".") for part in f.relative_to(p).parts):
                    continue
                route = detect_route(f)
                if route == "unsupported":
                    skipped.append((f, f"不支持 {f.suffix or '（无扩展名）'}"))
                else:
                    targets.append(f)
        elif p.is_file():
            route = detect_route(p)
            if route == "unsupported":
                skipped.append((p, f"不支持 {p.suffix or '（无扩展名）'}"))
            else:
                targets.append(p)
        else:
            skipped.append((p, "路径不存在"))

    if skipped:
        print(f"跳过 {len(skipped)} 个文件：")
        for f, why in skipped[:20]:
            print(f"  - {f.name}: {why}")
        if len(skipped) > 20:
            print(f"  … 另有 {len(skipped) - 20} 个")

    if not targets:
        print("没有可入库的文件。", file=sys.stderr)
        return 1

    print(f"待入库 {len(targets)} 个文件\n")
    ok_count = fail_count = 0
    for idx, path in enumerate(targets, 1):
        route = detect_route(path)
        try:
            result = await ingest_document(
                file_path=path,
                audit_ctx_id=args.audit_ctx,
                doc_type=args.doc_type,
                audit_year=args.year,
                department=args.department,
            )
        except IngestError as e:
            fail_count += 1
            print(f"[{idx}/{len(targets)}] FAIL {path.name}: {e}", file=sys.stderr)
            continue

        ok_count += 1
        status = result.get("status")
        chunks = result.get("chunks", 0)
        note = "（已存在，跳过）" if result.get("skipped") else ""
        print(f"[{idx}/{len(targets)}] OK   {path.name} [{route}] → {status} chunks={chunks}{note}")

    print(f"\n完成：成功 {ok_count}，失败 {fail_count}，跳过 {len(skipped)}")
    if args.failures_out and fail_count:
        Path(args.failures_out).write_text(
            json.dumps([p.name for p in targets], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if fail_count == 0 else 1


async def _cmd_eval(args) -> int:
    from app.evaluation.datasets import (
        load_dataset_dir,
        load_dataset_file,
        upsert_dataset,
    )
    from app.evaluation.ragas_runner import format_report, run_evaluation

    p = Path(args.dataset)
    specs = load_dataset_dir(p) if p.is_dir() else [load_dataset_file(p)]
    exit_code = 0
    for spec in specs:
        dataset_id = await upsert_dataset(spec)
        report = await run_evaluation(
            spec,
            dataset_id=dataset_id,
            metrics=args.metrics,
            checkpoint_path=(Path(args.out).with_suffix(".samples.json") if args.out else None),
        )
        print(format_report(report))
        if args.out:
            Path(args.out).write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            print(f"\n完整报告已写入 {args.out}")
        if report.get("ragas_error"):
            exit_code = 1
    return exit_code


async def _cmd_check(_args) -> int:
    """连通性自检：LLM / embedding / Milvus / PostgreSQL。"""
    import httpx

    from app.infra.config import get_settings

    s = get_settings()
    problems: list[str] = []

    print("== 配置 ==")
    print(f"  LLM   : {s.siliconflow_base_url}  classifier={s.classifier_model} "
          f"generator={s.generator_model}")
    print(f"  Emb   : {s.embedding_model} ({s.embedding_dim}d)")
    print(f"  Milvus: {s.milvus_uri} / {s.milvus_collection}")
    print(f"  PG    : {_mask(s.database_url)}")

    # LLM
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{s.siliconflow_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {s.siliconflow_api_key}"},
            )
        print(f"  [{'ok' if r.status_code == 200 else 'fail'}] LLM API {r.status_code}")
        if r.status_code != 200:
            problems.append(f"LLM API {r.status_code}: {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"LLM API: {e}")
        print(f"  [fail] LLM API: {e}")

    # Embedding
    try:
        from app.knowledge.embedder import get_embedder

        vec = await get_embedder().embed_query("审计 测试")
        print(f"  [ok] Embedding 维度 {len(vec)}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"Embedding: {e}")
        print(f"  [fail] Embedding: {e}")

    # Milvus
    try:
        from app.knowledge.milvus_store import get_store

        await get_store().ensure_collection()
        print("  [ok] Milvus 连通，collection 就绪")
    except Exception as e:  # noqa: BLE001
        problems.append(f"Milvus: {e}")
        print(f"  [fail] Milvus: {e}")

    # PostgreSQL
    try:
        from sqlalchemy import text

        from app.infra.db import get_engine

        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("  [ok] PostgreSQL 连通")
    except Exception as e:  # noqa: BLE001
        problems.append(f"PostgreSQL: {e}")
        print(f"  [fail] PostgreSQL: {e}")

    # MinerU
    from app.knowledge.mineru_parser import MinerUParser
    from app.knowledge.spreadsheet_parser import SpreadsheetParser

    ok, detail = MinerUParser().is_available()
    print(f"  [{'ok' if ok else 'fail'}] MinerU API: {detail.splitlines()[0]}")
    if not ok:
        problems.append("MinerU API 未就绪")

    ok, detail = SpreadsheetParser().is_available()
    print(f"  [{'ok' if ok else 'fail'}] 表格解析(xlsx/xlsb/csv): {detail.splitlines()[0]}")
    if not ok:
        problems.append(detail)

    if problems:
        print("\n存在的问题：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\n全部就绪。")
    return 0


def _mask(dsn: str) -> str:
    import re

    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", dsn)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reviewagent", description="审计智能问答系统 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="提问")
    ask.add_argument("question")
    ask.add_argument("--user", default="cli")
    ask.add_argument("--thread", default=None)
    ask.add_argument("--conversation", default=None)
    ask.add_argument("--audit-ctx", dest="audit_ctx", default=None)
    ask.add_argument("--context-text", dest="context_text", default=None)
    ask.add_argument("--json", action="store_true")
    ask.set_defaults(func=_cmd_ask)

    ing = sub.add_parser("ingest", help="文档入库（支持目录、递归、按格式自动路由）")
    ing.add_argument("files", nargs="+", help="文件或目录")
    ing.add_argument("--audit-ctx", dest="audit_ctx", default=None)
    ing.add_argument("--doc-type", dest="doc_type", default=None)
    ing.add_argument("--year", type=int, default=None)
    ing.add_argument("--department", default=None)
    ing.add_argument("--failures-out", dest="failures_out", default=None,
                     help="把失败文件列表写入该 JSON 文件")
    ing.set_defaults(func=_cmd_ingest)

    ev = sub.add_parser("eval", help="RAGAS 离线评估")
    ev.add_argument("dataset", help="评估集文件或目录")
    ev.add_argument("--metrics", nargs="*", default=None)
    ev.add_argument("--out", default=None)
    ev.set_defaults(func=_cmd_eval)

    chk = sub.add_parser("check", help="依赖与服务连通性自检")
    chk.set_defaults(func=_cmd_check)

    return p


async def _amain(args) -> int:
    from app.infra.db import dispose_engine
    from app.memory.short_term import checkpointer_holder

    # 只有跑图的命令（ask / eval）才需要短时记忆，ingest / check 不必付启动成本
    needs_checkpointer = args.cmd in {"ask", "eval"}
    if needs_checkpointer:
        await checkpointer_holder.startup()
    try:
        return await args.func(args)
    finally:
        if needs_checkpointer:
            await checkpointer_holder.shutdown()
        await dispose_engine()


def main() -> int:
    # psycopg 异步模式不支持 Windows 默认的 ProactorEventLoop，先切 Selector
    use_selector_event_loop()
    _setup_env()
    args = build_parser().parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
