"""doc_meta.py 单元测试。"""
from __future__ import annotations

import json
from pathlib import Path

from services.agent.doc_meta import (
    DocMeta,
    _extract_structure,
    assess_extraction_quality,
    generate_doc_meta,
    read_doc_meta,
    write_doc_meta,
)


class TestAssessExtractionQuality:
    def test_pass(self):
        status, err = assess_extraction_quality(10000, "A" * 5000, 10)
        assert status == "pass"
        assert err is None

    def test_scanned(self):
        status, err = assess_extraction_quality(500000, "短", 50)
        assert status == "fail"
        assert err == "scanned_document"

    def test_low_extraction(self):
        status, err = assess_extraction_quality(10000, "短文本", 10)
        assert status == "warning"
        assert err == "low_extraction"

    def test_empty(self):
        status, err = assess_extraction_quality(10000, "", 5)
        assert status == "fail"
        assert err == "empty_extraction"

    def test_small_file(self):
        """小文件即使 text_ratio 低也不判定为扫描件。"""
        status, _ = assess_extraction_quality(500, "短", 1)
        assert status != "fail"


class TestExtractStructure:
    def test_pdf_pages(self):
        text = """── 第 1 页 ──
[Heading 1] 第一章
正文内容

── 第 2 页 ──
=== 表格 1 (5行 x 3列) ===
  Row1: ['A', 'B', 'C']"""
        structure = _extract_structure(text, "pdf")
        assert len(structure) == 2
        assert structure[0]["type"] == "heading"
        assert structure[0]["page"] == 1
        assert structure[1]["type"] == "table"
        assert structure[1]["page"] == 2

    def test_slide(self):
        text = """=== Slide 1 ===
[Title] 演讲题目
=== Slide 3 ===
=== 表格 (3行 x 2列) ==="""
        structure = _extract_structure(text, "pptx")
        headings = [s for s in structure if s["type"] == "heading"]
        tables = [s for s in structure if s["type"] == "table"]
        assert len(headings) == 1
        assert headings[0]["page"] == 1
        assert len(tables) == 1
        assert tables[0]["page"] == 3

    def test_empty(self):
        assert _extract_structure("", "pdf") == []


class TestGenerateDocMeta:
    def test_basic(self):
        meta = generate_doc_meta(
            source_file="report.pdf",
            file_type="pdf",
            file_size=50000,
            extracted_text="A" * 5000,
            page_count=10,
            para_count=30,
            table_count=2,
        )
        assert meta.status == "pass"
        assert meta.summary["page_count"] == 10
        assert meta.summary["table_count"] == 2
        assert meta.extraction["text_ratio"] > 0

    def test_scanned_pdf(self):
        meta = generate_doc_meta(
            source_file="scan.pdf",
            file_type="pdf",
            file_size=5000000,
            extracted_text="",
            page_count=20,
        )
        assert meta.status == "fail"
        assert meta.summary["is_scanned"] is True
        error_issues = [i for i in meta.issues if i["type"] == "scanned_document"]
        assert len(error_issues) == 1

    def test_empty_pages(self):
        meta = generate_doc_meta(
            source_file="mixed.pdf",
            file_type="pdf",
            file_size=10000,
            extracted_text="A" * 3000,
            page_count=5,
            empty_pages=[3, 4],
        )
        assert meta.extraction["pages_empty"] == 2
        empty_issues = [i for i in meta.issues if i["type"] == "empty_page"]
        assert len(empty_issues) == 2


class TestDocMetaToDict:
    def test_returns_dict(self):
        meta = DocMeta(version="1.0", status="pass", file_type="pdf")
        d = meta.to_dict()
        assert isinstance(d, dict)
        assert d["version"] == "1.0"
        assert d["file_type"] == "pdf"

    def test_serializable(self):
        meta = generate_doc_meta(
            source_file="test.pdf", file_type="pdf",
            file_size=10000, extracted_text="content", page_count=3,
        )
        import json
        serialized = json.dumps(meta.to_dict(), default=str)
        assert len(serialized) > 0


class TestWriteReadDocMeta:
    def test_round_trip(self, tmp_path):
        meta = generate_doc_meta(
            source_file="doc.pdf", file_type="pdf",
            file_size=10000, extracted_text="test content",
            page_count=3,
        )
        write_doc_meta(str(tmp_path), "doc.pdf", meta)
        loaded = read_doc_meta(str(tmp_path), "doc.pdf")
        assert loaded is not None
        assert loaded.version == "1.0"
        assert loaded.summary["page_count"] == 3

    def test_read_missing(self, tmp_path):
        assert read_doc_meta(str(tmp_path), "missing.pdf") is None
