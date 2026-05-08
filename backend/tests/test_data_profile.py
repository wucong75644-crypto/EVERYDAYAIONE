"""测试 data_profile.build_data_profile — v6 全列类型数据摘要"""
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

    def test_returns_tuple(self):
        """v6: 返回 (text, stats_dict) 元组"""
        result = build_data_profile(self._basic_df(), "t.parquet", 1.0)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], dict)

    def test_contains_all_sections(self):
        """输出包含全部板块标记"""
        text, _ = build_data_profile(self._basic_df(), "trade_123.parquet", 2.5)
        assert "[数据已暂存]" in text
        assert "[字段]" in text
        assert "[质量]" in text
        assert "[统计-数值]" in text
        assert "[预览]" in text
        assert "[查询]" in text

    def test_meta_section(self):
        """元信息板块包含行数、列数、文件大小"""
        text, _ = build_data_profile(self._basic_df(), "test.parquet", 10.5, elapsed=1.2)
        assert "3 条" in text or "3条" in text
        assert "5 列" in text or "5列" in text
        assert "10KB" in text or "11KB" in text
        assert "1.2s" in text

    def test_schema_section(self):
        """字段板块列出列名和类型和 distinct_count"""
        text, _ = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert "order_no" in text
        assert "amount" in text
        assert "3种" in text  # distinct_count

    def test_quality_section_detects_nulls(self):
        """质量板块检测空值"""
        text, stats = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert "shop_name" in text
        assert "1条" in text or "1 条" in text
        assert "33.3%" in text
        assert stats["shop_name"]["null_count"] == 1

    def test_quality_section_no_nulls(self):
        """无空值时显示'无'"""
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        text, _ = build_data_profile(df, "test.parquet", 0.5)
        assert "空值: 无" in text

    def test_stats_section_numeric(self):
        """统计板块包含数值列的 sum/min/max/avg + v6 新增 median"""
        text, stats = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert "合计" in text
        assert "最小" in text
        assert "最大" in text
        assert "均值" in text
        assert "中位数" in text
        # stats_dict 结构验证
        assert "amount" in stats
        assert "median" in stats["amount"]
        assert "p25" in stats["amount"]
        assert "p75" in stats["amount"]

    def test_stats_dict_distinct_count(self):
        """stats_dict 包含每列的 distinct_count"""
        _, stats = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert stats["order_no"]["distinct_count"] == 3
        assert stats["shop_name"]["distinct_count"] == 2

    def test_preview_section(self):
        """预览板块显示 head(2) + sample(1)"""
        text, _ = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert "1." in text
        assert "2." in text
        assert "3." in text
        assert "A001" in text

    def test_read_instruction(self):
        """查询指引包含 data_query + 文件名"""
        text, _ = build_data_profile(self._basic_df(), "trade_999.parquet", 1.0)
        assert 'data_query(file="trade_999.parquet"' in text

    def test_no_absolute_path_leaked(self):
        """输出不包含服务器绝对路径"""
        text, _ = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert "/mnt/" not in text
        assert "/tmp/" not in text

    def test_text_column_stats(self):
        """v6: 文本列有 top-5 高频值"""
        text, stats = build_data_profile(self._basic_df(), "test.parquet", 1.0)
        assert "[统计-文本]" in text
        assert "shop_name" in stats
        assert "avg_length" in stats["shop_name"]

    def test_datetime_column_stats(self):
        """v6: 时间列有 min/max + 跨度"""
        df = pd.DataFrame({
            "dt": pd.to_datetime(["2026-04-01", "2026-04-15", "2026-04-30"]),
            "val": [1, 2, 3],
        })
        text, stats = build_data_profile(df, "t.parquet", 1.0)
        assert "[统计-时间]" in text
        assert "跨29天" in text
        assert stats["dt"]["span_days"] == 29


# ============================================================
# 边界情况
# ============================================================


