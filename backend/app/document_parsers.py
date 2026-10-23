import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docx import Document as WordDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from langchain_community.document_loaders import BSHTMLLoader, CSVLoader, JSONLoader
from pypdf import PdfReader

from app.parsers import parse_xlsx, parse_pptx

_whisper_model = None


@dataclass(slots=True)
class ParseResult:
    parser_name: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    structured: dict[str, Any] | None = None


def join_documents(documents) -> str:
    return "\n\n".join(document.page_content for document in documents if document.page_content.strip())


def markdown_content_list(markdown: str) -> list[dict[str, Any]]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", markdown) if block.strip()]
    result = []
    for block in blocks:
        result.append(
            {
                "type": "title" if re.match(r"^#{1,6}\s+", block) else "text",
                "text": re.sub(r"^#{1,6}\s+", "", block),
            }
        )
    return result


def parse_docx_markdown(path: Path) -> ParseResult:
    document = WordDocument(path)
    markdown_blocks: list[str] = []
    content_list: list[dict[str, Any]] = []
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in block.rows]
            table_text = "\n".join(row for row in rows if row.strip(" |"))
            if table_text:
                markdown_blocks.append(table_text)
                content_list.append(
                    {
                        "type": "table",
                        "text": table_text,
                    }
                )
            continue
        if not isinstance(block, Paragraph):
            continue
        paragraph = block
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = paragraph.style.name if paragraph.style else "Normal"
        if style_name == "Title":
            markdown_text = f"# {text}"
            item_type = "title"
        else:
            match = re.match(r"Heading\s+(\d+)", style_name, flags=re.IGNORECASE)
            if match:
                markdown_text = f"{'#' * min(int(match.group(1)) + 1, 6)} {text}"
                item_type = "title"
            else:
                markdown_text = text
                item_type = "text"
        markdown_blocks.append(markdown_text)
        content_list.append(
            {
                "type": item_type,
                "text": text,
            }
        )
    return ParseResult(
        "python_docx_markdown",
        "\n\n".join(markdown_blocks),
        {"paragraph_count": len(document.paragraphs)},
        {"content_list": content_list},
    )


def parse_pdf_text(path: Path) -> ParseResult:
    reader = PdfReader(str(path))
    page_blocks: list[str] = []
    content_list: list[dict[str, Any]] = []
    for index, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        page_blocks.append(f"## 第 {index + 1} 页\n\n{text}")
        content_list.append({"type": "text", "page_idx": index, "text": text})
    return ParseResult(
        "pypdf_text",
        "\n\n".join(page_blocks),
        {"page_count": len(reader.pages)},
        {"content_list": content_list},
    )


def transcript_segments_to_text(rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    blocks: list[str] = []
    for row in rows:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        blocks.append(text)
        segment = {
            "start": float(row["start"]),
            "end": float(row["end"]),
            "text": text,
        }
        speaker = str(row.get("speaker") or "").strip()
        if speaker:
            segment["speaker"] = speaker
        segments.append(segment)
    return "\n\n".join(blocks), {"segments": segments}


def parse_document(path: Path) -> ParseResult:
    global _whisper_model
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return parse_docx_markdown(path)
    if suffix == ".csv":
        return ParseResult("langchain_csv", join_documents(CSVLoader(str(path), encoding="utf-8-sig").load()))
    if suffix == ".json":
        loader = JSONLoader(str(path), jq_schema=".", text_content=False)
        loaded = loader.load()
        normalized = []
        for document in loaded:
            value = json.loads(document.page_content)
            normalized.append(json.dumps(value, ensure_ascii=False, indent=2))
        return ParseResult("langchain_json", "\n\n".join(normalized))
    if suffix in {".html", ".htm"}:
        return ParseResult("langchain_bshtml", join_documents(BSHTMLLoader(str(path), open_encoding="utf-8").load()))
    if suffix in {".md", ".markdown"}:
        markdown = path.read_text(encoding="utf-8")
        return ParseResult("markdown_text", markdown, structured={"content_list": markdown_content_list(markdown)})
    if suffix == ".pptx":
        res = parse_pptx(str(path))
        return ParseResult("pptx_ooxml", "\n\n".join(res["blocks"]), {"slide_count": res["slide_count"]}, {"content_list": res["content_list"]})
    if suffix == ".xlsx":
        structured = parse_xlsx(path.read_bytes())
        text_parts = []
        for sheet in structured["sheets"]:
            text_parts.append(f"## {sheet['name']}")
            text_parts.extend(" | ".join("" if value is None else str(value) for value in row) for row in sheet["rows"])
        return ParseResult("openpyxl", "\n".join(text_parts), {"sheet_count": len(structured["sheets"])}, structured)
    if suffix == ".pdf":
        return parse_pdf_text(path)
    if suffix in {".png", ".jpg", ".jpeg"}:
        raise ValueError("图片统一交给 MinerU 保留版面坐标与中文 OCR 结果")
    if suffix in {".wav", ".mp3", ".m4a"}:
        if _whisper_model is None:
            from faster_whisper import WhisperModel

            _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, info = _whisper_model.transcribe(str(path), language="zh", vad_filter=True)
        rows = [{"start": round(item.start, 2), "end": round(item.end, 2), "text": item.text.strip()} for item in segments]
        text, structured = transcript_segments_to_text(rows)
        return ParseResult("faster_whisper_tiny", text, {"language": info.language, "duration": info.duration}, structured)
    raise ValueError(f"尚未注册解析器: {suffix}")
