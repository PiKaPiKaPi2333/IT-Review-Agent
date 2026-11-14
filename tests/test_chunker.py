"""切分器测试：表格整块、标题不跨章节、超长段落硬切。"""

from __future__ import annotations

from app.knowledge.chunker import (
    ParsedBlock,
    SemanticChunker,
    _split_long_text,
    count_tokens,
)


def _text(n_chars: int, prefix: str = "内容") -> str:
    return prefix + "字" * (n_chars // 3)


def test_table_kept_as_single_chunk():
    ch = SemanticChunker(chunk_size=120, overlap=10)
    blocks = [
        ParsedBlock("费用明细表", kind="title", level=1, page=1),
        ParsedBlock("| 项目 | 金额 |\n| --- | --- |\n| 差旅 | 12 |", kind="table", page=2),
        ParsedBlock("表格下方说明文字。", kind="text", page=2),
    ]
    chunks = ch.split(blocks, document_id="doc1")
    tables = [c for c in chunks if c.metadata["chunk_type"] == "table"]
    assert len(tables) == 1
    assert "| 差旅 | 12 |" in tables[0].text
    assert tables[0].page == 2


def test_title_boundary_starts_new_chunk():
    ch = SemanticChunker(chunk_size=400, overlap=20)
    blocks = [
        ParsedBlock("第一章 总则", kind="title", level=1, page=1),
        ParsedBlock("总则内容。", kind="text", page=1),
        ParsedBlock("第二章 采购", kind="title", level=1, page=2),
        ParsedBlock("采购内容。", kind="text", page=2),
    ]
    chunks = ch.split(blocks, document_id="doc1")
    assert len(chunks) >= 2
    assert all(
        not ("第一章" in c.text and "第二章" in c.text) for c in chunks
    )


def test_section_path_recorded():
    ch = SemanticChunker(chunk_size=400, overlap=0)
    blocks = [
        ParsedBlock("第三章 审批", kind="title", level=1),
        ParsedBlock("3.1 权限", kind="title", level=2),
        ParsedBlock("一百万元以上报总经理办公会。", kind="text"),
    ]
    chunks = ch.split(blocks, document_id="doc1")
    assert any("第三章 审批 > 3.1 权限" in c.section for c in chunks)


def test_long_paragraph_split_within_budget():
    ch = SemanticChunker(chunk_size=60, overlap=10)
    long_text = "这是一个很长的句子。" * 60
    chunks = ch.split(
        [ParsedBlock(long_text, page=1)], document_id="doc1"
    )
    assert len(chunks) > 1
    assert all(count_tokens(c.text) <= 90 for c in chunks)


def test_chunk_ids_unique_and_stable():
    ch = SemanticChunker(chunk_size=100, overlap=10)
    blocks = [ParsedBlock(f"第{i}段内容。", page=i) for i in range(1, 6)]
    a = ch.split(blocks, document_id="docX")
    b = ch.split(blocks, document_id="docX")
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert len({c.chunk_id for c in a}) == len(a)


def test_extra_meta_propagated():
    ch = SemanticChunker(chunk_size=100, overlap=0)
    chunks = ch.split(
        [ParsedBlock("内容。", page=1)],
        document_id="doc1",
        extra_meta={"doc_type": "regulation", "audit_year": 2023},
    )
    assert chunks[0].metadata["doc_type"] == "regulation"
    assert chunks[0].metadata["audit_year"] == 2023


def test_split_long_text_keeps_all_sentences():
    text = "甲乙丙丁。" * 30
    pieces = _split_long_text(text, size=40, overlap=5)
    assert "".join(pieces).count("。") >= 28
    assert all(pieces)
