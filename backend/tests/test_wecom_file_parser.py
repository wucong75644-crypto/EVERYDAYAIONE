"""企微文件解析器单元测试"""

import json
import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.wecom.file_parser import (
    MAX_TEXT_LENGTH,
    is_supported,
    parse_file,
    _decode_bytes,
    _get_ext,
    _parse_csv,
    _parse_json,
    _parse_text,
)


# ── is_supported ──────────────────────────────────────


class TestIsSupported:
    def test_supported_text_files(self):
        assert is_supported("readme.txt") is True
        assert is_supported("data.csv") is True
        assert is_supported("config.json") is True
        assert is_supported("notes.md") is True

    def test_supported_documents(self):
        assert is_supported("report.pdf") is True
        assert is_supported("合同.docx") is True
        assert is_supported("销售数据.xlsx") is True

    def test_supported_code_files(self):
        assert is_supported("app.py") is True
        assert is_supported("index.js") is True
        assert is_supported("style.css") is True

    def test_unsupported_files(self):
        assert is_supported("video.mp4") is False
        assert is_supported("image.png") is False
        assert is_supported("archive.zip") is False
        assert is_supported("app.exe") is False

    def test_no_extension(self):
        assert is_supported("noext") is False
        assert is_supported("") is False

    def test_case_insensitive(self):
        assert is_supported("FILE.PDF") is True
        assert is_supported("data.CSV") is True
        assert is_supported("doc.DOCX") is True


# ── _get_ext ──────────────────────────────────────────


class TestGetExt:
    def test_normal(self):
        assert _get_ext("file.txt") == "txt"
        assert _get_ext("report.pdf") == "pdf"

    def test_uppercase(self):
        assert _get_ext("FILE.PDF") == "pdf"

    def test_multiple_dots(self):
        assert _get_ext("my.file.name.xlsx") == "xlsx"

    def test_no_extension(self):
        assert _get_ext("noext") == ""
        assert _get_ext("") == ""
        assert _get_ext(None) == ""


# ── parse_file ────────────────────────────────────────


class TestParseFile:
    def test_plain_text(self):
        data = "Hello, World!".encode("utf-8")
        text, truncated = parse_file(data, "hello.txt")
        assert text == "Hello, World!"
        assert truncated is False

    def test_csv_file(self):
        csv_data = "name,age\nAlice,30\nBob,25".encode("utf-8")
        text, truncated = parse_file(csv_data, "data.csv")
        assert "Alice" in text
        assert "Bob" in text
        assert truncated is False

    def test_json_file(self):
        obj = {"name": "测试", "value": 42}
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        text, truncated = parse_file(data, "config.json")
        assert "测试" in text
        assert "42" in text
        assert truncated is False

    def test_truncation(self):
        long_text = "A" * (MAX_TEXT_LENGTH + 1000)
        data = long_text.encode("utf-8")
        text, truncated = parse_file(data, "long.txt")
        assert len(text) == MAX_TEXT_LENGTH
        assert truncated is True

    def test_empty_file(self):
        text, truncated = parse_file(b"", "empty.txt")
        assert "内容为空" in text
        assert truncated is False

    def test_unsupported_format_still_parses(self):
        """parse_file 本身不校验格式，is_supported 在调用方校验"""
        data = b"binary garbage"
        text, truncated = parse_file(data, "file.bin")
        assert isinstance(text, str)

    def test_gbk_encoded_text(self):
        data = "中文内容".encode("gbk")
        text, truncated = parse_file(data, "chinese.txt")
        assert "中文内容" in text

    def test_parse_failure_returns_error_message(self):
        """PDF 解析传入非法数据应返回错误提示而非抛异常"""
        text, truncated = parse_file(b"not a real pdf", "bad.pdf")
        assert "解析失败" in text
        assert truncated is False


# ── _decode_bytes ─────────────────────────────────────


class TestDecodeBytes:
    def test_utf8(self):
        assert _decode_bytes("你好".encode("utf-8")) == "你好"

    def test_gbk_fallback(self):
        assert _decode_bytes("你好".encode("gbk")) == "你好"

    def test_latin1_fallback(self):
        data = bytes(range(128, 256))
        result = _decode_bytes(data)
        assert isinstance(result, str)


# ── _parse_csv ────────────────────────────────────────


class TestParseCsv:
    def test_basic(self):
        data = "a,b,c\n1,2,3".encode("utf-8")
        result = _parse_csv(data)
        assert "a | b | c" in result
        assert "1 | 2 | 3" in result


# ── _parse_json ───────────────────────────────────────


class TestParseJson:
    def test_valid_json(self):
        data = json.dumps({"key": "value"}).encode("utf-8")
        result = _parse_json(data)
        assert '"key"' in result
        assert '"value"' in result

    def test_invalid_json_returns_raw(self):
        data = b"not json {{"
        result = _parse_json(data)
        assert result == "not json {{"


# ── PDF/DOCX/XLSX 集成测试（需要真实文件，用简单 mock）──


class TestPdfParse:
    def test_pdf_parse_with_real_lib(self):
        """验证 PyPDF2 导入可用"""
        from PyPDF2 import PdfReader
        assert PdfReader is not None


class TestDocxParse:
    def test_docx_import(self):
        """验证 python-docx 导入可用"""
        from docx import Document
        assert Document is not None


class TestXlsxParse:
    def test_openpyxl_import(self):
        """验证 openpyxl 导入可用"""
        from openpyxl import load_workbook
        assert load_workbook is not None
