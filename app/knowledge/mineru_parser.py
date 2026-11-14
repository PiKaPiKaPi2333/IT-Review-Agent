"""MinerU 官方 API 客户端（远程解析，无需本地部署）。

调用的是「精准解析 API」(v4)，四步：

    1. POST /file-urls/batch        申请上传链接（返回 batch_id + file_urls）
    2. PUT  <file_url>              上传文件（**不要**设置 Content-Type）
    3. GET  /extract-results/batch/{batch_id}   轮询到 done / failed
    4. GET  <full_zip_url>          下载结果 zip，解压取 content_list.json

上传后服务端会自动扫描并提交解析任务，无需再手动创建任务。
Token 在 https://mineru.net 个人中心 → API 管理 创建，填到 MINERU_API_KEY。

限额（官方）：单文件 ≤ 200MB、≤ 600 页；上传链接 24 小时有效。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import zipfile
from pathlib import Path

import httpx

from app.infra.config import Settings, get_settings
from app.knowledge.chunker import ParsedBlock

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp", ".html",
}

MAX_FILE_BYTES = 200 * 1024 * 1024
TERMINAL_STATES = {"done", "failed"}

# 官方错误码 → 可读提示
ERROR_HINTS = {
    "A0202": "Token 无效，请检查 MINERU_API_KEY 是否正确",
    "A0211": "Token 已过期，请在 MinerU 个人中心重新创建",
    "-60002": "不支持的文件格式",
    "-60003": "文件读取失败",
    "-60004": "文件为空",
    "-60005": "文件超过 200MB 上限",
    "-60006": "页数超过上限",
    "-60008": "文件读取超时",
    "-60010": "解析失败",
    "-60018": "已达当日额度上限",
}


class MinerUError(RuntimeError):
    pass


class MinerUParser:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()

    # ------------------------------------------------------------------
    # 配置检查
    # ------------------------------------------------------------------
    def is_available(self) -> tuple[bool, str]:
        if not self.s.mineru_api_key:
            return False, (
                "未配置 MINERU_API_KEY。请在 https://mineru.net 个人中心 → API 管理 "
                "创建 Token，填入 .env 的 MINERU_API_KEY。"
            )
        return True, f"{self.s.mineru_api_base} (model={self.s.mineru_model_version})"

    def _headers(self, *, json_body: bool = True) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.s.mineru_api_key}"}
        if json_body:
            h["Content-Type"] = "application/json"
            h["Accept"] = "*/*"
        return h

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    async def parse(
        self,
        file_path: str | Path,
        *,
        document_id: str,
        output_root: str | Path | None = None,
    ) -> list[ParsedBlock]:
        ok, detail = self.is_available()
        if not ok:
            raise MinerUError(detail)

        src = Path(file_path)
        if not src.exists():
            raise MinerUError(f"文件不存在：{src}")
        suffix = src.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise MinerUError(f"不支持的文件类型：{suffix}")
        size = src.stat().st_size
        if size > MAX_FILE_BYTES:
            raise MinerUError(
                f"文件 {size / 1024 / 1024:.1f}MB 超过 MinerU API 的 200MB 上限"
            )

        logger.info("MinerU 解析开始：%s (%.1fMB)", src.name, size / 1024 / 1024)

        async with httpx.AsyncClient(
            base_url=self.s.mineru_api_base.rstrip("/"),
            timeout=httpx.Timeout(self.s.mineru_request_timeout, connect=30.0),
        ) as client:
            batch_id, upload_url = await self._request_upload_url(client, src.name)
            await self._upload(client, upload_url, src)
            zip_url, state, err_msg = await self._poll(client, batch_id, src.name)
            if state != "done" or not zip_url:
                raise MinerUError(f"MinerU 解析失败：{err_msg or state}")
            zip_bytes = await self._download(client, zip_url)

        blocks = self._extract_zip(zip_bytes, document_id, output_root)
        if not blocks:
            raise MinerUError("MinerU 返回结果中没有可解析内容")
        logger.info("MinerU 解析完成：%s，共 %d 块", src.name, len(blocks))
        return blocks

    # ------------------------------------------------------------------
    # 步骤 1：申请上传链接
    # ------------------------------------------------------------------
    async def _request_upload_url(
        self, client: httpx.AsyncClient, filename: str
    ) -> tuple[str, str]:
        payload: dict = {
            "files": [{"name": filename, "data_id": _safe_data_id(filename)}],
            "model_version": self.s.mineru_model_version,
        }
        if self.s.mineru_is_ocr:
            payload["is_ocr"] = True
        if self.s.mineru_language:
            payload["language"] = self.s.mineru_language
        if self.s.mineru_page_ranges:
            payload["page_ranges"] = self.s.mineru_page_ranges

        try:
            resp = await client.post(
                "/file-urls/batch", headers=self._headers(), json=payload
            )
        except httpx.HTTPError as e:
            raise MinerUError(f"申请上传链接失败（网络）：{e}") from e

        data = self._unwrap(resp, "申请上传链接")
        batch_id = data.get("batch_id")
        urls = data.get("file_urls") or []
        if not batch_id or not urls:
            raise MinerUError(f"申请上传链接返回缺少 batch_id/file_urls：{data}")
        return batch_id, urls[0]

    # ------------------------------------------------------------------
    # 步骤 2：上传文件
    # ------------------------------------------------------------------
    async def _upload(self, client: httpx.AsyncClient, upload_url: str, src: Path) -> None:
        # 官方明确要求：上传时不要设置 Content-Type，否则 OSS 签名校验失败
        try:
            with src.open("rb") as f:
                resp = await client.put(upload_url, content=f.read())
        except httpx.HTTPError as e:
            raise MinerUError(f"上传文件失败（网络）：{e}") from e
        if resp.status_code not in (200, 201, 204):
            raise MinerUError(f"上传文件失败：HTTP {resp.status_code} {resp.text[:300]}")
        logger.info("文件已上传，等待服务端扫描并排队解析")

    # ------------------------------------------------------------------
    # 步骤 3：轮询结果
    # ------------------------------------------------------------------
    async def _poll(
        self, client: httpx.AsyncClient, batch_id: str, filename: str
    ) -> tuple[str | None, str, str | None]:
        deadline = asyncio.get_event_loop().time() + self.s.mineru_timeout
        interval = self.s.mineru_poll_interval
        last_state = "waiting-file"
        waiting_since = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(interval)
            try:
                resp = await client.get(
                    f"/extract-results/batch/{batch_id}", headers=self._headers(json_body=False)
                )
            except httpx.HTTPError as e:
                logger.warning("轮询失败（将继续重试）：%s", e)
                continue

            data = self._unwrap(resp, "查询解析结果")
            results = data.get("extract_result") or []
            item = _pick_result(results, filename)
            if item is None:
                continue

            state = str(item.get("state") or "").lower()
            if state != last_state:
                logger.info("MinerU 任务状态：%s → %s", last_state, state)
                last_state = state

            if state in TERMINAL_STATES:
                return item.get("full_zip_url"), state, item.get("err_msg")

            if state == "waiting-file":
                # 文件已上传但服务端还没扫描到，超过 2 分钟视为异常
                if asyncio.get_event_loop().time() - waiting_since > 120:
                    raise MinerUError(
                        "MinerU 长时间处于 waiting-file，可能上传未生效，请重试"
                    )
            else:
                waiting_since = asyncio.get_event_loop().time()

        raise MinerUError(f"等待解析超时（>{self.s.mineru_timeout}s）")

    # ------------------------------------------------------------------
    # 步骤 4：下载并解压
    # ------------------------------------------------------------------
    async def _download(self, client: httpx.AsyncClient, zip_url: str) -> bytes:
        try:
            resp = await client.get(zip_url)
        except httpx.HTTPError as e:
            raise MinerUError(f"下载解析结果失败（网络）：{e}") from e
        if resp.status_code != 200:
            raise MinerUError(f"下载解析结果失败：HTTP {resp.status_code}")
        return resp.content

    def _extract_zip(
        self,
        zip_bytes: bytes,
        document_id: str,
        output_root: str | Path | None,
    ) -> list[ParsedBlock]:
        # 保留原始解析产物，便于溯源与问题复现
        dest = Path(output_root or self.s.mineru_output_dir) / document_id
        dest.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                _safe_extract(zf, dest)
        except zipfile.BadZipFile as e:
            raise MinerUError(f"解析结果不是合法 zip：{e}") from e

        return self._load_blocks(dest)

    # ------------------------------------------------------------------
    # 响应解包 / 错误映射
    # ------------------------------------------------------------------
    def _unwrap(self, resp: httpx.Response, action: str) -> dict:
        """兼容两种响应外壳：

        - 业务层 `{"code": 0, "data": {...}}`（code 为 0 表示成功）
        - 网关层 `{"success": true, "msgCode": 200, "data": {...}}`
        """
        try:
            body = resp.json()
        except ValueError:
            raise MinerUError(f"{action}失败：HTTP {resp.status_code} {resp.text[:300]}")

        if not isinstance(body, dict):
            raise MinerUError(f"{action}失败：响应格式异常 {str(body)[:300]}")

        code = body.get("code")
        msg_code = body.get("msgCode")
        success = body.get("success")

        ok = (
            code == 0
            or msg_code == 200
            or success is True
        )
        if ok:
            return body.get("data") or {}

        shown = code if code is not None else msg_code
        msg = body.get("msg") or body.get("message") or str(body)[:300]
        hint = ERROR_HINTS.get(str(shown))
        detail = f"{action}失败：[{shown}] {msg}" + (f"（{hint}）" if hint else "")
        raise MinerUError(detail)

    # ------------------------------------------------------------------
    # 产物解析（与之前一致：content_list.json 优先，md 兜底）
    # ------------------------------------------------------------------
    def _load_blocks(self, out_dir: Path) -> list[ParsedBlock]:
        content_lists = sorted(out_dir.rglob("*content_list*.json"))
        if content_lists:
            blocks: list[ParsedBlock] = []
            for f in content_lists:
                blocks.extend(self._from_content_list(f))
            if blocks:
                return blocks

        for md in sorted(out_dir.rglob("*.md")):
            return self._from_markdown(md)
        return []

    def _from_content_list(self, path: Path) -> list[ParsedBlock]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("读取 %s 失败：%s", path.name, e)
            return []

        items = raw if isinstance(raw, list) else raw.get("content", [])
        blocks: list[ParsedBlock] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = (item.get("type") or "text").lower()
            page_idx = item.get("page_idx")
            page = (page_idx + 1) if isinstance(page_idx, int) else None

            if kind == "title":
                text = (item.get("text") or "").strip()
                if text:
                    level = item.get("text_level") or item.get("level") or 1
                    try:
                        level = int(level)
                    except (TypeError, ValueError):
                        level = 1
                    blocks.append(
                        ParsedBlock(text=text, page=page, kind="title", level=max(1, level))
                    )
            elif kind == "table":
                body = (
                    item.get("table_body")
                    or item.get("text")
                    or _html_table_to_text(item.get("table_html"))
                )
                body = (body or "").strip()
                caption = " ".join(
                    str(c.get("text", "")) if isinstance(c, dict) else str(c)
                    for c in (item.get("table_caption") or [])
                ).strip()
                text = f"{caption}\n{body}".strip() if caption else body
                if text:
                    blocks.append(ParsedBlock(text=text, page=page, kind="table"))
            elif kind in {"text", "list", "equation", "code", "aside_text"}:
                text = (item.get("text") or "").strip()
                if text:
                    blocks.append(ParsedBlock(text=text, page=page, kind="text"))
            # image / footer / page_number 等无法作为证据引用，跳过
        return blocks

    def _from_markdown(self, path: Path) -> list[ParsedBlock]:
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks: list[ParsedBlock] = []
        for line in text.splitlines():
            m = re.match(r"^(#{1,6})\s+(.*)$", line)
            if m:
                blocks.append(
                    ParsedBlock(text=m.group(2).strip(), kind="title", level=len(m.group(1)))
                )
            elif line.strip():
                blocks.append(ParsedBlock(text=line.strip(), kind="text"))
        # 无页码信息：按块序近似（每 20 块算一页）
        for i in range(0, len(blocks), 20):
            page = i // 20 + 1
            for b in blocks[i : i + 20]:
                b.page = page
        return blocks


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def _safe_data_id(filename: str) -> str:
    """data_id 只允许字母数字下划线短横线点，≤128。"""
    stem = Path(filename).stem
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)
    return (cleaned or "doc")[:120]


def _pick_result(results: list, filename: str) -> dict | None:
    """批量结果里挑出当前文件。只有一个文件时直接取第一个。"""
    if not results:
        return None
    if len(results) == 1:
        return results[0]
    for item in results:
        if str(item.get("file_name", "")) == filename:
            return item
    return results[0]


def _html_table_to_text(html: str | None) -> str:
    """极简 HTML 表格转文本（仅在 table_body 缺失时兜底）。"""
    if not html:
        return ""
    text = re.sub(r"</t[dh]>", " | ", html, flags=re.I)
    text = re.sub(r"</tr>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """防 zip slip：只解压到 dest 内部。"""
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if not str(target).startswith(str(dest)):
            logger.warning("跳过可疑路径：%s", member.filename)
            continue
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, target.open("wb") as out:
            out.write(src.read())


def extract_parse_meta(blocks: list[ParsedBlock]) -> dict:
    """给 documents.parse_meta 用的摘要。"""
    pages = {b.page for b in blocks if b.page is not None}
    return {
        "blocks": len(blocks),
        "pages": len(pages),
        "tables": sum(1 for b in blocks if b.kind == "table"),
        "titles": sum(1 for b in blocks if b.kind == "title"),
        "chars": sum(len(b.text) for b in blocks),
        "source": "mineru-api",
    }
