"""参数安全护栏测试"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.kuaimai.param_guardrails import (
    _match_items,
    broadened_code_query,
    diagnose_empty_result,
    extract_base_code,
    preprocess_params,
)
from services.kuaimai.registry.base import ApiEntry


# ── 测试用 ApiEntry fixtures ──────────────────────────

def _stock_entry() -> ApiEntry:
    """stock_status 模拟条目（支持 outer_id + sku_outer_id）"""
    return ApiEntry(
        method="stock.api.status.query",
        description="库存查询",
        param_map={
            "outer_id": "mainOuterId",
            "sku_outer_id": "skuOuterId",
            "warehouse_id": "warehouseId",
        },
        retry_alt_params={
            "outer_id": "sku_outer_id",
            "sku_outer_id": "outer_id",
        },
        response_key="stockStatusVoList",
    )


def _order_entry() -> ApiEntry:
    """order_list 模拟条目（支持 order_id + system_id）"""
    return ApiEntry(
        method="erp.trade.list.query",
        description="订单查询",
        param_map={
            "order_id": "tid",
            "system_id": "sid",
            "status": "status",
        },
        retry_alt_params={
            "order_id": "system_id",
            "system_id": "order_id",
        },
    )


def _log_entry() -> ApiEntry:
    """order_log 模拟条目（只有 system_id，没有 order_id）"""
    return ApiEntry(
        method="erp.trade.trace.list",
        description="订单日志",
        param_map={
            "system_ids": "sids",
        },
    )


def _no_sku_entry() -> ApiEntry:
    """product_detail 模拟条目（只有 outer_id，没有 sku_outer_id）"""
    return ApiEntry(
        method="item.single.get",
        description="商品详情",
        param_map={
            "outer_id": "outerId",
            "item_id": "sysItemId",
        },
    )


# ── preprocess_params 测试 ────────────────────────────
# 编码互转（outer_id ↔ sku_outer_id）已移到 param_mapper 同义参数兜底
# 此处仅测试 order_id → system_id 格式校验纠正


class TestPreprocessSystemId:
    """16位纯数字 order_id → system_id 自动纠正"""

    def test_16digit_to_system_ids(self):
        """action 只有 system_ids 时，16位数字自动从 order_id 改为 system_ids"""
        entry = _log_entry()
        params, corrections = preprocess_params(
            entry, {"order_id": "5759422420146938"},
        )
        assert "system_ids" in params
        assert params["system_ids"] == "5759422420146938"
        assert "order_id" not in params
        assert len(corrections) == 1

    def test_order_id_supported_no_correction(self):
        """action 支持 order_id 时不纠正"""
        entry = _order_entry()
        params, corrections = preprocess_params(
            entry, {"order_id": "5759422420146938"},
        )
        assert "order_id" in params
        assert len(corrections) == 0

    def test_18digit_no_correction(self):
        """18位数字不纠正（淘宝订单号）"""
        entry = _log_entry()
        params, corrections = preprocess_params(
            entry, {"order_id": "126036803257340376"},
        )
        assert "order_id" in params
        assert len(corrections) == 0


class TestPreprocessNoSideEffects:
    """预处理不影响无关参数"""

    def test_other_params_preserved(self):
        """其他参数不受影响"""
        entry = _stock_entry()
        params, corrections = preprocess_params(
            entry, {"outer_id": "ABC123", "warehouse_id": "123"},
        )
        assert params["outer_id"] == "ABC123"
        assert params["warehouse_id"] == "123"
        assert len(corrections) == 0

    def test_empty_params(self):
        """空参数不报错"""
        entry = _stock_entry()
        params, corrections = preprocess_params(entry, {})
        assert params == {}
        assert corrections == []


# ── diagnose_empty_result 测试 ────────────────────────


class TestDiagnoseEmptyResult:
    """零结果诊断建议"""

    def test_empty_result_with_outer_id(self):
        """outer_id 查询返回 0 条 → 建议改用 sku_outer_id"""
        entry = _stock_entry()
        data = {"total": 0, "stockStatusVoList": []}
        suggestion = diagnose_empty_result(
            entry, {"outer_id": "DBTXL01-02"}, data,
        )
        assert suggestion is not None
        assert "sku_outer_id" in suggestion
        assert "DBTXL01-02" in suggestion

    def test_empty_result_with_sku_outer_id(self):
        """sku_outer_id 查询返回 0 条 → 建议改用 outer_id"""
        entry = _stock_entry()
        data = {"total": 0, "stockStatusVoList": []}
        suggestion = diagnose_empty_result(
            entry, {"sku_outer_id": "ABC123"}, data,
        )
        assert suggestion is not None
        assert "outer_id" in suggestion

    def test_nonempty_result_no_suggestion(self):
        """有结果时不生成建议"""
        entry = _stock_entry()
        data = {"total": 1, "stockStatusVoList": [{"title": "test"}]}
        suggestion = diagnose_empty_result(
            entry, {"outer_id": "ABC123"}, data,
        )
        assert suggestion is None

    def test_no_retry_config_no_suggestion(self):
        """无 retry_alt_params 配置时不生成建议"""
        entry = _no_sku_entry()
        data = {"total": 0, "list": []}
        suggestion = diagnose_empty_result(
            entry, {"outer_id": "ABC123"}, data,
        )
        assert suggestion is None

    def test_both_params_present_no_suggestion(self):
        """同时传了两个参数时不生成建议"""
        entry = _stock_entry()
        data = {"total": 0, "stockStatusVoList": []}
        suggestion = diagnose_empty_result(
            entry,
            {"outer_id": "ABC123", "sku_outer_id": "ABC123-01"},
            data,
        )
        assert suggestion is None

    def test_order_id_empty_suggests_system_id(self):
        """order_id 查询返回 0 条 → 建议改用 system_id"""
        entry = _order_entry()
        data = {"total": 0, "list": []}
        suggestion = diagnose_empty_result(
            entry, {"order_id": "12345"}, data,
        )
        assert suggestion is not None
        assert "system_id" in suggestion

    def test_total_positive_no_suggestion(self):
        """total > 0 时不生成建议（即使列表为空，可能是分页问题）"""
        entry = _stock_entry()
        data = {"total": 5, "stockStatusVoList": []}
        suggestion = diagnose_empty_result(
            entry, {"outer_id": "ABC123"}, data,
        )
        assert suggestion is None


# ── 编码驱动宽泛查询测试 ─────────────────────────────


class TestExtractBaseCode:
    """extract_base_code 编码拆分测试"""

    def test_sku_suffix(self):
        """DBTXL01-02 → DBTXL"""
        assert extract_base_code("DBTXL01-02") == "DBTXL"

    def test_letters_digits(self):
        """ABC123 → ABC"""
        assert extract_base_code("ABC123") == "ABC"

    def test_hyphen_digit_middle(self):
        """HM-2026A → HM"""
        assert extract_base_code("HM-2026A") == "HM"

    def test_pure_digits_returns_none(self):
        """纯数字 → None"""
        assert extract_base_code("12345") is None

    def test_pure_letters_returns_none(self):
        """已是纯字母 → None"""
        assert extract_base_code("DBTXL") is None

    def test_short_prefix_returns_none(self):
        """基础编码太短（<2字符）→ None"""
        assert extract_base_code("A-1") is None


class TestMatchItems:
    """_match_items 结果匹配测试"""

    def test_exact_match(self):
        """outerId 精确匹配"""
        items = [
            {"outerId": "DBTXL01-01", "mainOuterId": "DBTXL"},
            {"outerId": "DBTXL01-02", "mainOuterId": "DBTXL"},
            {"outerId": "DBTXL02-01", "mainOuterId": "DBTXL"},
        ]
        matched = _match_items(items, "DBTXL01-02")
        assert len(matched) == 1
        assert matched[0]["outerId"] == "DBTXL01-02"

    def test_contains_match_fallback(self):
        """无精确匹配时 contains 匹配"""
        items = [
            {"outerId": "PREFIX-DBTXL01-02-SUFFIX", "mainOuterId": "X"},
        ]
        matched = _match_items(items, "DBTXL01-02")
        assert len(matched) == 1

    def test_no_match(self):
        """无匹配"""
        items = [
            {"outerId": "OTHER-01", "mainOuterId": "OTHER"},
        ]
        matched = _match_items(items, "DBTXL01-02")
        assert len(matched) == 0

    def test_case_insensitive(self):
        """大小写不敏感"""
        items = [
            {"outerId": "dbtxl01-02", "mainOuterId": "dbtxl"},
        ]
        matched = _match_items(items, "DBTXL01-02")
        assert len(matched) == 1


class TestBroadenedCodeQuery:
    """broadened_code_query 编码驱动宽泛查询测试"""

    @pytest.mark.asyncio
    async def test_outer_id_broadened_match(self):
        """主编码宽泛查询命中 + 匹配"""
        entry = _stock_entry()
        user_params = {"sku_outer_id": "DBTXL01-02"}
        api_params = {"skuOuterId": "DBTXL01-02"}
        data = {"total": 0, "stockStatusVoList": []}

        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "total": 3,
            "stockStatusVoList": [
                {"outerId": "DBTXL01-01", "mainOuterId": "DBTXL"},
                {"outerId": "DBTXL01-02", "mainOuterId": "DBTXL"},
                {"outerId": "DBTXL02-01", "mainOuterId": "DBTXL"},
            ],
        }

        result = await broadened_code_query(
            entry, user_params, api_params, data,
            mock_client, None, None,
        )
        assert result is not None
        result_data, note = result
        assert len(result_data["stockStatusVoList"]) == 1
        assert result_data["stockStatusVoList"][0]["outerId"] == "DBTXL01-02"
        assert "DBTXL" in note
        assert "1/3" in note

    @pytest.mark.asyncio
    async def test_outer_id_empty_sku_fallback(self):
        """主编码返回0条 → SKU宽泛兜底"""
        entry = _stock_entry()
        user_params = {"outer_id": "DBTXL01-02"}
        api_params = {"mainOuterId": "DBTXL01-02"}
        data = {"total": 0, "stockStatusVoList": []}

        mock_client = AsyncMock()
        # 第一次调用（outer_id=DBTXL）返回空
        # 第二次调用（sku_outer_id=DBTXL）返回结果
        mock_client.request_with_retry.side_effect = [
            {"total": 0, "stockStatusVoList": []},
            {
                "total": 2,
                "stockStatusVoList": [
                    {"outerId": "DBTXL01-02", "skuOuterId": "DBTXL01-02"},
                    {"outerId": "DBTXL03-01", "skuOuterId": "DBTXL03-01"},
                ],
            },
        ]

        result = await broadened_code_query(
            entry, user_params, api_params, data,
            mock_client, None, None,
        )
        assert result is not None
        result_data, note = result
        assert len(result_data["stockStatusVoList"]) == 1
        assert "sku_outer_id" in note

    @pytest.mark.asyncio
    async def test_both_empty_returns_none(self):
        """主编码和SKU都返回0条 → None"""
        entry = _stock_entry()
        user_params = {"outer_id": "DBTXL01-02"}
        api_params = {"mainOuterId": "DBTXL01-02"}
        data = {"total": 0, "stockStatusVoList": []}

        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "total": 0, "stockStatusVoList": [],
        }

        result = await broadened_code_query(
            entry, user_params, api_params, data,
            mock_client, None, None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_write_operation_skipped(self):
        """写操作不启用宽泛查询"""
        entry = ApiEntry(
            method="stock.update",
            description="更新库存",
            param_map={"outer_id": "mainOuterId"},
            is_write=True,
        )
        result = await broadened_code_query(
            entry, {"outer_id": "ABC-01"}, {}, {"total": 0},
            AsyncMock(), None, None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_code_param_skipped(self):
        """无编码参数时跳过"""
        entry = _stock_entry()
        result = await broadened_code_query(
            entry, {"warehouse_id": "123"}, {}, {"total": 0},
            AsyncMock(), None, None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_nonempty_result_skipped(self):
        """初始查询有结果时不触发"""
        entry = _stock_entry()
        data = {"total": 1, "stockStatusVoList": [{"outerId": "X"}]}
        result = await broadened_code_query(
            entry, {"outer_id": "ABC-01"}, {},
            data, AsyncMock(), None, None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self):
        """API 异常时安全返回 None"""
        entry = _stock_entry()
        user_params = {"outer_id": "DBTXL01-02"}
        data = {"total": 0, "stockStatusVoList": []}

        mock_client = AsyncMock()
        mock_client.request_with_retry.side_effect = Exception("timeout")

        result = await broadened_code_query(
            entry, user_params, {}, data,
            mock_client, None, None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_pure_digit_code_skipped(self):
        """纯数字编码不触发宽泛查询"""
        entry = _stock_entry()
        result = await broadened_code_query(
            entry, {"outer_id": "12345"}, {},
            {"total": 0, "stockStatusVoList": []},
            AsyncMock(), None, None,
        )
        assert result is None
