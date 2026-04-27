"""
分布分析测试（RPC 调用 + 格式化 + 边界场景）。

覆盖: erp_analytics_distribution.py
设计文档: docs/document/TECH_ERP查询架构重构.md §5.9
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.kuaimai.erp_analytics_distribution import (
    BUCKET_RULES,
    DEFAULT_BUCKETS,
    format_distribution_summary,
    query_distribution,
)
from services.kuaimai.erp_unified_schema import TimeRange


# ── mock 工厂 ──


def _mock_tr() -> TimeRange:
    from datetime import timedelta
    from utils.time_context import DateRange, now_cn
    now = now_cn()
    end = now + timedelta(days=1)
    return TimeRange(
        start_iso=now.strftime("%Y-%m-%d %H:%M:%S%z"),
        end_iso=end.strftime("%Y-%m-%d %H:%M:%S%z"),
        time_col="doc_created_at",
        date_range=DateRange.custom(now, end, reference=now),
        label="test",
    )


def _mock_db_rpc(rpc_data):
    """构造 mock db，db.rpc(name, params).execute() → rpc_data。"""
    resp = MagicMock()
    resp.data = rpc_data

    rpc_result = MagicMock()
    rpc_result.execute = MagicMock(return_value=resp)

    db = MagicMock()
    db.rpc = MagicMock(return_value=rpc_result)
    return db


# ============================================================
# query_distribution 测试
# ============================================================


class TestQueryDistribution:
    @pytest.mark.asyncio
    async def test_basic(self):
        rpc_data = [
            {"bucket": "0~50", "count": 120, "bucket_total": 3800.00, "sort_key": 0},
            {"bucket": "50~100", "count": 85, "bucket_total": 6200.00, "sort_key": 50},
            {"bucket": "100~200", "count": 40, "bucket_total": 5600.00, "sort_key": 100},
        ]
        db = _mock_db_rpc(rpc_data)
        result = await query_distribution(
            db, "org-1", "order", tr=_mock_tr(), metrics=["amount"],
        )
        assert result.status == "success"
        assert result.metadata["query_type"] == "distribution"
        assert result.metadata["field"] == "amount"
        assert len(result.data) == 3
        # sort_key should be removed
        assert "sort_key" not in result.data[0]

    @pytest.mark.asyncio
    async def test_default_field(self):
        db = _mock_db_rpc([{"bucket": "0~100", "count": 10, "bucket_total": 500}])
        result = await query_distribution(db, "org-1", "order", tr=_mock_tr())
        # 默认 field=amount
        assert result.metadata["field"] == "amount"
        db.rpc.assert_called_once()
        call_args = db.rpc.call_args
        assert call_args[0][1]["p_field"] == "amount"

    @pytest.mark.asyncio
    async def test_bucket_rules(self):
        db = _mock_db_rpc([])
        await query_distribution(
            db, "org-1", "order", tr=_mock_tr(), metrics=["quantity"],
        )
        call_args = db.rpc.call_args
        assert call_args[0][1]["p_buckets"] == BUCKET_RULES["quantity"]

    @pytest.mark.asyncio
    async def test_unknown_field_uses_default_buckets(self):
        db = _mock_db_rpc([])
        await query_distribution(
            db, "org-1", "order", tr=_mock_tr(), metrics=["gross_profit"],
        )
        call_args = db.rpc.call_args
        assert call_args[0][1]["p_buckets"] == DEFAULT_BUCKETS

    @pytest.mark.asyncio
    async def test_daily_stats_table(self):
        db = _mock_db_rpc([])
        await query_distribution(
            db, "org-1", "daily_stats", tr=_mock_tr(), metrics=["order_amount"],
        )
        call_args = db.rpc.call_args
        assert call_args[0][1]["p_table"] == "erp_product_daily_stats"
        assert call_args[0][1]["p_time_col"] == "stat_date"
        # daily_stats 不应传 p_doc_type
        assert "p_doc_type" not in call_args[0][1]

    @pytest.mark.asyncio
    async def test_stock_table(self):
        db = _mock_db_rpc([])
        await query_distribution(
            db, "org-1", "stock", metrics=["available_stock"],
        )
        call_args = db.rpc.call_args
        assert call_args[0][1]["p_table"] == "erp_stock_status"
        assert call_args[0][1]["p_time_col"] == "stock_modified_time"

    @pytest.mark.asyncio
    async def test_empty_result(self):
        db = _mock_db_rpc([])
        result = await query_distribution(db, "org-1", "order", tr=_mock_tr())
        assert result.status == "empty"

    @pytest.mark.asyncio
    async def test_rpc_error(self):
        db = MagicMock()
        db.rpc = MagicMock(side_effect=Exception("RPC timeout"))
        result = await query_distribution(db, "org-1", "order", tr=_mock_tr())
        assert result.status == "error"
        assert "失败" in result.summary

    @pytest.mark.asyncio
    async def test_jsonb_string_response(self):
        """Supabase 有时返回 JSON 字符串而非 Python 对象。"""
        import json
        raw = json.dumps([{"bucket": "0~50", "count": 10, "bucket_total": 200}])
        db = _mock_db_rpc(raw)
        result = await query_distribution(db, "org-1", "order", tr=_mock_tr())
        assert result.status == "success"
        assert result.data[0]["count"] == 10

    @pytest.mark.asyncio
    async def test_doc_type_passed_for_document_items(self):
        db = _mock_db_rpc([])
        await query_distribution(
            db, "org-1", "order", tr=_mock_tr(), metrics=["amount"],
        )
        call_args = db.rpc.call_args
        assert call_args[0][1]["p_doc_type"] == "order"


# ============================================================
# 格式化函数
# ============================================================


class TestFormatDistributionSummary:
    def test_basic(self):
        rows = [
            {"bucket": "0~50", "count": 120, "bucket_total": 3800},
            {"bucket": "50~100", "count": 80, "bucket_total": 6000},
        ]
        s = format_distribution_summary(rows, "amount", "订单")
        assert "订单金额分布" in s
        assert "共 200 条" in s
        assert "60.0%" in s  # 120/200

    def test_empty(self):
        s = format_distribution_summary([], "amount", "订单")
        assert "无数据" in s

    def test_unknown_field(self):
        rows = [{"bucket": "0~100", "count": 5, "bucket_total": 50}]
        s = format_distribution_summary(rows, "unknown_field", "测试")
        assert "unknown_field" in s
