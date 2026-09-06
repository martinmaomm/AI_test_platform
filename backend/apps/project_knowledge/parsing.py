"""Deterministic, local-only parsers for project knowledge documents.

The parser returns source-addressable chunks.  It intentionally does not use a
LLM or a network service: parsing must be repeatable before a document reaches
the indexing stage.
"""
from __future__ import annotations

from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree


MAX_CHUNK_CHARS = 2_000
MAX_OFFICE_EXPANDED_BYTES = 100 * 1024 * 1024
_SUPPORTED_SUFFIXES = {".txt", ".md", ".docx", ".pdf", ".xlsx"}
_TEXT_HEADING = re.compile(r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节]|\d+(?:\.\d+)*[、.)]?)\s*\S+")
_XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def parse_document(path: str, filename: str) -> list[dict]:
    """Parse one supported document into source-addressable chunks.

    ``filename`` is deliberately used for format detection so callers can pass
    a storage path without an extension.  Old binary Word/Excel formats are
    rejected rather than returning a success-looking explanatory paragraph.
    """
    source_path = Path(path)
    suffix = Path(filename).suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的文档格式: {suffix or '无扩展名'}；请转换为 TXT、Markdown、DOCX、文本 PDF 或 XLSX")
    if not source_path.is_file():
        raise ValueError(f"文档不存在或不可读取: {path}")

    if suffix in {".docx", ".xlsx"}:
        with zipfile.ZipFile(source_path) as package:
            entries = package.infolist()
            if len(entries) > 10_000 or sum(item.file_size for item in entries) > MAX_OFFICE_EXPANDED_BYTES:
                raise ValueError("文档展开后体积过大，请拆分为较小文件后上传。")

    if suffix in {".txt", ".md"}:
        chunks = _parse_text(source_path.read_text(encoding="utf-8-sig"), "markdown" if suffix == ".md" else "text")
    elif suffix == ".docx":
        chunks = _parse_docx(source_path)
    elif suffix == ".pdf":
        chunks = _parse_pdf(source_path)
    else:
        chunks = _parse_xlsx(source_path)

    if not chunks:
        raise ValueError("文档未提取到可索引的文字内容")
    for ordinal, chunk in enumerate(chunks):
        chunk["ordinal"] = ordinal
        # Full titles remain in content; the DB heading is a bounded display label.
        chunk["heading"] = chunk["heading"][:500]
    return chunks


def _parse_text(text: str, kind: str) -> list[dict]:
    if kind == "markdown":
        sections = _markdown_sections(text)
    else:
        sections = _plain_text_sections(text)
    return _sections_to_chunks(sections, kind)


def _markdown_sections(text: str) -> list[dict]:
    headings = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*#*\s*$", text))
    if not headings:
        return [{"section_key": "section-0001", "heading": "", "content": text, "start": 0}]

    sections: list[dict] = []
    if headings[0].start() > 0:
        sections.append({
            "section_key": "preamble",
            "heading": "",
            "content": text[:headings[0].start()],
            "start": 0,
        })
    hierarchy: list[tuple[int, str]] = []
    for number, heading_match in enumerate(headings, start=1):
        level = len(heading_match.group(1))
        hierarchy = [(depth, title) for depth, title in hierarchy if depth < level]
        hierarchy.append((level, heading_match.group(2).strip()))
        content_start = heading_match.end()
        content_end = headings[number].start() if number < len(headings) else len(text)
        sections.append({
            "section_key": f"section-{number:04d}",
            "heading": " > ".join(title for _, title in hierarchy),
            "content": text[content_start:content_end],
            "start": content_start,
            "heading_start": heading_match.start(),
            "heading_end": heading_match.end(),
        })
    return sections


def _plain_text_sections(text: str) -> list[dict]:
    matches = [match for match in re.finditer(r"(?m)^.*$", text) if _TEXT_HEADING.match(match.group(0))]
    if not matches:
        return [{"section_key": "section-0001", "heading": "", "content": text, "start": 0}]

    sections: list[dict] = []
    if matches[0].start() > 0:
        sections.append({"section_key": "preamble", "heading": "", "content": text[:matches[0].start()], "start": 0})
    for number, match in enumerate(matches, start=1):
        content_start = match.end()
        content_end = matches[number].start() if number < len(matches) else len(text)
        sections.append({
            "section_key": f"section-{number:04d}",
            "heading": match.group(0).strip(),
            "content": text[content_start:content_end],
            "start": content_start,
            "heading_start": match.start(),
            "heading_end": match.end(),
        })
    return sections


