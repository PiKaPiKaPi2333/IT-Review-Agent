"""表格文件解析（xlsx / xlsb / csv）—— MinerU API 不支持这些格式，本地处理。

审计资料里的台账、申请单、检查表大多是表格，直接交给 LLM 检索时有两个要求：

1. **每行必须自带表头**。表格切成 chunk 后，单独一行没有列名就完全无法理解，
   所以每个 chunk 都重复一遍表头。
2. **保留行号**。审计追问"第 37 行那条申请"时必须能定位，因此行号写进正文，
   并记入 metadata（sheet / row_start / row_end）。

产物与 MinerU 解析结果同构（`ParsedBlock`），下游切分、入库无需区分来源。
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from pathlib import Path

from app.knowledge.chunker import ParsedBlock, count_tokens

logger = logging.getLogger(__name__)

SPREADSHEET_SUFFIXES = {".xlsx", ".xlsm", ".xlsb", ".csv"}

# 每个 sheet 的起始行数；超过 token 预算会自动再拆
DEFAULT_ROWS_PER_CHUNK = 40
MAX_CELL_CHARS = 300
MAX_TABLE_TOKENS = 1500


class SpreadsheetError(RuntimeError):
    pass


@dataclass
class SheetData:
    name: str
    rows: list[list[str]]


class SpreadsheetParser:
    """把工作簿/CSV 转成带表头与行号的文本块。"""

    def __init__(
        self,
        rows_per_chunk: int = DEFAULT_ROWS_PER_CHUNK,
        max_table_tokens: int = MAX_TABLE_TOKENS,
    ):
        self.rows_per_chunk = rows_per_chunk
        self.max_table_tokens = max_table_tokens

    # ------------------------------------------------------------------
    # 能力检查：按格式分别判断，CSV 无需任何第三方库
    # ------------------------------------------------------------------
    def missing_dependency(self, suffix: str) -> str | None:
        suffix = suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            try:
                import openpyxl  # noqa: F401
            except ImportError:
                return "openpyxl"
        elif suffix == ".xlsb":
            try:
                import pyxlsb  # noqa: F401
            except ImportError:
                return "pyxlsb"
        return None

    def is_available(self) -> tuple[bool, str]:
        missing: list[str] = []
        if self.missing_dependency(".xlsx") == "openpyxl":
            missing.append("openpyxl（xlsx/xlsm）")
        if self.missing_dependency(".xlsb") == "pyxlsb":
            missing.append("pyxlsb（xlsb）")
        if missing:
            return False, (
                "缺少表格解析依赖：" + "、".join(missing) + "\n"
                "请安装：pip install openpyxl pyxlsb\n"
                "（csv 不受影响，无需依赖）"
            )
        return True, "openpyxl + pyxlsb（csv 内置）"

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def parse(self, file_path: str | Path, *, document_id: str) -> list[ParsedBlock]:
        path = Path(file_path)
        if not path.exists():
            raise SpreadsheetError(f"文件不存在：{path}")
        suffix = path.suffix.lower()

        missing = self.missing_dependency(suffix)
        if missing:
            raise SpreadsheetError(
                f"解析 {suffix} 需要 {missing}，请执行：pip install {missing}"
            )

        if suffix == ".csv":
            sheets = [self._read_csv(path)]
        elif suffix in {".xlsx", ".xlsm"}:
            sheets = self._read_xlsx(path)
        elif suffix == ".xlsb":
            sheets = self._read_xlsb(path)
        else:
            raise SpreadsheetError(f"不支持的表格格式：{suffix}")

        blocks: list[ParsedBlock] = []
        for ordinal, sheet in enumerate(sheets, 1):
            if not sheet.rows:
                continue
            blocks.extend(self._sheet_to_blocks(sheet, page=ordinal))
        if not blocks:
            raise SpreadsheetError(f"{path.name} 未读取到任何内容")
        logger.info("%s 解析完成：%d 个 sheet，%d 块", path.name, len(sheets), len(blocks))
        return blocks

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def _read_xlsx(self, path: Path) -> list[SheetData]:
        try:
            import openpyxl
        except ImportError as e:
            raise SpreadsheetError("缺少 openpyxl，请执行 pip install openpyxl") from e

        try:
            # data_only=True：取公式的计算结果而非公式串；read_only 省内存
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        except Exception as e:  # noqa: BLE001
            raise SpreadsheetError(f"打开 xlsx 失败：{e}") from e

        sheets: list[SheetData] = []
        try:
            for ws in wb.worksheets:
                rows = [
                    [self._cell(v) for v in row]
                    for row in ws.iter_rows(values_only=True)
                ]
                rows = _trim_empty(rows)
                if rows:
                    sheets.append(SheetData(name=ws.title, rows=rows))
        finally:
            wb.close()
        return sheets

    def _read_xlsb(self, path: Path) -> list[SheetData]:
        try:
            from pyxlsb import open_workbook
        except ImportError as e:
            raise SpreadsheetError("缺少 pyxlsb，请执行 pip install pyxlsb") from e

        sheets: list[SheetData] = []
        try:
            with open_workbook(path) as wb:
                for name in wb.sheets:
                    with wb.get_sheet(name) as ws:
                        rows = [[self._cell(c.v) for c in row] for row in ws.rows()]
                    rows = _trim_empty(rows)
                    if rows:
                        sheets.append(SheetData(name=name, rows=rows))
        except Exception as e:  # noqa: BLE001
            raise SpreadsheetError(f"读取 xlsb 失败：{e}") from e
        return sheets

    def _read_csv(self, path: Path) -> SheetData:
        # 审计系统导出的 CSV 常见 utf-8-sig / gbk
        raw = path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "utf-16"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", errors="replace")
            logger.warning("%s 编码无法自动识别，按 utf-8 容错解码", path.name)

        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel

        rows = [[self._cell(c) for c in row] for row in csv.reader(io.StringIO(text), dialect)]
        return SheetData(name=path.stem, rows=_trim_empty(rows))

    # ------------------------------------------------------------------
    # 转换
    # ------------------------------------------------------------------
    def _cell(self, value) -> str:
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        s = str(value).strip().replace("\n", " ").replace("|", "／")
        return s[:MAX_CELL_CHARS]

    def _sheet_to_blocks(self, sheet: SheetData, *, page: int) -> list[ParsedBlock]:
        rows = sheet.rows
        header = rows[0]
        body = rows[1:]

        # 去掉整列为空的行（尾部空行、分隔行）
        header, body = _drop_empty_columns(header, body)

        blocks: list[ParsedBlock] = [
            ParsedBlock(
                text=f"{sheet.name}",
                kind="title",
                level=1,
                page=page,
                section=sheet.name,
            )
        ]

        sheet_desc = f"工作表「{sheet.name}」，表头 {len([h for h in header if h])} 列，数据 {len(body)} 行"
        blocks.append(ParsedBlock(text=sheet_desc, kind="text", page=page, section=sheet.name))

        if not body:
            return blocks

        header_line = " | ".join(h or f"列{i + 1}" for i, h in enumerate(header))
        start = 0
        while start < len(body):
            chunk_rows, end = self._take_rows(body, start, header_line)
            lines = [
                f"工作表：{sheet.name}",
                f"表头：{header_line}",
                f"数据行：第 {start + 1}–{end} 行（共 {len(body)} 行）",
                "",
            ]
            for offset, row in enumerate(chunk_rows, start=start + 1):
                cells = " | ".join(c or "" for c in row).rstrip(" |")
                lines.append(f"[{offset}] {cells}")

            blocks.append(
                ParsedBlock(
                    text="\n".join(lines),
                    kind="table",
                    page=page,
                    section=sheet.name,
                )
            )
            # metadata 由 block 携带不便，这里用 section 后缀传递给切分器
            blocks[-1].section = sheet.name
            start = end

        return blocks

    def _take_rows(
        self, body: list[list[str]], start: int, header_line: str
    ) -> tuple[list[list[str]], int]:
        """在行数与 token 预算内尽量多取行，返回 (rows, 结束行号不含)。"""
        taken: list[list[str]] = []
        used = count_tokens(header_line) + 40
        end = start
        while end < len(body) and len(taken) < self.rows_per_chunk:
            row_text = " | ".join(c or "" for c in body[end])
            cost = count_tokens(row_text)
            if taken and used + cost > self.max_table_tokens:
                break
            taken.append(body[end])
            used += cost
            end += 1
        return taken, end


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def _trim_empty(rows: list[list[str]]) -> list[list[str]]:
    """去掉全空的行与尾部空行，并统一列宽。"""
    cleaned = [r for r in rows if any((c or "").strip() for c in r)]
    if not cleaned:
        return []
    width = max(len(r) for r in cleaned)
    return [r + [""] * (width - len(r)) for r in cleaned]


def _drop_empty_columns(
    header: list[str], body: list[list[str]]
) -> tuple[list[str], list[list[str]]]:
    if not header:
        return header, body
    keep = [
        i
        for i in range(len(header))
        if (header[i] or "").strip() or any((r[i] or "").strip() for r in body[:200] if i < len(r))
    ]
    if not keep:
        return header, body
    return (
        [header[i] for i in keep],
        [[r[i] if i < len(r) else "" for i in keep] for r in body],
    )


def extract_spreadsheet_meta(sheets_blocks: list[ParsedBlock]) -> dict:
    tables = [b for b in sheets_blocks if b.kind == "table"]
    return {
        "blocks": len(sheets_blocks),
        "pages": len({b.page for b in sheets_blocks if b.page is not None}),
        "tables": len(tables),
        "titles": sum(1 for b in sheets_blocks if b.kind == "title"),
        "chars": sum(len(b.text) for b in sheets_blocks),
        "source": "local-spreadsheet",
    }
