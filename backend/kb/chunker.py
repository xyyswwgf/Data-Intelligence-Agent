"""
Markdown 切块 — 按标题层级分节，节内按段落累积到目标长度，相邻块保留重叠

每个块记录标题路径（如「指标口径 > GMV」），检索结果可以直接定位到小节，
向量化时把标题路径拼进文本，短段落也能带上上下文语义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.config import KB_CHUNK_OVERLAP, KB_CHUNK_SIZE

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Chunk:
    heading_path: str
    content: str


def split_markdown(markdown: str, chunk_size: int = KB_CHUNK_SIZE, overlap: int = KB_CHUNK_OVERLAP) -> list[Chunk]:
    sections = _sections(markdown)
    chunks: list[Chunk] = []
    for path, blocks in sections:
        for content in _pack(blocks, chunk_size, overlap):
            chunks.append(Chunk(heading_path=path, content=content))
    return chunks


def _sections(markdown: str) -> list[tuple[str, list[str]]]:
    """返回 [(标题路径, [段落块...])]，代码块和表格作为不可拆分的整体。"""
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str, list[str]]] = []
    blocks: list[str] = []
    buf: list[str] = []
    in_fence = False

    def flush_block():
        if buf and "".join(buf).strip():
            blocks.append("\n".join(buf).strip())
        buf.clear()

    def flush_section():
        flush_block()
        if blocks:
            sections.append((" > ".join(t for _, t in stack), list(blocks)))
        blocks.clear()

    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            buf.append(line)
            continue
        if in_fence:
            buf.append(line)
            continue
        heading = _HEADING.match(line)
        if heading:
            flush_section()
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2).strip()))
            continue
        if not line.strip():
            flush_block()
        else:
            # 表格行连续成块
            if buf and buf[-1].lstrip().startswith("|") != line.lstrip().startswith("|"):
                flush_block()
            buf.append(line)
    flush_section()
    return sections


def _pack(blocks: list[str], size: int, overlap: int) -> list[str]:
    pieces: list[str] = []
    for block in blocks:
        if len(block) <= size or block.lstrip().startswith(("|", "```", "~~~")):
            pieces.append(block)
        else:
            pieces.extend(_split_long(block, size))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > size:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            # 重叠从句子边界开始，避免半个词
            cut = re.search(r"[。！？；.!?;\n]\s*", tail)
            tail = tail[cut.end():] if cut else tail
            current = f"{tail}\n\n{piece}".strip() if tail else piece
        else:
            current = f"{current}\n\n{piece}".strip() if current else piece
    if current:
        chunks.append(current)
    return chunks


def _split_long(text: str, size: int) -> list[str]:
    sentences = re.split(r"(?<=[。！？；.!?;])\s*", text)
    out, cur = [], ""
    for sentence in sentences:
        if not sentence:
            continue
        while len(sentence) > size:  # 没有标点的超长句硬切
            out.append(sentence[:size])
            sentence = sentence[size:]
        if len(cur) + len(sentence) > size and cur:
            out.append(cur)
            cur = sentence
        else:
            cur += sentence
    if cur:
        out.append(cur)
    return out
