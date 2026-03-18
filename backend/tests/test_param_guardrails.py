"""参数安全护栏测试"""

from unittest.mock import AsyncMock

import pytest

from services.kuaimai.param_guardrails import (
    _find_code_param,
    _match_items,
    _match_items_batch,
    _deduplicate_items,
    apply_code_broadening,
    diagnose_empty_result,
    extract_base_code,
    preprocess_params,
    try_batch_dual_query,
    try_broadened_queries,
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


def _warehouse_stock_entry() -> ApiEntry:
    """warehouse_stock 模拟条目（single_code_only, response_key=None）"""
    return ApiEntry(
        method="erp.item.warehouse.list.get",
        description="仓库库存查询",
        param_map={
            "outer_id": "outerId",
            "sku_outer_id": "skuOuterId",
        },
        single_code_only=True,
        response_key=None,
    )


def _batch_entry() -> ApiEntry:
    """item_supplier_list 模拟条目（批量，支持 outer_ids + sku_outer_ids）"""
    return ApiEntry(
        method="erp.item.supplier.list.get",
        description="供应商查询",
        param_map={
            "outer_ids": "outerIds",
            "sku_outer_ids": "skuOuterIds",
        },
        response_key="suppliers",
    )


def _batch_single_param_entry() -> ApiEntry:
    """multi_product 模拟条目（批量，只有 outer_ids）"""
    return ApiEntry(
        method="erp.item.list.get",
        description="批量商品查询",
        param_map={
            "outer_ids": "outerIds",
        },
        response_key="items",
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
        assert extract_base_code("DBTXL01-02") == "DBTXL"

    def test_letters_digits(self):
        assert extract_base_code("ABC123") == "ABC"

    def test_hyphen_digit_middle(self):
        assert extract_base_code("HM-2026A") == "HM"

    def test_pure_digits_returns_none(self):
        assert extract_base_code("12345") is None

    def test_pure_letters_returns_none(self):
        assert extract_base_code("DBTXL") is None

    def test_short_prefix_returns_none(self):
        assert extract_base_code("A-1") is None


class TestMatchItems:
    """_match_items 结果匹配测试"""

    def test_exact_match(self):
        items = [
            {"outerId": "DBTXL01-01", "mainOuterId": "DBTXL"},
            {"outerId": "DBTXL01-02", "mainOuterId": "DBTXL"},
            {"outerId": "DBTXL02-01", "mainOuterId": "DBTXL"},
        ]
        matched = _match_items(items, "DBTXL01-02")
        assert len(matched) == 1
        assert matched[0]["outerId"] == "DBTXL01-02"

    def test_contains_match_fallback(self):
        items = [
            {"outerId": "PREFIX-DBTXL01-02-SUFFIX", "mainOuterId": "X"},
        ]
        matched = _match_items(items, "DBTXL01-02")
        assert len(matched) == 1

    def test_no_match(self):
        items = [{"outerId": "OTHER-01", "mainOuterId": "OTHER"}]
        matched = _match_items(items, "DBTXL01-02")
        assert len(matched) == 0

    def test_case_insensitive(self):
        items = [{"outerId": "dbtxl01-02", "mainOuterId": "dbtxl"}]
        matched = _match_items(items, "DBTXL01-02")
        assert len(matched) == 1


# ── _find_code_param 测试 ─────────────────────────────


class TestFindCodeParam:
    """_find_code_param 编码参数识别"""

    def test_outer_id(self):
        result = _find_code_param({"outer_id": "ABC123"})
        assert result == ("outer_id", "ABC123", False)

    def test_sku_outer_id(self):
        result = _find_code_param({"sku_outer_id": "SKU001"})
        assert result == ("sku_outer_id", "SKU001", False)

    def test_outer_ids_batch(self):
        result = _find_code_param({"outer_ids": "A,B,C"})
        assert result == ("outer_ids", "A,B,C", True)

    def test_sku_outer_ids_batch(self):
        result = _find_code_param({"sku_outer_ids": "S1,S2"})
        assert result == ("sku_outer_ids", "S1,S2", True)

    def test_singular_takes_priority(self):
        """单数优先于复数（同时存在时）"""
        result = _find_code_param({
            "outer_id": "ABC", "outer_ids": "A,B,C",
        })
        assert result is not None
        assert result[2] is False  # is_batch=False

    def test_no_code_param(self):
        result = _find_code_param({"warehouse_id": "123"})
        assert result is None


# ── apply_code_broadening 测试 ────────────────────────


class TestApplyCodeBroadening:
    """apply_code_broadening 编码宽泛化预处理"""

    def test_single_normal_packing(self):
        """单条正常编码：原始+宽泛打包"""
        entry = _stock_entry()
        api_params = {"mainOuterId": "DBTXL01-02"}
        result = apply_code_broadening(
            entry, {"outer_id": "DBTXL01-02"}, api_params,
        )
        assert result is not None
        original, packed, api_keys, is_batch = result
        assert original == "DBTXL01-02"
        assert packed == "DBTXL01-02,DBTXL"
        assert len(api_keys) == 2
        assert is_batch is False
        # api_params 中原有编码被清除
        assert "mainOuterId" not in api_params

    def test_single_pure_digit(self):
        """单条纯数字：不宽泛但仍返回api_keys做双参数试"""
        entry = _stock_entry()
        result = apply_code_broadening(
            entry, {"outer_id": "12345"}, {},
        )
        assert result is not None
        original, packed, api_keys, is_batch = result
        assert packed == "12345"  # 无宽泛
        assert len(api_keys) == 2  # 仍有双参数

    def test_single_code_only(self):
        """single_code_only：不打包"""
        entry = _warehouse_stock_entry()
        result = apply_code_broadening(
            entry, {"outer_id": "DBTXL01-02"}, {},
        )
        assert result is not None
        original, packed, api_keys, is_batch = result
        assert packed == "DBTXL01-02"  # 不打包宽泛
        assert len(api_keys) == 2

    def test_batch_dual_params(self):
        """批量双参数：返回2个api_keys"""
        entry = _batch_entry()
        api_params = {"outerIds": "A1,B2"}
        result = apply_code_broadening(
            entry, {"outer_ids": "A1,B2"}, api_params,
        )
        assert result is not None
        original, packed, api_keys, is_batch = result
        assert is_batch is True
        assert len(api_keys) == 2
        assert "outerIds" not in api_params

    def test_batch_single_param(self):
        """批量单参数：返回1个api_key（不跳过）"""
        entry = _batch_single_param_entry()
        result = apply_code_broadening(
            entry, {"outer_ids": "ABC123"}, {},
        )
        assert result is not None
        _, _, api_keys, is_batch = result
        assert is_batch is True
        assert len(api_keys) == 1

    def test_batch_over_20_drops_broadening(self):
        """批量超20个放弃宽泛，packed=原始编码"""
        entry = _batch_entry()
        # 11个编码，每个有唯一基础编码 → 11 + 11 = 22 > 20
        prefixes = ["AA", "BB", "CC", "DD", "EE", "FF", "GG", "HH", "II", "JJ", "KK"]
        codes = ",".join(f"{p}01" for p in prefixes)
        result = apply_code_broadening(
            entry, {"outer_ids": codes}, {},
        )
        assert result is not None
        original, packed, _, _ = result
        assert packed == codes  # 放弃宽泛

    def test_write_operation_skipped(self):
        """写操作跳过"""
        entry = ApiEntry(
            method="stock.update", description="更新库存",
            param_map={"outer_id": "mainOuterId"}, is_write=True,
        )
        result = apply_code_broadening(
            entry, {"outer_id": "ABC-01"}, {},
        )
        assert result is None

    def test_no_code_param_skipped(self):
        """无编码参数跳过"""
        entry = _stock_entry()
        result = apply_code_broadening(
            entry, {"warehouse_id": "123"}, {},
        )
        assert result is None

    def test_batch_no_matching_api_keys(self):
        """批量无任何编码参数时跳过"""
        entry = _order_entry()  # 没有 outer_ids / sku_outer_ids
        result = apply_code_broadening(
            entry, {"outer_ids": "A,B"}, {},
        )
        assert result is None

    def test_api_params_code_cleared(self):
        """api_params原有编码key被清除"""
        entry = _stock_entry()
        api_params = {
            "mainOuterId": "X", "skuOuterId": "Y", "warehouseId": "1",
        }
        apply_code_broadening(
            entry, {"outer_id": "DBTXL01-02"}, api_params,
        )
        assert "mainOuterId" not in api_params
        assert "skuOuterId" not in api_params
        assert api_params["warehouseId"] == "1"


# ── try_broadened_queries 测试 ────────────────────────


class TestTryBroadenedQueries:
    """try_broadened_queries 单条宽泛查询"""

    @pytest.mark.asyncio
    async def test_list_api_first_key_match(self):
        """List API：第一个参数就匹配到"""
        entry = _stock_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "total": 3,
            "stockStatusVoList": [
                {"outerId": "DBTXL01-01", "mainOuterId": "DBTXL"},
                {"outerId": "DBTXL01-02", "mainOuterId": "DBTXL"},
                {"outerId": "DBTXL02-01", "mainOuterId": "DBTXL"},
            ],
        }

        data, note = await try_broadened_queries(
            entry, {}, "DBTXL01-02", "DBTXL01-02,DBTXL",
            ["mainOuterId", "skuOuterId"],
            mock_client, None, None,
        )
        assert len(data["stockStatusVoList"]) == 1
        assert data["stockStatusVoList"][0]["outerId"] == "DBTXL01-02"
        assert "匹配到1条" in note

    @pytest.mark.asyncio
    async def test_list_api_second_key_fallback(self):
        """List API：第一个参数无匹配，第二个参数匹配到"""
        entry = _stock_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.side_effect = [
            {"total": 0, "stockStatusVoList": []},
            {
                "total": 2,
                "stockStatusVoList": [
                    {"outerId": "DBTXL01-02", "skuOuterId": "DBTXL01-02"},
                    {"outerId": "OTHER", "skuOuterId": "OTHER"},
                ],
            },
        ]

        data, note = await try_broadened_queries(
            entry, {}, "DBTXL01-02", "DBTXL01-02,DBTXL",
            ["mainOuterId", "skuOuterId"],
            mock_client, None, None,
        )
        assert len(data["stockStatusVoList"]) == 1
        assert "sku_outer_id" in note

    @pytest.mark.asyncio
    async def test_list_api_no_match(self):
        """List API：两个参数都无匹配"""
        entry = _stock_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "total": 0, "stockStatusVoList": [],
        }

        data, note = await try_broadened_queries(
            entry, {}, "DBTXL01-02", "DBTXL01-02,DBTXL",
            ["mainOuterId", "skuOuterId"],
            mock_client, None, None,
        )
        assert data["stockStatusVoList"] == []
        assert "所有参数均无匹配" in note

    @pytest.mark.asyncio
    async def test_detail_api_first_key_success(self):
        """Detail API（response_key=None）：第一个参数成功即返回"""
        entry = _warehouse_stock_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "warehouses": [{"name": "仓1", "stock": 10}],
        }

        data, note = await try_broadened_queries(
            entry, {}, "DBTXL01-02", "DBTXL01-02",
            ["outerId", "skuOuterId"],
            mock_client, None, None,
        )
        assert "warehouses" in data
        assert "命中" in note

    @pytest.mark.asyncio
    async def test_detail_api_first_error_second_success(self):
        """Detail API：第一个异常，第二个成功"""
        entry = _warehouse_stock_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.side_effect = [
            Exception("not found"),
            {"warehouses": [{"name": "仓1"}]},
        ]

        data, note = await try_broadened_queries(
            entry, {}, "DBTXL01-02", "DBTXL01-02",
            ["outerId", "skuOuterId"],
            mock_client, None, None,
        )
        assert "warehouses" in data
        assert "sku_outer_id" in note

    @pytest.mark.asyncio
    async def test_all_api_errors(self):
        """所有API调用异常时返回空"""
        entry = _stock_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.side_effect = Exception("timeout")

        data, note = await try_broadened_queries(
            entry, {}, "ABC01", "ABC01,ABC",
            ["mainOuterId", "skuOuterId"],
            mock_client, None, None,
        )
        assert data["stockStatusVoList"] == []
        assert "所有参数均无匹配" in note

    @pytest.mark.asyncio
    async def test_empty_items_skipped(self):
        """List API 返回空列表时跳到下一个参数"""
        entry = _stock_entry()
        mock_client = AsyncMock()
        # 第一个返回空列表，第二个返回有数据
        mock_client.request_with_retry.side_effect = [
            {"total": 0, "stockStatusVoList": []},
            {
                "total": 1,
                "stockStatusVoList": [
                    {"outerId": "ABC01", "mainOuterId": "ABC"},
                ],
            },
        ]

        data, note = await try_broadened_queries(
            entry, {}, "ABC01", "ABC01,ABC",
            ["mainOuterId", "skuOuterId"],
            mock_client, None, None,
        )
        assert len(data["stockStatusVoList"]) == 1


