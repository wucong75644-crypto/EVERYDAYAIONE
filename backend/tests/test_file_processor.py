"""file_processor.py 单元测试。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from services.agent.excel_cleaner import CleaningReport
from services.agent.file_meta import FileMeta, generate_file_meta, write_file_meta
from services.agent.file_processor import (
    FileProcessResult,
    L2FixRequest,
    _build_l2_error_for_ai,
    _build_l2_request,
    _build_l3_result,
    build_l3_message,
    check_l2_result,
    process_file,
)


class TestFileProcessResult:
    def test_success(self):
        r = FileProcessResult(success=True, processed_by="L1")
        assert r.success is True
        assert r.error is None

    def test_failure(self):
        r = FileProcessResult(success=False, error={"status": "failed"})
        assert r.success is False


class TestBuildL3Result:
    def test_basic(self):
        r = _build_l3_result("test.xlsx", "parse_error", "无法解析")
        assert r.success is False
        assert r.processed_by == "L3"
        assert r.error["error_type"] == "parse_error"
        assert len(r.error["suggestions"]) > 0


class TestBuildL2Request:
    def test_from_meta(self):
        meta = FileMeta(
            status="fail",
            issues=[
                {"type": "missing_value", "severity": "warning",
                 "suggestion": "金额列缺失", "location": {"row": 3, "col": "B"}},
            ],
            sample={"head": [{"_row": 2, "a": 1}], "tail": []},
        )
        req = _build_l2_request("/path/test.xlsx", meta, "/cache/test.parquet", "/staging")
        assert req.source_file == "/path/test.xlsx"
        assert req.l1_error_type == "missing_value"
        assert "金额" in req.l1_details
        assert len(req.raw_sample) > 0


class TestBuildL2ErrorForAi:
    def test_structure(self):
        req = L2FixRequest(
            source_file="test.xlsx",
            l1_error_type="header_detection_failed",
            l1_details="无法识别表头",
            output_path="/cache/test.parquet",
        )
        meta = FileMeta(status="fail")
        err = _build_l2_error_for_ai(req, meta)
        assert err["status"] == "needs_fix"
        assert err["max_retries"] == 3
        assert "file-fix.md" in err["instructions"]


class TestBuildL3Message:
    def test_with_issues(self):
        meta = FileMeta(
            issues=[
                {"type": "missing_value", "location": {"row": 3, "col": "B"},
                 "suggestion": "金额列有12个缺失值"},
                {"type": "duplicate_row", "location": {"row": 88},
                 "suggestion": "有5条重复数据"},
            ],
        )
        msg = build_l3_message(meta, retry_count=3)
        assert "无法自动处理" in msg
        assert "Row 3" in msg
        assert "金额" in msg

    def test_empty_issues(self):
        meta = FileMeta(issues=[])
        msg = build_l3_message(meta, retry_count=3)
        assert "未知问题" in msg


class TestCheckL2Result:
    def test_pass(self, tmp_path):
        cache_path = str(tmp_path / "test.parquet")
        df = pd.DataFrame({"a": [1, 2]})
        report = CleaningReport(data_start_row=2)
        meta = generate_file_meta(df, report, "test.xlsx")
        meta.status = "pass"
        meta.processed_by = "L2"
        write_file_meta(cache_path, meta)

        result = check_l2_result(cache_path)
        assert result.success is True
        assert result.processed_by == "L2"
        assert result.file_view != ""

    def test_still_fail(self, tmp_path):
        cache_path = str(tmp_path / "test.parquet")
        meta = FileMeta(status="fail", processed_by="L2")
        write_file_meta(cache_path, meta)

        result = check_l2_result(cache_path)
        assert result.success is False

    def test_no_meta(self, tmp_path):
        result = check_l2_result(str(tmp_path / "nonexistent.parquet"))
        assert result.success is False


class TestProcessFile:
    @pytest.mark.asyncio
    async def test_l1_pass(self, tmp_path):
        """L1 通过 → 直接返回文件视图。"""
        staging = tmp_path / "staging"
        staging.mkdir()
        excel_path = str(tmp_path / "test.xlsx")

        # 创建测试 Excel
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["name", "age"])
        ws.append(["Alice", 25])
        ws.append(["Bob", 30])
        wb.save(excel_path)
        wb.close()

        result = await process_file(excel_path, str(staging))
        assert result.success is True
        assert result.processed_by == "L1"

    @pytest.mark.asyncio
    async def test_l1_exception(self, tmp_path):
        """L1 异常 → 直接跳到 L3。"""
        result = await process_file(
            str(tmp_path / "nonexistent.xlsx"),
            str(tmp_path / "staging"),
        )
        assert result.success is False
        assert result.processed_by == "L3"
        assert result.error is not None