def _sections_to_chunks(sections: list[dict], kind: str) -> list[dict]:
    chunks: list[dict] = []
    for section in sections:
        content = section["content"]
        heading = section["heading"]
        if content.strip():
            parts = _split_preserving_content(content, section["start"])
        elif heading:
            parts = [("", section.get("heading_start", section["start"]), section.get("heading_end", section["start"]))]
        else:
            continue
        for part, start, end in parts:
            if not part.strip() and not heading:
                continue
            # Repeating the hierarchy for every long-part split is intentional:
            # vector embeddings must not lose the title that gives a rule its
            # scope, and a heading-only rule remains a searchable source.
            semantic_content = f"{heading}\n{part}" if heading and part else (heading or part)
            chunks.append({
                "section_key": section["section_key"],
                "heading": heading,
                "content": semantic_content,
                "location": {
                    "kind": kind,
                    "character_start": start,
                    "character_end": end,
                    **({
                        "heading_character_start": section["heading_start"],
                        "heading_character_end": section["heading_end"],
                    } if heading and "heading_start" in section else {}),
                },
            })
    return chunks


def _split_preserving_content(content: str, base_offset: int) -> list[tuple[str, int, int]]:
    """Split long text on a natural boundary without dropping any characters."""
    if len(content) <= MAX_CHUNK_CHARS:
        return [(content, base_offset, base_offset + len(content))]

    parts: list[tuple[str, int, int]] = []
    start = 0
    separators = ("\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ", "; ", " ")
    while start < len(content):
        end = min(start + MAX_CHUNK_CHARS, len(content))
        if end < len(content):
            cut = 0
            window = content[start:end]
            for separator in separators:
                position = window.rfind(separator)
                if position >= 0:
                    cut = max(cut, start + position + len(separator))
            if cut > start:
                end = cut
        parts.append((content[start:end], base_offset + start, base_offset + end))
        start = end
    return parts


def _parse_docx(path: Path) -> list[dict]:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ValueError("当前服务缺少 DOCX 解析依赖") from exc

    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    chunks: list[dict] = []
    current_heading = ""
    current_key = "section-0001"
    current_heading_paragraph = None
    current_paragraphs: list[tuple[int, str]] = []
    paragraph_numbers = {paragraph._p: number for number, paragraph in enumerate(document.paragraphs, start=1)}

    def flush() -> None:
        nonlocal current_paragraphs
        text = "\n".join(item[1] for item in current_paragraphs)
        if text.strip():
            paragraph_start = current_paragraphs[0][0]
            paragraph_end = current_paragraphs[-1][0]
            for part, start, end in _split_preserving_content(text, 0):
                if part.strip():
                    chunks.append({
                        "section_key": current_key,
                        "heading": current_heading,
                        "content": f"{current_heading}\n{part}" if current_heading else part,
                        "location": {
                            "kind": "docx",
                            "paragraph_start": paragraph_start,
                            "paragraph_end": paragraph_end,
                            "character_start": start,
                            "character_end": end,
                            **({"heading_paragraph": current_heading_paragraph} if current_heading_paragraph else {}),
                        },
                    })
        elif current_heading:
            chunks.append({
                "section_key": current_key,
                "heading": current_heading,
                "content": current_heading,
                "location": {
                    "kind": "docx",
                    "paragraph_start": current_heading_paragraph,
                    "paragraph_end": current_heading_paragraph,
                    "character_start": 0,
                    "character_end": len(current_heading),
                    "heading_paragraph": current_heading_paragraph,
                },
            })
        current_paragraphs = []

    section_number = 0
    table_number = 0
    hierarchy: list[tuple[int, str]] = []
    for body_index, child in enumerate(document.element.body.iterchildren(), start=1):
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            paragraph_number = paragraph_numbers.get(child, body_index)
            text = paragraph.text
            style_name = (paragraph.style.name if paragraph.style else "").lower()
            if text.strip() and style_name.startswith("heading"):
                flush()
                section_number += 1
                current_key = f"section-{section_number:04d}"
                level_match = re.search(r"\d+", style_name)
                level = int(level_match.group()) if level_match else 1
                hierarchy = [(depth, title) for depth, title in hierarchy if depth < level]
                hierarchy.append((level, text.strip()))
                current_heading = " > ".join(title for _, title in hierarchy)
                current_heading_paragraph = paragraph_number
            else:
                current_paragraphs.append((paragraph_number, text))
            continue

        if child.tag != qn("w:tbl"):
            continue
        flush()
        table_number += 1
        table = Table(child, document)
        for row_number, row in enumerate(table.rows, start=1):
            row_text = " | ".join(cell.text.replace("\n", " ").strip() for cell in row.cells)
            if not row_text.strip():
                continue
            for part, start, end in _split_preserving_content(row_text, 0):
                chunks.append({
                    "section_key": f"{current_key}-table-{table_number:04d}-row-{row_number:04d}",
                    "heading": current_heading,
                    "content": f"{current_heading}\n{part}" if current_heading else part,
                    "location": {
                        "kind": "docx",
                        "body_index": body_index,
                        "table_index": table_number,
                        "row_start": row_number,
                        "row_end": row_number,
                        "character_start": start,
                        "character_end": end,
                        **({"heading_paragraph": current_heading_paragraph} if current_heading_paragraph else {}),
                    },
                })
    flush()
    return chunks


