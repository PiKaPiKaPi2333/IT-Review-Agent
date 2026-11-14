"""语义切分：按标题层级 + token 窗口，表格整块保留。

输入是 MinerU 解析出的 blocks（含 page / 类型），输出带元数据的 chunk 列表。
默认 512 token、重叠 64（架构 §5.1）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

import tiktoken

from app.infra.config import get_settings

_ENC = None


def _enc():
    global _ENC
    if _ENC is None:
        _ENC = tiktoken.get_encoding("cl100k_base")
    return _ENC


def count_tokens(text: str) -> int:
    return len(_enc().encode(text or ""))


@dataclass
class ParsedBlock:
    """MinerU 输出的一块内容。"""

    text: str
    page: int | None = None
    kind: str = "text"  # text / title / table / list
    level: int = 0      # title 的层级
    section: str = ""


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    text: str
    page: int | None
    section: str
    metadata: dict = field(default_factory=dict)


class SemanticChunker:
    def __init__(
        self,
        chunk_size: int | None = None,
        overlap: int | None = None,
    ):
        s = get_settings()
        self.chunk_size = chunk_size or s.chunk_size_tokens
        self.overlap = overlap or s.chunk_overlap_tokens
        if self.overlap >= self.chunk_size:
            self.overlap = max(0, self.chunk_size // 8)

    def split(
        self,
        blocks: list[ParsedBlock],
        *,
        document_id: str,
        extra_meta: dict | None = None,
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        section_path: list[tuple[int, str]] = []
        buffer: list[ParsedBlock] = []
        buffer_tokens = 0

        def flush() -> None:
            nonlocal buffer, buffer_tokens
            if not buffer:
                return
            text = _join_blocks(buffer)
            if text.strip():
                chunks.append(
                    self._make_chunk(
                        document_id, text, buffer, section_path, extra_meta, len(chunks)
                    )
                )
            # 尾部重叠：保留最后若干块，尽量不超过 overlap token
            tail: list[ParsedBlock] = []
            acc = 0
            for b in reversed(buffer):
                if acc >= self.overlap:
                    break
                if b.kind == "table":  # 表格不参与重叠，避免重复引用
                    break
                tail.insert(0, b)
                acc += count_tokens(b.text)
            buffer = tail
            buffer_tokens = acc

        for block in blocks:
            if block.kind == "title":
                # 标题变化：同一层级以上先收尾，保证 chunk 不跨章节
                level = block.level or 1
                section_path = [(lv, t) for lv, t in section_path if lv < level]
                section_path.append((level, block.text.strip()))
                flush()
                buffer = []
                buffer_tokens = 0
                buffer.append(block)
                buffer_tokens += count_tokens(block.text)
                continue

            if block.kind == "table":
                flush()
                buffer = []
                buffer_tokens = 0
                chunks.append(
                    self._make_chunk(
                        document_id, block.text, [block], section_path, extra_meta, len(chunks)
                    )
                )
                continue

            tokens = count_tokens(block.text)
            if tokens > self.chunk_size:
                # 单个超长段落：先 flush，再按句子硬切
                flush()
                buffer = []
                buffer_tokens = 0
                for piece in _split_long_text(block.text, self.chunk_size, self.overlap):
                    chunks.append(
                        self._make_chunk(
                            document_id,
                            piece,
                            [ParsedBlock(piece, block.page, block.kind, section=block.section)],
                            section_path,
                            extra_meta,
                            len(chunks),
                        )
                    )
                continue

            if buffer_tokens + tokens > self.chunk_size and buffer:
                flush()
            buffer.append(block)
            buffer_tokens += tokens

        flush()
        return chunks

    def _make_chunk(
        self,
        document_id: str,
        text: str,
        blocks: list[ParsedBlock],
        section_path: list[tuple[int, str]],
        extra_meta: dict | None,
        seq: int,
    ) -> Chunk:
        section = " > ".join(t for _, t in section_path)[:500]
        pages = [b.page for b in blocks if b.page is not None]
        page = pages[0] if pages else None
        digest = hashlib.sha1(f"{document_id}|{seq}|{text[:200]}".encode()).hexdigest()[:16]
        metadata = {
            "chunk_seq": seq,
            "chunk_type": "table" if any(b.kind == "table" for b in blocks) else "text",
            **(extra_meta or {}),
        }
        return Chunk(
            chunk_id=f"{document_id}-{digest}",
            document_id=document_id,
            text=text,
            page=page,
            section=section,
            metadata=metadata,
        )


def _join_blocks(blocks: list[ParsedBlock]) -> str:
    parts: list[str] = []
    for b in blocks:
        t = b.text.strip()
        if not t:
            continue
        if b.kind == "title":
            parts.append(f"{'#' * min(b.level or 1, 6)} {t}")
        elif b.kind == "table":
            parts.append(t)
        else:
            parts.append(t)
    return "\n\n".join(parts)


_SENT_SPLIT = re.compile(r"(?<=[。！？；\n])")


def _split_long_text(text: str, size: int, overlap: int) -> list[str]:
    sentences = [s for s in _SENT_SPLIT.split(text) if s.strip()]
    pieces: list[str] = []
    current: list[str] = []
    tokens = 0
    for sent in sentences:
        t = count_tokens(sent)
        while t > size:  # 极端情况：单句超长，硬按字符截
            head_chars = max(1, size * 2)
            pieces.append(sent[:head_chars])
            sent = sent[head_chars:]
            t = count_tokens(sent)
        if tokens + t > size and current:
            pieces.append("".join(current))
            tail: list[str] = []
            acc = 0
            for s in reversed(current):
                if acc >= overlap:
                    break
                tail.insert(0, s)
                acc += count_tokens(s)
            current = tail
            tokens = acc
        current.append(sent)
        tokens += t
    if current:
        pieces.append("".join(current))
    return [p.strip() for p in pieces if p.strip()]