# ── try_batch_dual_query 测试 ─────────────────────────


class TestTryBatchDualQuery:
    """try_batch_dual_query 批量双参数查询"""

    @pytest.mark.asyncio
    async def test_dual_param_merge(self):
        """两个参数都有数据，合并去重+本地匹配"""
        entry = _batch_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.side_effect = [
            {"suppliers": [
                {"outerId": "A1", "sysItemId": "1"},
                {"outerId": "B2", "sysItemId": "2"},
            ]},
            {"suppliers": [
                {"outerId": "A1-01", "skuOuterId": "A1-01", "sysItemId": "3"},
            ]},
        ]

        data, note = await try_batch_dual_query(
            entry, {}, "A1,A1-01,B2", "A1,A1-01,B2,ABC",
            ["outerIds", "skuOuterIds"],
            mock_client, None, None,
        )
        assert len(data["suppliers"]) == 3
        assert "批量双参数查询" in note

    @pytest.mark.asyncio
    async def test_single_param_only(self):
        """单参数批量查询（只有1个api_key）"""
        entry = _batch_single_param_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "items": [
                {"outerId": "ABC123", "sysItemId": "1"},
            ],
        }

        data, note = await try_batch_dual_query(
            entry, {}, "ABC123", "ABC123,ABC",
            ["outerIds"],
            mock_client, None, None,
        )
        assert len(data["items"]) == 1

    @pytest.mark.asyncio
    async def test_dedup_removes_duplicates(self):
        """重复数据被去重"""
        entry = _batch_entry()
        mock_client = AsyncMock()
        same_item = {"outerId": "A1", "sysItemId": "1"}
        mock_client.request_with_retry.side_effect = [
            {"suppliers": [same_item]},
            {"suppliers": [same_item.copy()]},
        ]

        data, note = await try_batch_dual_query(
            entry, {}, "A1", "A1",
            ["outerIds", "skuOuterIds"],
            mock_client, None, None,
        )
        assert len(data["suppliers"]) == 1
        assert "合并去重后1条" in note

    @pytest.mark.asyncio
    async def test_api_error_skipped(self):
        """API异常时跳过继续"""
        entry = _batch_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.side_effect = [
            Exception("timeout"),
            {"suppliers": [{"outerId": "A1", "sysItemId": "1"}]},
        ]

        data, note = await try_batch_dual_query(
            entry, {}, "A1", "A1",
            ["outerIds", "skuOuterIds"],
            mock_client, None, None,
        )
        assert len(data["suppliers"]) == 1

    @pytest.mark.asyncio
    async def test_total_correct(self):
        """去重后total正确"""
        entry = _batch_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.side_effect = [
            {"suppliers": [
                {"outerId": "A1", "sysItemId": "1"},
                {"outerId": "B2", "sysItemId": "2"},
            ]},
            {"suppliers": []},
        ]

        data, note = await try_batch_dual_query(
            entry, {}, "A1,B2", "A1,B2",
            ["outerIds", "skuOuterIds"],
            mock_client, None, None,
        )
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_no_exact_match_returns_all(self):
        """本地匹配无精确命中时返回全部"""
        entry = _batch_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "suppliers": [
                {"outerId": "DBTXL", "sysItemId": "1"},  # 宽泛编码命中
            ],
        }

        data, note = await try_batch_dual_query(
            entry, {}, "DBTXL01-02", "DBTXL01-02,DBTXL",
            ["outerIds"],
            mock_client, None, None,
        )
        # 原始编码DBTXL01-02没有精确匹配，返回全部
        assert len(data["suppliers"]) == 1


