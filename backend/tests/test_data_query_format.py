"""测试 data_query_format — 纯函数格式化"""
from __future__ import annotations

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pandas as pd
import pytest

from services.agent.data_query_format import (
    format_full_result,
    format_markdown_table,
    format_numeric_summary,
    format_sql_error,
)


# ── format_markdown_table ──


class TestFormatMarkdownTable:
    def test_basic_table(self):
        df = pd.DataFrame({"name": ["Alice", "Bob"], "age": [30, 25]})
        result = format_markdown_table(df)

        assert "| name | age |" in result
        assert "| --- | --- |" in result
        assert "| Alice | 30 |" in result
        assert "| Bob | 25 |" in result

    def test_truncate_long_cell(self):
        df = pd.DataFrame({"text": ["a" * 100]})
        result = format_markdown_table(df, max_cell_len=20)

        assert "..." in result
        # 截断后长度不超过 max_cell_len
        lines = result.strip().split("\n")
        data_line = lines[2]  # 第三行是数据
        cell = data_line.split("|")[1].strip()
        assert len(cell) <= 20

    def test_nan_and_none_rendered_empty(self):
        df = pd.DataFrame({"val": [None, float("nan")]})
        result = format_markdown_table(df)

        lines = result.strip().split("\n")
        # 数据行的 cell 应该是空字符串
        for line in lines[2:]:
            cell = line.split("|")[1].strip()
            assert cell == ""

    def test_empty_dataframe(self):
        df = pd.DataFrame({"a": [], "b": []})
        result = format_markdown_table(df)

        assert "| a | b |" in result
        # 只有 header + separator，没有数据行
        lines = [l for l in result.strip().split("\n") if l.strip()]
        assert len(lines) == 2

    def test_chinese_columns(self):
        df = pd.DataFrame({"店铺名称": ["白桃杂货铺"], "金额": [999.5]})
        result = format_markdown_table(df)

        assert "店铺名称" in result
        assert "白桃杂货铺" in result


# ── format_numeric_summary ──


class TestFormatNumericSummary:
    def test_basic_summary(self):
        df = pd.DataFrame({"amount": [100, 200, 300], "qty": [1, 2, 3]})
        result = format_numeric_summary(df)

        assert "**统计摘要**" in result
        assert "amount" in result
        assert "合计" in result
        assert "均值" in result

    def test_no_numeric_columns(self):
        df = pd.DataFrame({"name": ["a", "b", "c"]})
        result = format_numeric_summary(df)

        assert result == ""

    def test_max_cols_limit(self):
        df = pd.DataFrame({f"col{i}": [i * 10] for i in range(10)})
        result = format_numeric_summary(df, max_cols=3)

        # 最多 3 列 + 标题行
        lines = [l for l in result.strip().split("\n") if l.startswith("- ")]
        assert len(lines) <= 3

    def test_all_nan_column_skipped(self):
        df = pd.DataFrame({"val": [float("nan"), float("nan")]})
        result = format_numeric_summary(df)

        # dropna 后为空，不应输出
        assert result == ""


# ── format_full_result ──


class TestFormatFullResult:
    def test_includes_table_and_meta(self):
        df = pd.DataFrame({"x": [1, 2, 3]})
        result = format_full_result(df, rows=3, elapsed=0.12)

        assert "| x |" in result
        assert "共 3 行" in result
        assert "0.12s" in result

    def test_large_row_count_formatted(self):
        df = pd.DataFrame({"x": [1]})
        result = format_full_result(df, rows=12345, elapsed=1.0)

        assert "12,345" in result


# ── format_sql_error ──


class TestFormatSqlError:
    def test_basic_error_with_columns(self):
        result = format_sql_error(
            "列 '店铺名' 不存在",
            ["店铺名称", "金额", "日期"],
        )

        # 返回 AgentResult
        assert result.status == "error"
        assert result.is_failure
        assert "店铺名' 不存在" in result.error_message
        assert "店铺名称" in result.summary or "店铺名称" in result.error_message
        assert result.metadata.get("retryable") is True

    def test_empty_columns(self):
        result = format_sql_error("syntax error", [])

        assert result.status == "error"
        assert "无法获取列名" in result.summary

    def test_many_columns_truncated(self):
        cols = [f"col_{i}" for i in range(50)]
        result = format_sql_error("error", cols)

        assert result.status == "error"
        assert "共 50 列" in result.summary
        # 前 30 列应该展示
        assert "col_0" in result.summary
        assert "col_29" in result.summary

    def test_chinese_hint(self):
        result = format_sql_error("error", ["店铺名称"])

        assert result.status == "error"
        assert '双引号' in result.summary
        assert '"店铺名称"' in result.summary

    def test_duckdb_did_you_mean_suggestion(self):
        """DuckDB 'Did you mean' 建议应被提取并高亮"""
        error_msg = 'Binder Error: Referenced column "店铺名" not found. Did you mean "店铺名称"?'
        result = format_sql_error(error_msg, ["店铺名称", "金额"])

        assert result.status == "error"
        assert result.metadata.get("suggestion") == "店铺名称"
        assert "店铺名称" in result.summary
        assert "修正" in result.summary

    def test_no_suggestion_shows_columns(self):
        """无 Did you mean 时展示可用列名"""
        result = format_sql_error("Parser Error: syntax error", ["col_a", "col_b"])

        assert result.metadata.get("suggestion") is None
        assert '"col_a"' in result.summary
        assert '"col_b"' in result.summary
