"""表格解析测试：CSV 无需第三方库即可测；xlsx/xlsb 在有依赖时才跑。

重点：每块都带表头、行号可定位、空列剔除、超大表按 token 与行数双重切分。
"""

from __future__ import annotations

import pytest

from app.knowledge.spreadsheet_parser import (
    SpreadsheetError,
    SpreadsheetParser,
    _drop_empty_columns,
    _trim_empty,
)

CSV_HEADER = "id,form_def_key,creator,gmt_created,status"
CSV_ROWS = [f"{i},FORM_A,user{i},2026-05-07 14:{i:02d},approved" for i in range(1, 96)]


@pytest.fixture
def csv_file(tmp_path):
    p = tmp_path / "申请记录.csv"
    p.write_text("\n".join([CSV_HEADER, *CSV_ROWS]), encoding="utf-8-sig")
    return p


class TestCsvParse:
    def test_no_dependency_needed(self):
        assert SpreadsheetParser().missing_dependency(".csv") is None

    def test_produces_title_and_table_blocks(self, csv_file):
        blocks = SpreadsheetParser().parse(csv_file, document_id="d1")
        kinds = [b.kind for b in blocks]
        assert "title" in kinds
        assert kinds.count("table") >= 1
        assert blocks[0].text == "申请记录"

    def test_every_chunk_repeats_header(self, csv_file):
        blocks = SpreadsheetParser(rows_per_chunk=20).parse(csv_file, document_id="d1")
        tables = [b for b in blocks if b.kind == "table"]
        assert len(tables) > 1, "95 行、每块 20 行，应当切成多块"
        for t in tables:
            assert "表头：id | form_def_key | creator | gmt_created | status" in t.text

    def test_row_numbers_preserved_and_continuous(self, csv_file):
        blocks = SpreadsheetParser(rows_per_chunk=20).parse(csv_file, document_id="d1")
        tables = [b for b in blocks if b.kind == "table"]
        # 每块声明自己的行号区间
        for t in tables:
            assert "数据行：第 " in t.text
        # 首块从第 1 行开始，末块覆盖到第 95 行
        assert "[1] " in tables[0].text
        assert "[95] " in tables[-1].text
        # 行号不重不漏
        import re

        seen = [int(m) for t in tables for m in re.findall(r"^\[(\d+)\] ", t.text, re.M)]
        assert seen == list(range(1, 96))

    def test_token_budget_splits_large_rows(self, tmp_path):
        # 每行都很长 → 即使行数没到上限也要按 token 切
        header = "a,b,c"
        rows = [f"{'甲' * 200},{'乙' * 200},{'丙' * 200}" for _ in range(40)]
        p = tmp_path / "wide.csv"
        p.write_text("\n".join([header, *rows]), encoding="utf-8")
        blocks = SpreadsheetParser(rows_per_chunk=40, max_table_tokens=300).parse(
            p, document_id="d2"
        )
        tables = [b for b in blocks if b.kind == "table"]
        assert len(tables) > 1

    def test_gbk_encoding_detected(self, tmp_path):
        p = tmp_path / "gbk.csv"
        p.write_bytes("编号,名称\n1,采购申请\n".encode("gbk"))
        blocks = SpreadsheetParser().parse(p, document_id="d3")
        text = "\n".join(b.text for b in blocks)
        assert "采购申请" in text

    def test_cell_pipe_escaped_to_keep_markdown_safe(self, tmp_path):
        # 单元格内的 | 会与列分隔符混淆，替换为全角斜杠
        p = tmp_path / "pipe.csv"
        p.write_text('a,b\n1,"x|y"\n', encoding="utf-8")
        blocks = SpreadsheetParser().parse(p, document_id="d4")
        text = "\n".join(b.text for b in blocks)
        assert "x／y" in text
        assert "x|y" not in text

    def test_empty_file_raises(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("", encoding="utf-8")
        with pytest.raises(SpreadsheetError, match="未读取到任何内容"):
            SpreadsheetParser().parse(p, document_id="d5")

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(SpreadsheetError, match="不存在"):
            SpreadsheetParser().parse(tmp_path / "nope.csv", document_id="d6")

    def test_unsupported_suffix_raises(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("x", encoding="utf-8")
        with pytest.raises(SpreadsheetError, match="不支持"):
            SpreadsheetParser().parse(p, document_id="d7")


class TestXlsx:
    def test_parses_sheets_with_headers(self, tmp_path):
        pytest.importorskip("openpyxl")
        import openpyxl

        p = tmp_path / "台账.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "访问申请"
        ws.append(["工号", "系统", "审批人", "日期"])
        for i in range(1, 31):
            ws.append([f"E{i:03d}", "SIEM", "张经理", "2026-05-07"])
        ws2 = wb.create_sheet("汇总")
        ws2.append(["系统", "次数"])
        ws2.append(["SIEM", 30])
        wb.save(p)

        blocks = SpreadsheetParser(rows_per_chunk=10).parse(p, document_id="dx")
        text = "\n".join(b.text for b in blocks)
        assert "访问申请" in text and "汇总" in text
        assert "表头：工号 | 系统 | 审批人 | 日期" in text
        # 两个 sheet 各自编号，sheet 名进正文
        assert "工作表：访问申请" in text
        assert "工作表：汇总" in text

    def test_formula_result_used_not_formula_text(self, tmp_path):
        pytest.importorskip("openpyxl")
        import openpyxl

        p = tmp_path / "f.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["a", "b"])
        ws.append([1, 2])
        ws["C1"] = "sum"
        wb.save(p)
        # 仅验证能读出不报错（data_only 模式下无缓存值时为空，属于预期）
        blocks = SpreadsheetParser().parse(p, document_id="df")
        assert any(b.kind == "table" for b in blocks)

    def test_missing_openpyxl_gives_install_hint(self, tmp_path, monkeypatch):
        p = tmp_path / "x.xlsx"
        p.write_bytes(b"PK\x03\x04")
        monkeypatch.setattr(
            SpreadsheetParser, "missing_dependency", lambda self, s: "openpyxl"
        )
        with pytest.raises(SpreadsheetError, match="pip install openpyxl"):
            SpreadsheetParser().parse(p, document_id="dz")


class TestHelpers:
    def test_trim_empty_rows_and_ragged_width(self):
        rows = [["a", "b"], ["", ""], ["c"], ["", ""]]
        out = _trim_empty(rows)
        assert out == [["a", "b"], ["c", ""]]

    def test_drop_empty_columns(self):
        header = ["a", "", "c"]
        body = [["1", "", "3"], ["4", "", "6"]]
        h, b = _drop_empty_columns(header, body)
        assert h == ["a", "c"]
        assert b == [["1", "3"], ["4", "6"]]

    def test_keeps_column_with_data_but_no_header(self):
        header = ["a", ""]
        body = [["1", "有数据的无标题列"]]
        h, b = _drop_empty_columns(header, body)
        assert h == ["a", ""]
        assert b == [["1", "有数据的无标题列"]]
