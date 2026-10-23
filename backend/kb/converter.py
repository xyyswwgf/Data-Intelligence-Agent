"""
文档 → Markdown 转换

所有导入的资料统一转成 Markdown，作为 Wiki 页面的正文：
  - .md / .markdown：原样保留
  - .txt：段落直通
  - .pdf：pypdf 逐页抽取文本，每页一个二级标题（便于引用定位页码）
  - .docx：python-docx 按文档顺序转换标题、列表、表格
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from backend.csv_importer import detect_encoding

SUPPORTED_TYPES = {"md": "markdown", "markdown": "markdown", "txt": "text", "pdf": "pdf", "docx": "docx"}


@dataclass
class ConvertedDocument:
    title: str
    markdown: str
    file_type: str


def file_type_of(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_TYPES:
        raise ValueError(f"不支持的文件类型 .{ext or '?'}，目前支持：Markdown、TXT、PDF、Word(.docx)")
    return ext if ext != "markdown" else "md"


def convert_to_markdown(filename: str, data: bytes) -> ConvertedDocument:
    file_type = file_type_of(filename)
    stem = filename.rsplit(".", 1)[0].strip() or "未命名文档"
    if file_type in {"md", "txt"}:
        text, _ = detect_encoding(data)
        markdown = _normalize(text) if file_type == "md" else _text_to_markdown(text)
    elif file_type == "pdf":
        markdown = _pdf_to_markdown(data)
    else:
        markdown = _docx_to_markdown(data)

    if not markdown.strip():
        raise ValueError("文档中没有可提取的文字内容")
    title = _first_heading(markdown) or stem
    if not re.match(r"^#\s", markdown.lstrip()):
        markdown = f"# {title}\n\n{markdown}"
    return ConvertedDocument(title=title[:300], markdown=markdown.strip() + "\n", file_type=file_type)


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _text_to_markdown(text: str) -> str:
    text = _normalize(text)
    # 纯文本中的 #、* 等字符不应被当成 Markdown 语法
    return re.sub(r"^([#>*\-+])", r"\\\1", text, flags=re.MULTILINE)


def _first_heading(markdown: str) -> str | None:
    match = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _pdf_to_markdown(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
    except Exception as exc:
        raise ValueError(f"PDF 无法解析: {exc}") from exc

    parts = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            content = page.extract_text() or ""
        except Exception:
            content = ""
        content = _join_pdf_lines(content)
        if content:
            parts.append(f"## 第 {number} 页\n\n{content}")
    if not parts:
        raise ValueError("PDF 中没有可提取的文字（可能是扫描件，需要 OCR，暂不支持）")
    return "\n\n".join(parts)


def _join_pdf_lines(text: str) -> str:
    """PDF 抽出的文本按排版硬换行，把段内换行拼回去（中文直接拼接，英文补空格）。"""
    lines = [ln.strip() for ln in _normalize(text).split("\n")]
    paragraphs, buf = [], ""
    for line in lines:
        if not line:
            if buf:
                paragraphs.append(buf)
                buf = ""
            continue
        if buf and re.search(r"[。！？；：.!?;:]$", buf):
            paragraphs.append(buf)
            buf = line
        elif buf:
            sep = "" if re.search(r"[\u4e00-\u9fff]$", buf) or re.match(r"[\u4e00-\u9fff]", line) else " "
            buf += sep + line
        else:
            buf = line
    if buf:
        paragraphs.append(buf)
    return "\n\n".join(paragraphs)


def _docx_to_markdown(data: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"Word 文档无法解析（仅支持 .docx）: {exc}") from exc

    lines: list[str] = []
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            line = _docx_paragraph(Paragraph(child, document))
            if line is not None:
                lines.append(line)
        elif tag == "tbl":
            lines.append(_docx_table(Table(child, document)))
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(l for l in lines if l is not None)).strip()


def _docx_paragraph(paragraph) -> str | None:
    text = paragraph.text.strip()
    if not text:
        return None
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    level = None
    if style == "Title":
        level = 1
    else:
        match = re.match(r"(?:Heading|标题)\s*(\d)", style)
        if match:
            level = min(int(match.group(1)) + 1, 6)  # Word 的标题 1 → ##，# 留给文档标题
    if level:
        return f"{'#' * level} {text}"
    if "List Number" in style or "编号" in style:
        return f"1. {text}"
    if "List" in style or "项目符号" in style:
        return f"- {text}"
    return text


def _docx_table(table) -> str:
    rows = []
    for row in table.rows:
        cells = [cell.text.strip().replace("|", "\\|").replace("\n", " ") for cell in row.cells]
        rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)
