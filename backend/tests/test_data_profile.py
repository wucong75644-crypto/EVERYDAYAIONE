"""测试 data_profile.build_data_profile — 7 板块数据摘要"""
from __future__ import annotations

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pandas as pd
import pytest
from services.agent.data_profile import build_data_profile


# ============================================================
# 基础功能
# ============================================================


class TestBuildDataProfile:
    """build_data_profile 基本输出"""

    def _basic_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "order_no": ["A001", "A002", "A003"],
            "shop_name": ["旗舰店", "专卖店", None],
            "amount": [99.9, 149.9, 199.9],
            "qty": [1, 2, 3],
            "pay_time": ["2026-04-18", "2026-04-18", "2026-04-18"],
        })

    def test_contains_all_7_sections(self):
        """输出包含全部 7 个板块标记"""
        result = build_data_profile(self._basic_df(), "trade_123.parquet", 2.5)
        assert "[数据已暂存]" in result
        assert "[字段]" in result
        assert "[质量]" in result
        assert "[统计]" in result
        assert "[预览]" in result
        assert "[读取]" in result

    def test_meta_section(self):
        """元信息板块包含行数、列数、文件大小"""
        result = build_data_profile(self._basic_df(), "test.parquet", 10.5, elapsed=1.2)
        assert "3 条" in result or "3条" in result
        assert "5 列" in result or "5列" in result
        assert "10KB" in result or "11KB" in result
        assert "1.2s" in result

    def test_schema_section(self):
        """字段板块列出列名和类型"""
        result = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert "order_no" in result
        assert "amount" in result
        assert "float" in result or "numeric" in result

    def test_quality_section_detects_nulls(self):
        """质量板块检测空值"""
        result = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert "shop_name" in result
        assert "1条" in result or "1 条" in result
        assert "33.3%" in result

    def test_quality_section_no_nulls(self):
        """无空值时显示'无'"""
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        result = build_data_profile(df, "test.parquet", 0.5)
        assert "空值: 无" in result

    def test_stats_section(self):
        """统计板块包含数值列的 sum/min/max/avg"""
        result = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert "合计" in result
        assert "最小" in result
        assert "最大" in result
        assert "均值" in result

    def test_preview_section(self):
        """预览板块显示前 3 行"""
        result = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert "1." in result
        assert "2." in result
        assert "3." in result
        assert "A001" in result

    def test_read_instruction(self):
        """读取指引包含 STAGING_DIR + 文件名"""
        result = build_data_profile(self._basic_df(), "trade_999.parquet", 1.0)
        assert "STAGING_DIR + '/trade_999.parquet'" in result

    def test_no_absolute_path_leaked(self):
        """输出不包含服务器绝对路径"""
        result = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert "/mnt/" not in result
        assert "/tmp/" not in result


# ============================================================
# 边界情况
# ============================================================


class TestBuildDataProfileEdgeCases:
    """边界情况"""

    def test_empty_dataframe(self):
        """空 DataFrame 不报错"""
        df = pd.DataFrame({"a": pd.Series(dtype="int64")})
        result = build_data_profile(df, "empty.parquet", 0.0)
        assert "0 条" in result or "0条" in result
        assert "[字段]" in result

    def test_no_numeric_columns(self):
        """无数值列时不输出统计板块"""
        df = pd.DataFrame({"name": ["a", "b"], "code": ["x", "y"]})
        result = build_data_profile(df, "test.parquet", 0.5)
        assert "[统计]" not in result

    def test_all_null_column(self):
        """全空值列标记高空值率"""
        df = pd.DataFrame({"a": [1, 2, 3], "b": [None, None, None]})
        result = build_data_profile(df, "test.parquet", 0.5)
        assert "100.0%" in result
        assert "高空值率" in result

    def test_sync_info_in_warnings(self):
        """sync_info 出现在警告板块"""
        df = pd.DataFrame({"a": [1]})
        result = build_data_profile(df, "t.parquet", 0.1, sync_info="最后同步于 5 分钟前")
        assert "最后同步于 5 分钟前" in result

    def test_long_value_truncated_in_preview(self):
        """预览中超长值被截断"""
        df = pd.DataFrame({"text": ["a" * 100]})
        result = build_data_profile(df, "t.parquet", 0.1)
        assert "..." in result

    def test_duplicate_detection(self):
        """重复行检测"""
        df = pd.DataFrame({"a": [1, 1, 2], "b": [10, 10, 20]})
        result = build_data_profile(df, "t.parquet", 0.5)
        assert "重复: 1条" in result

    def test_many_numeric_columns_capped_at_5(self):
        """数值列超过 5 列时只展示前 5 列"""
        cols = {f"num_{i}": [float(i)] for i in range(8)}
        df = pd.DataFrame(cols)
        result = build_data_profile(df, "t.parquet", 0.1)
        # 统计板块最多 5 行
        stat_lines = [l for l in result.split("\n") if "合计" in l]
        assert len(stat_lines) <= 5
