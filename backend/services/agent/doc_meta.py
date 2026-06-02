"""文档文件（PDF/DOCX/PPTX）元数据生成 + 提取质检。

从已提取的文本内容中分析质量，生成 .meta.json。
PDF/DOCX/PPTX 文本提取走 code_execute + pdfplumber/python-docx/python-pptx
（file_read 工具已废弃，本模块只负责对已提取的文本做元数据分析）。

设计文档：docs/document/TECH_文件处理系统.md §二（类型二：文档文件）
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class DocMeta:
    """文档文件的 .meta.json 结构。"""
    version: str = "1.0"
    status: str = "pass"
    source_file: str = ""
    processed_by: str = "L1"
    file_type: str = ""            # pdf | docx | pptx
    processed_at: str = ""

    summary: dict[str, Any] = field(default_factory=dict)
    structure: list[dict[str, Any]] = field(default_factory=list)
    extraction: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_extraction_quality(
    file_size: int,
    extracted_text: str,
    page_count: int,
) -> tuple[str, str | None]:
    """文档提取质量判定。

    Returns:
        (status, error_type)
        status: "pass" | "warning" | "fail"
        error_type: None 或失败原因标识
    """
    text_ratio = len(extracted_text.strip()) / max(file_size, 1)
    avg_chars = len(extracted_text.strip()) / max(page_count, 1)

    if text_ratio < 0.01 and file_size > 10000:
        return "fail", "scanned_document"
    if not extracted_text.strip():
        return "fail", "empty_extraction"
    if avg_chars < 50 and page_count > 1:
        return "warning", "low_extraction"
    return "pass", None


def generate_doc_meta(
    source_file: str,
    file_type: str,
    file_size: int,
    extracted_text: str,
    page_count: int = 0,
    para_count: int = 0,
    table_count: int = 0,
    empty_pages: list[int] | None = None,
) -> DocMeta:
    """从提取结果生成文档 meta。"""
    now = datetime.now().isoformat(timespec="seconds")
    status, error_type = assess_extraction_quality(file_size, extracted_text, max(page_count, 1))

    meta = DocMeta(
        source_file=source_file,
        file_type=file_type,
        processed_at=now,
        status=status,
    )

    meta.summary = {
        "description": f"{page_count}页 {file_type.upper()}" + (f"，{table_count}个表格" if table_count else ""),
        "page_count": page_count,
        "para_count": para_count,
        "table_count": table_count,
        "char_count": len(extracted_text),
        "is_scanned": error_type == "scanned_document",
    }

    # structure（简化版：提取标题和表格位置）
    meta.structure = _extract_structure(extracted_text, file_type)

    meta.extraction = {
        "text_ratio": round(len(extracted_text) / max(file_size, 1), 3),
        "tables_extracted": table_count,
        "pages_with_text": max(page_count - len(empty_pages or []), 0),
        "pages_empty": len(empty_pages or []),
    }

    # issues（统一 schema：type/severity/location/preserved/action/recovery_hint）
    issues: list[dict[str, Any]] = []
    for p in (empty_pages or []):
        issues.append({
            "type": "empty_page",
            "severity": "warning",
            "location": {"page": p},
            "preserved": True,
            "action": f"第{p}页无可提取文本",
            "recovery_hint": "可能是扫描件/图片页，需要 OCR 或跳过该页",
        })
    if error_type == "scanned_document":
        issues.append({
            "type": "scanned_document",
            "severity": "error",
            "location": {},
            "preserved": True,
            "action": "文件可能是扫描件，无法提取文字",
            "recovery_hint": "建议提供可编辑版本，或等待 OCR 功能上线",
        })
    elif error_type == "low_extraction":
        issues.append({
            "type": "low_extraction",
            "severity": "warning",
            "location": {},
            "preserved": True,
            "action": "提取文本量偏少，部分内容可能是图片或特殊格式",
            "recovery_hint": "在沙盒中用其他库尝试提取，或要求用户提供文字版",
        })
    meta.issues = issues
    return meta


def _extract_structure(text: str, file_type: str) -> list[dict[str, Any]]:
    """从提取文本中解析文档结构（标题、表格位置）。"""
    structure: list[dict[str, Any]] = []
    page_num = 1
    para_idx = 0

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 页码标记
        page_match = re.match(r"^── 第 (\d+) 页 ──$", line)
        if page_match:
            page_num = int(page_match.group(1))
            continue
        # Slide 标记
        slide_match = re.match(r"^=== Slide (\d+) ===$", line)
        if slide_match:
            page_num = int(slide_match.group(1))
            continue
        # 表格标记
        table_match = re.match(r"^=== 表格\s*(\d*)\s*\((\d+)行", line)
        if table_match:
            tbl_num = table_match.group(1) or str(len([s for s in structure if s["type"] == "table"]) + 1)
            rows = int(table_match.group(2))
            structure.append({"type": "table", "id": f"Table {tbl_num}", "page": page_num, "rows": rows})
            continue
        # 标题/段落
        heading_match = re.match(r"^\[(Heading \d+|Title)\]\s*(.+)$", line)
        if heading_match:
            para_idx += 1
            structure.append({
                "type": "heading", "id": f"Para {para_idx}",
                "page": page_num, "preview": heading_match.group(2)[:60],
            })
            if len(structure) >= 50:
                break
    return structure


def write_doc_meta(staging_dir: str, filename: str, meta: DocMeta) -> str:
    """写入文档 .meta.json，返回路径。"""
    safe_name = re.sub(r'[^\w\-.]', '_', filename)
    meta_path = Path(staging_dir) / f"{safe_name}.meta.json"
    try:
        Path(staging_dir).mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(meta.to_dict(), ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Failed to write doc meta: {e}")
    return str(meta_path)


def read_doc_meta(staging_dir: str, filename: str) -> DocMeta | None:
    """读取文档 .meta.json。"""
    safe_name = re.sub(r'[^\w\-.]', '_', filename)
    meta_path = Path(staging_dir) / f"{safe_name}.meta.json"
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if "version" not in data:
            return None
        return DocMeta(**data)
    except Exception:
        return None
