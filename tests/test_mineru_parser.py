"""MinerU API 客户端测试：不联网，只覆盖易错逻辑。

重点：响应外壳兼容、错误码映射、批量结果挑选、zip slip 防护、
content_list 解析（标题层级 / 表格 / 页码）。
"""

from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest

from app.knowledge.mineru_parser import (
    ERROR_HINTS,
    MinerUError,
    MinerUParser,
    _html_table_to_text,
    _pick_result,
    _safe_data_id,
    extract_parse_meta,
)


@pytest.fixture
def parser():
    return MinerUParser()


def _resp(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=payload,
        request=httpx.Request("GET", "https://mineru.net/api/v4/test"),
    )


class TestUnwrap:
    def test_business_envelope(self, parser):
        data = parser._unwrap(
            _resp({"code": 0, "data": {"batch_id": "b1", "file_urls": ["u"]}}), "x"
        )
        assert data["batch_id"] == "b1"

    def test_gateway_envelope(self, parser):
        data = parser._unwrap(_resp({"success": True, "msgCode": 200, "data": {"a": 1}}), "x")
        assert data["a"] == 1

    def test_business_error_maps_hint(self, parser):
        with pytest.raises(MinerUError, match="Token 无效"):
            parser._unwrap(_resp({"code": "A0202", "msg": "auth failed"}), "申请")

    def test_quota_error_maps_hint(self, parser):
        with pytest.raises(MinerUError, match="额度"):
            parser._unwrap(_resp({"code": -60018, "msg": "quota"}), "申请")

    def test_non_json_body(self, parser):
        resp = httpx.Response(
            502, text="<html>bad gateway</html>",
            request=httpx.Request("POST", "https://mineru.net/api/v4/test"),
        )
        with pytest.raises(MinerUError, match="502"):
            parser._unwrap(resp, "申请")

    def test_all_error_codes_have_hints(self):
        # 常见的失败码都应给出可操作提示
        for code in ("A0202", "A0211", "-60005", "-60018"):
            assert code in ERROR_HINTS


class TestParseMeta:
    def test_counts_pages_tables_titles(self, parser):
        from app.knowledge.chunker import ParsedBlock

        blocks = [
            ParsedBlock("第一章", kind="title", page=1),
            ParsedBlock("正文", page=1),
            ParsedBlock("表格", kind="table", page=2),
        ]
        meta = extract_parse_meta(blocks)
        assert meta["pages"] == 2
        assert meta["tables"] == 1
        assert meta["titles"] == 1
        assert meta["source"] == "mineru-api"


class TestAvailable:
    def test_missing_key_gives_actionable_message(self, parser, monkeypatch):
        monkeypatch.setattr(parser.s, "mineru_api_key", "", raising=False)
        ok, msg = parser.is_available()
        assert ok is False
        assert "MINERU_API_KEY" in msg and "mineru.net" in msg

    def test_with_key_ok(self, parser, monkeypatch):
        monkeypatch.setattr(parser.s, "mineru_api_key", "sk-test", raising=False)
        ok, msg = parser.is_available()
        assert ok is True
        assert "pipeline" in msg


class TestHelpers:
    def test_data_id_sanitized(self):
        # 采购管理办法( → 7 个下划线；版) → 2 个；数字保留
        assert _safe_data_id("采购管理办法(2023版).pdf") == "_______2023__"
        assert len(_safe_data_id("a" * 300 + ".pdf")) <= 120

    def test_pick_result_single(self):
        assert _pick_result([{"file_name": "x.pdf"}], "y.pdf") == {"file_name": "x.pdf"}

    def test_pick_result_matches_filename(self):
        results = [{"file_name": "a.pdf", "state": "done"}, {"file_name": "b.pdf"}]
        assert _pick_result(results, "b.pdf")["file_name"] == "b.pdf"

    def test_pick_result_empty(self):
        assert _pick_result([], "a.pdf") is None

    def test_html_table_to_text(self):
        html = "<table><tr><td>差旅</td><td>12</td></tr><tr><td>会议</td><td>3</td></tr></table>"
        text = _html_table_to_text(html)
        assert "差旅" in text and "会议" in text
        assert "<" not in text


def _make_zip(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestZipExtract:
    def test_extracts_content_list_and_parses_blocks(self, parser, tmp_path):
        content = [
            {"type": "title", "text": "第三章 审批权限", "text_level": 2, "page_idx": 0},
            {"type": "text", "text": "采购金额超过一百万元须报总经理办公会。", "page_idx": 0},
            {"type": "table", "table_body": "| 项目 | 金额 |\n| 差旅 | 12 |",
             "table_caption": [{"text": "费用明细"}], "page_idx": 1},
            {"type": "image", "img_path": "a.jpg", "page_idx": 1},
        ]
        zip_bytes = _make_zip({"full.md": "# 忽略", "x_content_list.json": json.dumps(content)})

        blocks = parser._extract_zip(zip_bytes, "doc1", tmp_path)

        titles = [b for b in blocks if b.kind == "title"]
        tables = [b for b in blocks if b.kind == "table"]
        assert titles[0].text == "第三章 审批权限"
        assert titles[0].level == 2
        assert titles[0].page == 1  # page_idx 0 → 页码 1
        assert len(tables) == 1
        assert "费用明细" in tables[0].text and "差旅" in tables[0].text
        assert tables[0].page == 2
        # 图片块不进入证据链
        assert not any(b.kind == "image" for b in blocks)
        # 原始产物落盘，便于溯源
        assert (tmp_path / "doc1" / "x_content_list.json").exists()

    def test_falls_back_to_markdown(self, parser, tmp_path):
        md = "# 第一章 总则\n\n本制度适用于集团总部。\n\n## 1.1 范围\n\n含子公司。"
        blocks = parser._extract_zip(_make_zip({"full.md": md}), "doc2", tmp_path)
        assert [b.kind for b in blocks].count("title") == 2
        assert any("适用于集团总部" in b.text for b in blocks)
        assert all(b.page is not None for b in blocks)

    def test_rejects_bad_zip(self, parser, tmp_path):
        with pytest.raises(MinerUError, match="zip"):
            parser._extract_zip(b"not a zip", "doc3", tmp_path)

    def test_zip_slip_blocked(self, parser, tmp_path):
        evil = _make_zip({"../escaped.txt": "pwned", "ok_content_list.json": "[]"})
        parser._extract_zip(evil, "doc4", tmp_path)
        assert not (tmp_path.parent / "escaped.txt").exists()
        assert not (tmp_path / "escaped.txt").exists()

    def test_empty_result_raises_after_parse(self, parser, tmp_path):
        assert parser._load_blocks(tmp_path / "nonexistent") == []


class TestParseGuards:
    async def test_missing_file(self, parser, monkeypatch, tmp_path):
        monkeypatch.setattr(parser.s, "mineru_api_key", "k", raising=False)
        with pytest.raises(MinerUError, match="不存在"):
            await parser.parse(tmp_path / "nope.pdf", document_id="d")

    async def test_unsupported_suffix(self, parser, monkeypatch, tmp_path):
        monkeypatch.setattr(parser.s, "mineru_api_key", "k", raising=False)
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(MinerUError, match="不支持"):
            await parser.parse(f, document_id="d")

    async def test_missing_key_rejected_before_network(self, parser, monkeypatch, tmp_path):
        monkeypatch.setattr(parser.s, "mineru_api_key", "", raising=False)
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF-1.4")
        with pytest.raises(MinerUError, match="MINERU_API_KEY"):
            await parser.parse(f, document_id="d")