# ── _deduplicate_items 测试 ───────────────────────────


class TestDeduplicateItems:
    """去重辅助函数"""

    def test_basic_dedup(self):
        items = [
            {"outerId": "A1", "sysItemId": "1"},
            {"outerId": "A1", "sysItemId": "1"},
            {"outerId": "B2", "sysItemId": "2"},
        ]
        result = _deduplicate_items(items)
        assert len(result) == 2

    def test_preserves_order(self):
        items = [
            {"outerId": "B2", "sysItemId": "2"},
            {"outerId": "A1", "sysItemId": "1"},
        ]
        result = _deduplicate_items(items)
        assert result[0]["outerId"] == "B2"
        assert result[1]["outerId"] == "A1"


# ── _match_items_batch 测试 ───────────────────────────


class TestMatchItemsBatch:
    """批量编码本地匹配"""

    def test_exact_match_filter(self):
        items = [
            {"outerId": "A1"},
            {"outerId": "B2"},
            {"outerId": "DBTXL"},
        ]
        matched = _match_items_batch(items, ["A1", "B2"])
        assert len(matched) == 2

    def test_no_match_returns_all(self):
        """无精确匹配时返回全部"""
        items = [{"outerId": "DBTXL"}]
        matched = _match_items_batch(items, ["DBTXL01-02"])
        assert len(matched) == 1  # 返回全部

    def test_case_insensitive(self):
        items = [{"outerId": "abc123"}]
        matched = _match_items_batch(items, ["ABC123"])
        assert len(matched) == 1