def _parse_pdf(path: Path) -> list[dict]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ValueError("当前服务缺少 PDF 解析依赖") from exc

    try:
        reader = PdfReader(str(path))
        page_texts = [(page_number, page.extract_text() or "") for page_number, page in enumerate(reader.pages, start=1)]
    except Exception as exc:
        raise ValueError(f"PDF 文字提取失败: {exc}") from exc

    if not any(text.strip() for _, text in page_texts):
        raise ValueError("扫描 PDF 或图片型 PDF 不含可提取文字；请先 OCR 后重新上传")

    scanned_pages = [
        number for number, text in page_texts
        if not text.strip() and bool(getattr(reader.pages[number - 1], 'images', []))
    ]
    if scanned_pages:
        positions = "、".join(str(number) for number in scanned_pages[:20])
        raise ValueError(f"PDF 第 {positions} 页包含无法提取文字的图片，正文不完整；请先 OCR 后重新上传。")

    chunks: list[dict] = []
    for page_number, text in page_texts:
        if not text.strip():
            continue
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        heading = first_line if _TEXT_HEADING.match(first_line) else ""
        for part, start, end in _split_preserving_content(text, 0):
            if part.strip():
                chunks.append({
                    "section_key": f"page-{page_number:04d}",
                    "heading": heading,
                    "content": part,
                    "location": {
                        "kind": "pdf",
                        "page_start": page_number,
                        "page_end": page_number,
                        "character_start": start,
                        "character_end": end,
                    },
                })
    return chunks


def _parse_xlsx(path: Path) -> list[dict]:
    """Read the textual cells of an XLSX package without an openpyxl dependency."""
    try:
        with zipfile.ZipFile(path) as workbook:
            shared_strings = _xlsx_shared_strings(workbook)
            sheets = _xlsx_sheets(workbook)
            chunks: list[dict] = []
            for sheet_number, (sheet_name, sheet_target) in enumerate(sheets, start=1):
                root = ElementTree.fromstring(workbook.read(f"xl/{sheet_target}"))
                for row in root.findall(f".//{_XML_NS}row"):
                    row_number = int(row.attrib.get("r", "0"))
                    cells = []
                    for cell in row.findall(f"{_XML_NS}c"):
                        value = _xlsx_cell_value(cell, shared_strings)
                        if value:
                            cells.append(value)
                    if not cells:
                        continue
                    content = " | ".join(cells)
                    for part, start, end in _split_preserving_content(content, 0):
                        chunks.append({
                            "section_key": f"sheet-{sheet_number:04d}-row-{row_number:06d}",
                            "heading": sheet_name,
                            "content": part,
                            "location": {
                                "kind": "xlsx",
                                "sheet": sheet_name,
                                "row_start": row_number,
                                "row_end": row_number,
                                "character_start": start,
                                "character_end": end,
                            },
                        })
            return chunks
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
        raise ValueError(f"XLSX 解析失败: {exc}") from exc


def _xlsx_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.itertext()) for node in root.findall(f"{_XML_NS}si")]


def _xlsx_sheets(workbook: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook_root = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
    relationships_root = ElementTree.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    relationship_targets = {
        relation.attrib["Id"]: relation.attrib["Target"].lstrip("/")
        for relation in relationships_root
    }
    sheets = []
    for sheet in workbook_root.findall(f".//{_XML_NS}sheet"):
        relationship_id = sheet.attrib[f"{_REL_NS}id"]
        target = relationship_targets[relationship_id].lstrip("/")
        if target.startswith("xl/"):
            target = target[3:]
        sheets.append((sheet.attrib["name"], target if target.startswith("worksheets/") else f"worksheets/{target}"))
    return sheets


def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(cell.itertext()).strip()
    value_node = cell.find(f"{_XML_NS}v")
    if value_node is None or value_node.text is None:
        formula_node = cell.find(f"{_XML_NS}f")
        return f"={formula_node.text}" if formula_node is not None and formula_node.text else ""
    value = value_node.text
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (IndexError, ValueError):
            return ""
    return value
