"""file_prescan.py 单元测试。"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from services.agent.file_prescan import (
    PrescanResult,
    _col_letter,
    _df_to_coord_lines,
    _parse_prescan_response,
    build_prescan_prompt,
)


class TestColLetter:
    def test_basic(self):
        assert _col_letter(0) == "A"
        assert _col_letter(1) == "B"
        assert _col_letter(25) == "Z"
        assert _col_letter(26) == "AA"


class TestDfToCoordLines:
    def test_basic(self):
        df = pd.DataFrame({"a": ["hello", None], "b": [123, 456]})
        lines = _df_to_coord_lines(df, row_offset=1)
        assert len(lines) == 2
        assert "A1:hello" in lines[0]
        assert "B1:123" in lines[0]
        # None → 空字符串（冒号后无值）
        assert "A2:" in lines[1]
        assert "B2:456" in lines[1]

    def test_row_offset(self):
        df = pd.DataFrame({"x": [1]})
        lines = _df_to_coord_lines(df, row_offset=28)
        assert "Row 28:" in lines[0]
        assert "A28:1" in lines[0]

    def test_empty_df(self):
        df = pd.DataFrame()
        lines = _df_to_coord_lines(df, row_offset=1)
        assert lines == []


class TestBuildPrescanPrompt:
    def test_contains_key_info(self):
        prompt = build_prescan_prompt(
            filename="test.xlsx",
            sheet_name="Sheet1",
            total_rows=100,
            total_cols=5,
            sampled_text="Row 1: A1:test",
        )
        assert "test.xlsx" in prompt
        assert "Sheet1" in prompt
        assert "总行数: 100" in prompt
        assert "总列数: 5" in prompt
        assert "Row 1: A1:test" in prompt
        assert "header_type" in prompt
        assert "data_start_row" in prompt
        assert "column_mapping" in prompt


class TestParsePrescanResponse:
    def test_basic_json(self):
        response = json.dumps({
            "header_type": "multi_level",
            "header_rows": [1, 2],
            "data_start_row": 3,
            "column_mapping": {"B": "3月_金额"},
            "special_rows": {"summary": [25]},
            "regions": [{"start_row": 3, "end_row": 25, "start_col": "A", "end_col": "E", "description": "主数据"}],
            "confidence": "high",
            "reasoning": "Row 1 是分组标题",
        })
        result = _parse_prescan_response(response)
        assert result.header_type == "multi_level"
        assert result.header_rows == [1, 2]
        assert result.data_start_row == 3
        assert result.column_mapping["B"] == "3月_金额"
        assert result.special_rows["summary"] == [25]
        assert result.confidence == "high"

    def test_markdown_code_block(self):
        response = "```json\n" + json.dumps({
            "header_type": "single",
            "header_rows": [1],
            "data_start_row": 2,
            "confidence": "high",
            "reasoning": "标准表格",
        }) + "\n```"
        result = _parse_prescan_response(response)
        assert result.header_type == "single"
        assert result.data_start_row == 2

    def test_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_prescan_response("not json")

    def test_missing_fields_use_defaults(self):
        response = json.dumps({"header_type": "none"})
        result = _parse_prescan_response(response)
        assert result.header_type == "none"
        assert result.data_start_row == 1
        assert result.column_mapping == {}
        assert result.confidence == "low"


class TestPrescanResult:
    def test_defaults(self):
        r = PrescanResult()
        assert r.header_type == "single"
        assert r.confidence == "low"
        assert r.header_rows == []
        assert r.column_mapping == {}
