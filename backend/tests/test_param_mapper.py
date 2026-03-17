"""参数映射器测试"""

import pytest

from services.kuaimai.param_mapper import map_params
from services.kuaimai.registry.base import ApiEntry


# ── 测试用 ApiEntry fixtures ──────────────────────────


def _stock_entry() -> ApiEntry:
    """库存查询（同时支持 outer_id 和 sku_outer_id）"""
    return ApiEntry(
        method="stock.api.status.query",
        description="库存查询",
        param_map={
            "outer_id": "mainOuterId",
            "sku_outer_id": "skuOuterId",
            "warehouse_id": "warehouseId",
        },
    )


def _stock_in_out_entry() -> ApiEntry:
    """出入库流水（只有 outer_id，没有 sku_outer_id）"""
    return ApiEntry(
        method="erp.item.stock.in.out.list",
        description="出入库流水",
        param_map={
            "outer_id": "outerId",
            "warehouse_id": "warehouseId",
            "start_date": "operateTimeBegin",
        },
    )


def _sku_only_entry() -> ApiEntry:
    """模拟只有 sku_outer_id 的条目"""
    return ApiEntry(
        method="sku.info.query",
        description="SKU信息查询",
        param_map={
            "sku_outer_id": "skuOuterId",
            "item_id": "sysItemId",
        },
    )


# ── 同义参数兜底测试 ─────────────────────────────────


class TestSynonymFallback:
    """_PARAM_SYNONYMS 同义参数兜底映射"""

    def test_sku_outer_id_fallback_to_outer_id(self):
        """action 只有 outer_id 时，sku_outer_id 自动用 outer_id 的映射"""
        entry = _stock_in_out_entry()
        mapped, warnings = map_params(entry, {"sku_outer_id": "SEVENTEENLSG01-01"})
        assert mapped["outerId"] == "SEVENTEENLSG01-01"
        assert "sku_outer_id" not in warnings

    def test_outer_id_fallback_to_sku_outer_id(self):
        """action 只有 sku_outer_id 时，outer_id 自动用 sku_outer_id 的映射"""
        entry = _sku_only_entry()
        mapped, warnings = map_params(entry, {"outer_id": "ABC123"})
        assert mapped["skuOuterId"] == "ABC123"
        assert "outer_id" not in warnings

    def test_both_supported_no_fallback(self):
        """action 同时支持两个参数时，各自映射，不走兜底"""
        entry = _stock_entry()
        mapped, warnings = map_params(
            entry, {"outer_id": "MAIN", "sku_outer_id": "SKU01"},
        )
        assert mapped["mainOuterId"] == "MAIN"
        assert mapped["skuOuterId"] == "SKU01"
        assert len(warnings) == 0

    def test_single_param_direct_mapping(self):
        """有直接映射时不走兜底"""
        entry = _stock_entry()
        mapped, warnings = map_params(entry, {"sku_outer_id": "ABC-01"})
        assert mapped["skuOuterId"] == "ABC-01"
        assert "mainOuterId" not in mapped  # outer_id 未传，不会映射

    def test_unknown_param_still_warned(self):
        """非同义参数仍然被警告"""
        entry = _stock_in_out_entry()
        mapped, warnings = map_params(entry, {"unknown_param": "value"})
        assert "unknown_param" in warnings

    def test_synonym_with_other_params_preserved(self):
        """同义兜底不影响其他正常参数"""
        entry = _stock_in_out_entry()
        mapped, warnings = map_params(
            entry, {
                "sku_outer_id": "DBTXL01-02",
                "warehouse_id": "W001",
                "start_date": "2026-03-01",
            },
        )
        assert mapped["outerId"] == "DBTXL01-02"
        assert mapped["warehouseId"] == "W001"
        assert len(warnings) == 0


# ── 别名解析测试 ─────────────────────────────────────


class TestAliasResolving:
    """中文/常见别名 → 标准参数名"""

    def test_chinese_alias_outer_id(self):
        """商家编码 → outer_id"""
        entry = _stock_entry()
        mapped, warnings = map_params(entry, {"商家编码": "ABC123"})
        assert mapped["mainOuterId"] == "ABC123"

    def test_chinese_alias_sku_outer_id(self):
        """规格编码 → sku_outer_id"""
        entry = _stock_entry()
        mapped, warnings = map_params(entry, {"规格编码": "SKU-01"})
        assert mapped["skuOuterId"] == "SKU-01"

    def test_alias_plus_synonym(self):
        """别名解析 + 同义兜底联动：规格编码→sku_outer_id→outer_id兜底"""
        entry = _stock_in_out_entry()
        mapped, warnings = map_params(entry, {"规格编码": "DBTXL01-02"})
        # 规格编码 → sku_outer_id → 兜底到 outer_id 映射
        assert mapped["outerId"] == "DBTXL01-02"


# ── 分页和日期测试 ────────────────────────────────────


class TestPaginationAndDates:
    """分页和日期标准化"""

    def test_default_pagination(self):
        """默认分页参数"""
        entry = _stock_entry()
        mapped, _ = map_params(entry, {})
        assert mapped["pageNo"] == 1
        assert mapped["pageSize"] == 20

    def test_page_size_minimum_20(self):
        """pageSize 最小值 20"""
        entry = _stock_entry()
        mapped, _ = map_params(entry, {"page_size": 5})
        assert mapped["pageSize"] == 20

    def test_date_normalization_start(self):
        """start 日期补全 00:00:00"""
        entry = _stock_in_out_entry()
        mapped, _ = map_params(entry, {"start_date": "2026-03-01"})
        assert mapped["operateTimeBegin"] == "2026-03-01 00:00:00"