class TestBuildDataProfileEdgeCases:
    """边界情况"""

    def test_empty_dataframe(self):
        """空 DataFrame 不报错"""
        df = pd.DataFrame({"a": pd.Series(dtype="int64")})
        text, stats = build_data_profile(df, "empty.parquet", 0.0)
        assert "0 条" in text or "0条" in text
        assert stats == {}

    def test_no_numeric_columns(self):
        """无数值列时不输出数值统计板块"""
        df = pd.DataFrame({"name": ["a", "b"], "code": ["x", "y"]})
        text, _ = build_data_profile(df, "test.parquet", 0.5)
        assert "[统计-数值]" not in text

    def test_all_null_column(self):
        """全空值列标记高空值率"""
        df = pd.DataFrame({"a": [1, 2, 3], "b": [None, None, None]})
        text, _ = build_data_profile(df, "test.parquet", 0.5)
        assert "100.0%" in text
        assert "高空值率" in text

    def test_sync_info_in_warnings(self):
        """sync_info 出现在警告板块"""
        df = pd.DataFrame({"a": [1]})
        text, _ = build_data_profile(df, "t.parquet", 0.1, sync_info="最后同步于 5 分钟前")
        assert "最后同步于 5 分钟前" in text

    def test_long_value_truncated_in_preview(self):
        """预览中超长值被截断"""
        df = pd.DataFrame({"text": ["a" * 100]})
        text, _ = build_data_profile(df, "t.parquet", 0.1)
        assert "..." in text

    def test_duplicate_detection(self):
        """重复行检测"""
        df = pd.DataFrame({"a": [1, 1, 2], "b": [10, 10, 20]})
        text, _ = build_data_profile(df, "t.parquet", 0.5)
        assert "重复: 1条" in text

    def test_many_numeric_columns_capped_at_5(self):
        """数值列超过 5 列时只展示前 5 列"""
        cols = {f"num_{i}": [float(i)] for i in range(8)}
        df = pd.DataFrame(cols)
        text, _ = build_data_profile(df, "t.parquet", 0.1)
        stat_lines = [line for line in text.split("\n") if "合计" in line]
        assert len(stat_lines) <= 5

    def test_max_profile_rows_sampling(self):
        """v6: 超过 max_profile_rows 时采样"""
        df = pd.DataFrame({"v": range(200)})
        text, _ = build_data_profile(df, "t.parquet", 0.1, max_profile_rows=50)
        assert "统计基于 50 条采样" in text

    def test_high_cardinality_text_no_top5(self):
        """v6: 高基数文本列不做 top-5"""
        df = pd.DataFrame({"order_no": [f"ORD{i}" for i in range(200)]})
        text, stats = build_data_profile(df, "t.parquet", 0.1)
        assert "top5" not in stats.get("order_no", {})
        assert "平均长度" in text

    def test_iqr_outlier_detection(self):
        """v6: IQR 异常值检测"""
        # 1,2,3,4,5 + 100 (明显异常)
        df = pd.DataFrame({"val": [1, 2, 3, 4, 5, 100]})
        text, stats = build_data_profile(df, "t.parquet", 0.1)
        assert "异常值" in text
        assert stats["val"].get("outlier_count", 0) > 0


# ============================================================
# 数据结构画像（_infer_structure）
# ============================================================

from services.agent.data_profile import _infer_structure


class TestInferStructure:
    """列角色分类 + 主从模式检测"""

    def test_master_detail_detected(self):
        """主从模式：主字段相邻重复率高，明细字段低"""
        columns = [
            {"name": "order_id", "type": "BIGINT", "distinct_count": 50, "null_count": 0},
            {"name": "amount", "type": "DOUBLE", "distinct_count": 50, "null_count": 0},
            {"name": "item_price", "type": "DOUBLE", "distinct_count": 80, "null_count": 0},
            {"name": "qty", "type": "INTEGER", "distinct_count": 5, "null_count": 0},
        ]
        profile = {
            "adjacent_dup_ratios": {
                "order_id": 0.85,   # 主字段
                "amount": 0.85,     # 主字段
                "item_price": 0.05, # 明细字段
                "qty": 0.03,        # 明细字段
            },
        }
        lines = _infer_structure(columns, 100, profile)
        text = "\n".join(lines)
        assert "主从模式" in text
        assert "order_id" in text
        assert "明细字段" in text
        assert "去重" in text

    def test_flat_table_no_master_detail(self):
        """扁平表：所有列相邻重复率都低 → 不输出主从段"""
        columns = [
            {"name": "id", "type": "BIGINT", "distinct_count": 95, "null_count": 0},
            {"name": "name", "type": "VARCHAR", "distinct_count": 90, "null_count": 0},
            {"name": "amount", "type": "DOUBLE", "distinct_count": 80, "null_count": 0},
        ]
        profile = {
            "adjacent_dup_ratios": {"id": 0.01, "name": 0.03, "amount": 0.02},
        }
        lines = _infer_structure(columns, 100, profile)
        text = "\n".join(lines)
        assert "主从模式" not in text

    def test_column_roles_id(self):
        """唯一率 >0.9 且完整率 >0.95 → ID"""
        columns = [
            {"name": "order_no", "type": "VARCHAR", "distinct_count": 95, "null_count": 0},
            {"name": "status", "type": "VARCHAR", "distinct_count": 3, "null_count": 0},
        ]
        lines = _infer_structure(columns, 100, {})
        text = "\n".join(lines)
        assert "ID: order_no" in text
        assert "分类: status" in text

    def test_column_roles_timestamp(self):
        """时间类型 → 时间角色"""
        columns = [
            {"name": "created_at", "type": "TIMESTAMP", "distinct_count": 90, "null_count": 0},
            {"name": "amount", "type": "DOUBLE", "distinct_count": 80, "null_count": 0},
        ]
        lines = _infer_structure(columns, 100, {})
        text = "\n".join(lines)
        assert "时间: created_at" in text
        assert "度量: amount" in text

    def test_small_table_skipped(self):
        """小表（<10行）→ 跳过推断"""
        columns = [{"name": "a", "type": "INTEGER", "distinct_count": 5, "null_count": 0}]
        lines = _infer_structure(columns, 5, {})
        assert lines == []

    def test_empty_columns_skipped(self):
        """空列列表 → 跳过推断"""
        lines = _infer_structure([], 100, {})
        assert lines == []

    def test_no_adjacent_ratios_still_outputs_roles(self):
        """无相邻重复率数据 → 不输出主从，但仍输出列角色"""
        columns = [
            {"name": "id", "type": "BIGINT", "distinct_count": 95, "null_count": 0},
            {"name": "status", "type": "VARCHAR", "distinct_count": 2, "null_count": 0},
            {"name": "pay_time", "type": "TIMESTAMP", "distinct_count": 90, "null_count": 0},
        ]
        lines = _infer_structure(columns, 100, {})
        text = "\n".join(lines)
        assert "主从模式" not in text
        assert "[列角色]" in text
        assert "ID: id" in text