# ── _fetch_all_with_limit 测试 ────────────────────────

from services.kuaimai.param_guardrails import _fetch_all_with_limit


class TestFetchAllWithLimit:
    """_fetch_all_with_limit 独立翻页函数"""

    @pytest.mark.asyncio
    async def test_single_page(self):
        """单页即止（返回条数 < pageSize）"""
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "list": [{"id": 1}, {"id": 2}], "total": 2,
        }
        params = {"pageSize": 100}
        data = await _fetch_all_with_limit(
            mock_client, "test.method", params, None, None,
            response_key="list", max_pages=10,
        )
        assert len(data["list"]) == 2
        assert mock_client.request_with_retry.call_count == 1

    @pytest.mark.asyncio
    async def test_multi_page_fetch(self):
        """多页翻页：3页数据合并"""
        mock_client = AsyncMock()
        page_size = 2
        mock_client.request_with_retry.side_effect = [
            {"list": [{"id": 1}, {"id": 2}]},
            {"list": [{"id": 3}, {"id": 4}]},
            {"list": [{"id": 5}]},  # < pageSize → 最后一页
        ]
        params = {"pageSize": page_size}
        data = await _fetch_all_with_limit(
            mock_client, "test.method", params, None, None,
            response_key="list", max_pages=10,
        )
        assert len(data["list"]) == 5
        assert mock_client.request_with_retry.call_count == 3

    @pytest.mark.asyncio
    async def test_max_pages_truncation(self):
        """max_pages 截断：限制最多拉2页"""
        mock_client = AsyncMock()
        page_size = 2
        # 每页都满，理论上应该无限翻页
        mock_client.request_with_retry.return_value = {
            "list": [{"id": 1}, {"id": 2}],
        }
        params = {"pageSize": page_size}
        data = await _fetch_all_with_limit(
            mock_client, "test.method", params, None, None,
            response_key="list", max_pages=2,
        )
        assert len(data["list"]) == 4  # 2页 × 2条
        assert mock_client.request_with_retry.call_count == 2

    @pytest.mark.asyncio
    async def test_custom_response_key(self):
        """自定义 response_key"""
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "stockStatusVoList": [{"sku": "A1"}],
        }
        params = {"pageSize": 100}
        data = await _fetch_all_with_limit(
            mock_client, "test.method", params, None, None,
            response_key="stockStatusVoList",
        )
        assert len(data["stockStatusVoList"]) == 1


# ── apply_code_broadening pageSize 验证 ───────────────


class TestApplyCodeBroadeningPageSize:
    """apply_code_broadening 的 pageSize 设置"""

    def test_broadening_sets_page_size_100(self):
        """有宽泛打包时 pageSize 被设为 100"""
        entry = _stock_entry()
        api_params = {"mainOuterId": "DBTXL01-02", "pageSize": 20}
        apply_code_broadening(
            entry, {"outer_id": "DBTXL01-02"}, api_params,
        )
        assert api_params["pageSize"] == 100

    def test_pure_digit_no_page_size_change(self):
        """纯数字编码不设 pageSize（无宽泛打包）"""
        entry = _stock_entry()
        api_params = {"mainOuterId": "12345", "pageSize": 20}
        apply_code_broadening(
            entry, {"outer_id": "12345"}, api_params,
        )
        assert api_params["pageSize"] == 20
