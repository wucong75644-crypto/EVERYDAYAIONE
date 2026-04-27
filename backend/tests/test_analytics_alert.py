"""
预警查询测试（5 种 alert_type + 边界场景）。

覆盖: erp_analytics_alert.py
设计文档: docs/document/TECH_ERP查询架构重构.md §5.7
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.kuaimai.erp_analytics_alert import (
    ALERT_THRESHOLDS,
    format_alert_summary,
    query_alert,
    _fetch_daily_avg_sales,
    _fetch_active_skus,
)


# ── mock 工厂 ──


def _mock_db_multi(table_data: dict[str, list[dict]]):
    """构造 mock db，根据 table name 返回不同数据。

    table_data: {"erp_stock_status": [...], "erp_product_daily_stats": [...], ...}
    """
    def _build_chain(rows):
        resp = MagicMock()
        resp.data = rows
        resp.count = len(rows)

        q = MagicMock()
        for method in ("eq", "neq", "gt", "gte", "lt", "lte",
                        "ilike", "order", "limit", "in_"):
            setattr(q, method, MagicMock(return_value=q))
        q.not_ = MagicMock()
        q.not_.in_ = MagicMock(return_value=q)
        q.execute = MagicMock(return_value=resp)
        return q

    def table_fn(name):
        rows = table_data.get(name, [])
        chain = _build_chain(rows)
        tbl = MagicMock()
        tbl.select = MagicMock(return_value=chain)
        return tbl

    db = MagicMock()
    db.table = MagicMock(side_effect=table_fn)
    return db


# ── 测试数据 ──

_STOCK_DATA = [
    {"outer_id": "SKU-A", "item_name": "商品A", "available_stock": 5,
     "total_stock": 10, "sellable_num": 5},
    {"outer_id": "SKU-B", "item_name": "商品B", "available_stock": 200,
     "total_stock": 200, "sellable_num": 200},
    {"outer_id": "SKU-C", "item_name": "商品C", "available_stock": 0,
     "total_stock": 0, "sellable_num": 0},
    {"outer_id": "SKU-D", "item_name": "商品D", "available_stock": 500,
     "total_stock": 500, "sellable_num": 500},
]

_DAILY_STATS_DATA = [
    {"outer_id": "SKU-A", "order_qty": 30},   # 日均 1（30天）
    {"outer_id": "SKU-B", "order_qty": 60},   # 日均 2
    {"outer_id": "SKU-C", "order_qty": 90},   # 日均 3（有销量但库存=0）
    {"outer_id": "SKU-D", "order_qty": 30},   # 日均 1（库存500/日均1=500天）
]

_PRODUCT_DATA = [
    {"outer_id": "SKU-A", "title": "商品A"},
    {"outer_id": "SKU-B", "title": "商品B"},
    {"outer_id": "SKU-C", "title": "商品C"},
    {"outer_id": "SKU-D", "title": "商品D"},
    {"outer_id": "SKU-E", "title": "商品E（无销量无库存）"},
]

_PURCHASE_OVERDUE_DATA = [
    {"doc_code": "PO-001", "supplier_name": "供应商甲", "item_name": "物料X",
     "outer_id": "MAT-X", "quantity": 100, "delivery_date": "2026-04-01",
     "doc_created_at": "2026-03-15"},
    {"doc_code": "PO-002", "supplier_name": "供应商乙", "item_name": "物料Y",
     "outer_id": "MAT-Y", "quantity": 50, "delivery_date": "2026-04-20",
     "doc_created_at": "2026-04-01"},
]


# ============================================================
# 缺货预警
# ============================================================


class TestLowStock:
    @pytest.mark.asyncio
    async def test_basic(self):
        db = _mock_db_multi({
            "erp_stock_status": _STOCK_DATA,
            "erp_product_daily_stats": _DAILY_STATS_DATA,
        })
        result = await query_alert(db, "org-1", "low_stock")

        assert result.status == "success"
        assert result.metadata["query_type"] == "alert"
        assert result.metadata["alert_type"] == "low_stock"
        # SKU-A: 5/1=5天 → warning; SKU-B: 200/2=100天 → 超过14天不告警
        alerts = result.data
        assert any(a["outer_id"] == "SKU-A" for a in alerts)
        assert not any(a["outer_id"] == "SKU-B" for a in alerts)

    @pytest.mark.asyncio
    async def test_severity_levels(self):
        stock = [
            {"outer_id": "X1", "item_name": "紧急", "available_stock": 2,
             "total_stock": 2, "sellable_num": 2},
            {"outer_id": "X2", "item_name": "警告", "available_stock": 10,
             "total_stock": 10, "sellable_num": 10},
            {"outer_id": "X3", "item_name": "提醒", "available_stock": 20,
             "total_stock": 20, "sellable_num": 20},
        ]
        daily = [
            {"outer_id": "X1", "order_qty": 30},  # 日均1，库存2→2天→critical
            {"outer_id": "X2", "order_qty": 60},  # 日均2，库存10→5天→warning
            {"outer_id": "X3", "order_qty": 60},  # 日均2，库存20→10天→info
        ]
        db = _mock_db_multi({
            "erp_stock_status": stock,
            "erp_product_daily_stats": daily,
        })
        result = await query_alert(db, "org-1", "low_stock")
        sev_map = {a["outer_id"]: a["severity"] for a in result.data}
        assert sev_map["X1"] == "critical"
        assert sev_map["X2"] == "warning"
        assert sev_map["X3"] == "info"

    @pytest.mark.asyncio
    async def test_no_sales_skipped(self):
        """无销量 SKU 不应出现在缺货预警中。"""
        stock = [{"outer_id": "Z1", "item_name": "无销量", "available_stock": 3,
                  "total_stock": 3, "sellable_num": 3}]
        db = _mock_db_multi({
            "erp_stock_status": stock,
            "erp_product_daily_stats": [],
        })
        result = await query_alert(db, "org-1", "low_stock")
        assert result.status == "empty"
        assert result.data == []

    @pytest.mark.asyncio
    async def test_suggestion_text(self):
        stock = [{"outer_id": "S1", "item_name": "测试", "available_stock": 5,
                  "total_stock": 5, "sellable_num": 5}]
        daily = [{"outer_id": "S1", "order_qty": 60}]  # 日均2
        db = _mock_db_multi({
            "erp_stock_status": stock,
            "erp_product_daily_stats": daily,
        })
        result = await query_alert(db, "org-1", "low_stock")
        assert "建议补货" in result.data[0]["suggestion"]


# ============================================================
# 滞销预警
# ============================================================


class TestSlowMoving:
    @pytest.mark.asyncio
    async def test_basic(self):
        db = _mock_db_multi({
            "erp_stock_status": _STOCK_DATA,
            "erp_product_daily_stats": _DAILY_STATS_DATA,
            "erp_products": _PRODUCT_DATA,
        })
        result = await query_alert(db, "org-1", "slow_moving")
        assert result.metadata["alert_type"] == "slow_moving"
        # SKU-E 无销量且无库存 → 不应出现（已归零）
        # 有库存但无销量的才算滞销
        oids = {a["outer_id"] for a in result.data}
        assert "SKU-E" not in oids  # 无库存不算滞销

    @pytest.mark.asyncio
    async def test_sorted_by_stock_desc(self):
        stock = [
            {"outer_id": "A", "item_name": "A", "available_stock": 50,
             "total_stock": 50, "sellable_num": 50},
            {"outer_id": "B", "item_name": "B", "available_stock": 200,
             "total_stock": 200, "sellable_num": 200},
        ]
        db = _mock_db_multi({
            "erp_stock_status": stock,
            "erp_product_daily_stats": [],
            "erp_products": [
                {"outer_id": "A", "title": "A"},
                {"outer_id": "B", "title": "B"},
            ],
        })
        result = await query_alert(db, "org-1", "slow_moving")
        assert result.data[0]["outer_id"] == "B"  # 库存200排前面


# ============================================================
# 积压预警
# ============================================================


class TestOverstock:
    @pytest.mark.asyncio
    async def test_basic(self):
        db = _mock_db_multi({
            "erp_stock_status": _STOCK_DATA,
            "erp_product_daily_stats": _DAILY_STATS_DATA,
        })
        result = await query_alert(db, "org-1", "overstock")
        # SKU-D: 500/1=500天 > 90 → 积压
        # SKU-B: 200/2=100天 > 90 → 积压
        # SKU-A: 5/1=5天 < 90 → 不积压
        oids = {a["outer_id"] for a in result.data}
        assert "SKU-D" in oids
        assert "SKU-B" in oids
        assert "SKU-A" not in oids

    @pytest.mark.asyncio
    async def test_excess_qty(self):
        stock = [{"outer_id": "X", "item_name": "X", "available_stock": 200,
                  "total_stock": 200, "sellable_num": 200}]
        daily = [{"outer_id": "X", "order_qty": 30}]  # 日均1
        db = _mock_db_multi({
            "erp_stock_status": stock,
            "erp_product_daily_stats": daily,
        })
        result = await query_alert(db, "org-1", "overstock")
        assert result.data[0]["excess_qty"] == round(200 - 1 * 90)


# ============================================================
# 热销断货
# ============================================================


class TestOutOfStock:
    @pytest.mark.asyncio
    async def test_basic(self):
        db = _mock_db_multi({
            "erp_stock_status": _STOCK_DATA,
            "erp_product_daily_stats": _DAILY_STATS_DATA,
        })
        result = await query_alert(db, "org-1", "out_of_stock")
        # SKU-C: 库存=0, 有销量 → 断货
        assert any(a["outer_id"] == "SKU-C" for a in result.data)
        assert all(a["severity"] == "critical" for a in result.data)

    @pytest.mark.asyncio
    async def test_zero_stock_no_sales(self):
        """库存=0 且无销量不算热销断货。"""
        stock = [{"outer_id": "Z", "item_name": "Z", "available_stock": 0,
                  "total_stock": 0, "sellable_num": 0}]
        db = _mock_db_multi({
            "erp_stock_status": stock,
            "erp_product_daily_stats": [],
        })
        result = await query_alert(db, "org-1", "out_of_stock")
        assert result.status == "empty"


# ============================================================
# 采购超期
# ============================================================


class TestPurchaseOverdue:
    @pytest.mark.asyncio
    async def test_basic(self):
        db = _mock_db_multi({
            "erp_document_items": _PURCHASE_OVERDUE_DATA,
        })
        result = await query_alert(db, "org-1", "purchase_overdue")
        assert result.metadata["alert_type"] == "purchase_overdue"
        assert len(result.data) == 2
        for r in result.data:
            assert "overdue_days" in r
            assert "severity" in r

    @pytest.mark.asyncio
    async def test_empty(self):
        db = _mock_db_multi({"erp_document_items": []})
        result = await query_alert(db, "org-1", "purchase_overdue")
        assert result.status == "empty"


# ============================================================
# 无效 alert_type
# ============================================================


class TestInvalidAlertType:
    @pytest.mark.asyncio
    async def test_unknown_type(self):
        db = _mock_db_multi({})
        result = await query_alert(db, "org-1", "unknown_type")
        assert result.status == "error"
        assert "不支持" in result.summary


# ============================================================
# 格式化函数
# ============================================================


class TestFormatAlertSummary:
    def test_empty(self):
        s = format_alert_summary([], "low_stock", 0)
        assert "暂无预警项" in s

    def test_with_data(self):
        alerts = [
            {"item_name": "A", "days_left": 2, "severity": "critical"},
            {"item_name": "B", "days_left": 5, "severity": "warning"},
        ]
        s = format_alert_summary(alerts, "low_stock", 2)
        assert "缺货预警" in s
        assert "紧急 1 项" in s
        assert "警告 1 项" in s
        assert "还能卖" in s

    def test_overstock_format(self):
        alerts = [
            {"item_name": "X", "days_of_stock": 180, "excess_qty": 90,
             "severity": "warning"},
        ]
        s = format_alert_summary(alerts, "overstock", 1)
        assert "积压预警" in s
        assert "库存够卖" in s

    def test_purchase_overdue_format(self):
        alerts = [
            {"doc_code": "PO-1", "supplier_name": "供应商A",
             "overdue_days": 10, "severity": "warning"},
        ]
        s = format_alert_summary(alerts, "purchase_overdue", 1)
        assert "采购超期" in s
        assert "PO-1" in s

    def test_truncation_hint(self):
        alerts = [{"item_name": f"item{i}", "days_left": i, "severity": "info"}
                  for i in range(5)]
        s = format_alert_summary(alerts, "low_stock", 5)
        assert "等共 5 项" in s


# ============================================================
# limit 截断
# ============================================================


class TestLimitTruncation:
    @pytest.mark.asyncio
    async def test_limit_respected(self):
        stock = [
            {"outer_id": f"S{i}", "item_name": f"商品{i}",
             "available_stock": 3, "total_stock": 3, "sellable_num": 3}
            for i in range(20)
        ]
        daily = [{"outer_id": f"S{i}", "order_qty": 30} for i in range(20)]
        db = _mock_db_multi({
            "erp_stock_status": stock,
            "erp_product_daily_stats": daily,
        })
        result = await query_alert(db, "org-1", "low_stock", limit=5)
        assert len(result.data) <= 5
        assert result.metadata["total_alerts"] == 20
