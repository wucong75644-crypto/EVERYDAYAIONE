"""编码识别器单元测试

覆盖：_guess_code_type（纯规则）、identify_code（含回退）、
      _identify_product / _identify_order / _identify_barcode（Mock API）
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.kuaimai.code_identifier import (
    _fetch_suit_singles,
    _format_suit_singles,
    _guess_code_type,
    _identify_barcode,
    _identify_order,
    _identify_product,
    identify_code,
)
from services.kuaimai.errors import KuaiMaiBusinessError


# ── _guess_code_type 测试（纯规则，无API） ──────────


class TestGuessCodeType:

    def test_barcode_13_digits_69_prefix(self):
        assert _guess_code_type("6901234567890") == "barcode"

    def test_barcode_13_digits_wrong_prefix(self):
        """13位但不是69开头 → 不是条码，走order_other（10-15位纯数字）"""
        assert _guess_code_type("1234567890123") == "order_other"

    def test_order_18_taobao(self):
        assert _guess_code_type("126036803257340376") == "order_18"

    def test_order_19_douyin(self):
        assert _guess_code_type("1234567890123456789") == "order_19"

    def test_order_16_jd(self):
        assert _guess_code_type("5759422420146938") == "order_16"

    def test_order_xhs(self):
        assert _guess_code_type("P123456789012345678") == "order_xhs"

    def test_order_pdd(self):
        assert _guess_code_type("260305-123456789") == "order_pdd"

    def test_product_alpha_numeric(self):
        assert _guess_code_type("DBTXL01-02") == "product"

    def test_product_short_numeric(self):
        """短纯数字不是订单号"""
        assert _guess_code_type("8001") == "product"

    def test_product_pure_alpha(self):
        assert _guess_code_type("ABCDEF") == "product"

    def test_order_other_12_digits(self):
        """12位纯数字 → order_other（京东等平台）"""
        assert _guess_code_type("345908383885") == "order_other"

    def test_order_other_10_digits(self):
        """10位纯数字 → order_other"""
        assert _guess_code_type("1234567890") == "order_other"

    def test_order_other_15_digits(self):
        """15位纯数字 → order_other"""
        assert _guess_code_type("123456789012345") == "order_other"

    def test_product_9_digits(self):
        """9位纯数字 → product（太短不算订单）"""
        assert _guess_code_type("123456789") == "product"

    def test_product_17_digits(self):
        """17位纯数字不匹配任何订单格式"""
        assert _guess_code_type("12345678901234567") == "product"

    def test_product_20_digits(self):
        """20位纯数字不匹配任何订单格式"""
        assert _guess_code_type("12345678901234567890") == "product"


# ── identify_code 输入校验 ──────────────────────


class TestInputValidation:

    @pytest.mark.asyncio
    async def test_empty_string(self):
        client = AsyncMock()
        result = await identify_code(client, "")
        assert "请提供有效编码" in result

    @pytest.mark.asyncio
    async def test_whitespace_only(self):
        client = AsyncMock()
        result = await identify_code(client, "   ")
        assert "请提供有效编码" in result

    @pytest.mark.asyncio
    async def test_comma_separated(self):
        client = AsyncMock()
        result = await identify_code(client, "ABC,DEF")
        assert "逐个识别" in result

    @pytest.mark.asyncio
    async def test_chinese_comma(self):
        client = AsyncMock()
        result = await identify_code(client, "ABC，DEF")
        assert "逐个识别" in result


# ── _identify_product 测试 ─────────────────────


class TestIdentifyProduct:

    @pytest.mark.asyncio
    async def test_main_code_normal(self):
        """主编码命中（普通商品）"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "title": "短袖T恤", "type": 0, "outerId": "DBTXL01",
            "sysItemId": 12345,
            "items": [
                {"skuOuterId": "DBTXL01-01", "sysSkuId": 67890,
                 "propertiesName": "白色 M"},
            ],
        })
        result = await _identify_product(client, "DBTXL01")
        assert "主编码(outer_id)" in result
        assert "普通(type=0)" in result
        assert "item_id=12345" in result
        assert "DBTXL01-01" in result

    @pytest.mark.asyncio
    async def test_main_code_suite(self):
        """主编码命中（套件）→ 含套件警告"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "title": "天竺棉套装", "type": 1, "outerId": "TJ-01",
            "sysItemId": 23456, "items": [],
        })
        result = await _identify_product(client, "TJ-01")
        assert "SKU套件(type=1)" in result
        assert "套件没有独立库存" in result

    @pytest.mark.asyncio
    async def test_sku_code_hit(self):
        """主编码未命中 → SKU编码命中"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(side_effect=[
            KuaiMaiBusinessError(message="not found", code="20110"),
            {"skuOuterId": "DBTXL01-02", "sysSkuId": 67891,
             "propertiesName": "黑色 XL", "outerId": "DBTXL01", "type": 0},
        ])
        result = await _identify_product(client, "DBTXL01-02")
        assert "SKU编码(sku_outer_id)" in result
        assert "sku_id=67891" in result
        assert "对应主编码: DBTXL01" in result

    @pytest.mark.asyncio
    async def test_not_found(self):
        """主编码 + SKU都未命中 → 未识别"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(side_effect=[
            KuaiMaiBusinessError(message="not found", code="20110"),
            KuaiMaiBusinessError(message="not found", code="20107"),
        ])
        result = await _identify_product(client, "XXXXXX")
        assert "未识别" in result
        assert "请确认编码拼写" in result

    @pytest.mark.asyncio
    async def test_api_exception_continues(self):
        """主编码API异常 → 跳过继续尝试SKU"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(side_effect=[
            Exception("timeout"),
            {"skuOuterId": "SKU01", "sysSkuId": 100,
             "propertiesName": "红色", "outerId": "MAIN01", "type": 0},
        ])
        result = await _identify_product(client, "SKU01")
        assert "SKU编码(sku_outer_id)" in result

    @pytest.mark.asyncio
    async def test_sku_api_empty_response(self):
        """SKU API返回空数据(success=true但无实际字段) → 未识别"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(side_effect=[
            KuaiMaiBusinessError(message="not found", code="20110"),
            {"success": True, "traceId": "123"},  # 无 sysSkuId
        ])
        result = await _identify_product(client, "NOTEXIST999")
        assert "未识别" in result
        assert "请确认编码拼写" in result

    @pytest.mark.asyncio
    async def test_main_code_nested_item_response(self):
        """item.single.get 返回嵌套结构 {"item": {...}}"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "traceId": "123",
            "item": {
                "title": "对白同学录", "type": 0, "outerId": "DBTXL01",
                "sysItemId": 720000905370112,
                "skus": [
                    {"skuOuterId": "DBTXL01-01", "sysSkuId": 720000905370113,
                     "propertiesName": "消失雨天"},
                ],
            },
        })
        result = await _identify_product(client, "DBTXL01")
        assert "主编码(outer_id)" in result
        assert "对白同学录" in result
        assert "item_id=720000905370112" in result
        assert "DBTXL01-01" in result


# ── _identify_order 测试 ──────────────────────


class TestIdentifyOrder:

    @pytest.mark.asyncio
    async def test_tid_found(self):
        """18位数字 → 用tid查到订单"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "list": [{
                "tid": "126036803257340376", "sid": "5759422420146938",
                "buyerNick": "张三", "sysStatus": "已发货", "source": "淘宝",
            }],
        })
        result = await _identify_order(client, "126036803257340376", "order_18")
        assert "平台订单号(order_id)" in result
        assert "system_id=5759422420146938" in result
        assert "淘宝" in result

    @pytest.mark.asyncio
    async def test_16_digit_sid_fallback(self):
        """16位数字 → tid无结果 → sid命中"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(side_effect=[
            {"list": []},  # tid无结果
            {"list": [{
                "tid": "126036803257340376", "sid": "5759422420146938",
                "buyerNick": "李四", "sysStatus": "待审核", "source": "",
            }]},
        ])
        result = await _identify_order(client, "5759422420146938", "order_16")
        assert "系统单号(system_id)" in result

    @pytest.mark.asyncio
    async def test_order_not_found(self):
        """订单未找到 → 返回 None（触发回退）"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={"list": []})
        result = await _identify_order(client, "126036803257340376", "order_18")
        assert result is None

    @pytest.mark.asyncio
    async def test_16_digit_both_miss(self):
        """16位 tid+sid 都未命中 → 返回 None"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={"list": []})
        result = await _identify_order(client, "5759422420146938", "order_16")
        assert result is None
        # 应该调用了4次（tid + sid + 归档tid + 归档sid）
        assert client.request_with_retry.call_count == 4

    @pytest.mark.asyncio
    async def test_order_other_tid_found(self):
        """order_other(12位) → 用tid查到订单"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "list": [{
                "tid": "345908383885", "sid": "5712100380273764",
                "buyerNick": "买家A", "sysStatus": "WAIT_AUDIT", "source": "jd",
            }],
        })
        result = await _identify_order(client, "345908383885", "order_other")
        assert "平台订单号(order_id)" in result
        assert "345908383885" in result

    @pytest.mark.asyncio
    async def test_order_other_not_found(self):
        """order_other 未命中 → 返回 None（只查tid，不查sid）"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={"list": []})
        result = await _identify_order(client, "345908383885", "order_other")
        assert result is None
        # order_other: tid近期 + tid归档 = 2次（不查sid）
        assert client.request_with_retry.call_count == 2

    @pytest.mark.asyncio
    async def test_api_exception_returns_none(self):
        """API异常 → 返回 None（不中断）"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(
            side_effect=Exception("network error"),
        )
        result = await _identify_order(client, "126036803257340376", "order_18")
        assert result is None


# ── _identify_barcode 测试 ────────────────────


class TestIdentifyBarcode:

    @pytest.mark.asyncio
    async def test_barcode_found(self):
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "list": [{"outerId": "MAIN01", "title": "测试商品"}],
        })
        result = await _identify_barcode(client, "6901234567890")
        assert "条码(barcode)" in result
        assert "outer_id=MAIN01" in result

    @pytest.mark.asyncio
    async def test_barcode_not_found(self):
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={"list": []})
        result = await _identify_barcode(client, "6901234567890")
        assert result is None

    @pytest.mark.asyncio
    async def test_barcode_api_error(self):
        client = AsyncMock()
        client.request_with_retry = AsyncMock(
            side_effect=Exception("timeout"),
        )
        result = await _identify_barcode(client, "6901234567890")
        assert result is None


# ── 回退机制集成测试 ─────────────────────────


class TestFallbackMechanism:

    @pytest.mark.asyncio
    async def test_order_miss_fallback_to_product(self):
        """16位纯数字 → 订单未命中 → 回退商品分支命中"""
        client = AsyncMock()
        call_count = 0

        async def mock_request(method, params, **kwargs):
            nonlocal call_count
            call_count += 1
            if method == "erp.trade.list.query":
                return {"list": []}
            if method == "item.single.get":
                return {
                    "title": "数字编码商品", "type": 0,
                    "outerId": "5759422420146938",
                    "sysItemId": 99999, "items": [],
                }
            raise Exception("unexpected call")

        client.request_with_retry = AsyncMock(side_effect=mock_request)
        result = await identify_code(client, "5759422420146938")
        assert "主编码(outer_id)" in result
        assert "数字编码商品" in result

    @pytest.mark.asyncio
    async def test_barcode_miss_fallback_to_product(self):
        """条码格式但multicode未命中 → 回退商品分支"""
        client = AsyncMock()

        async def mock_request(method, params, **kwargs):
            if method == "erp.item.multicode.query":
                return {"list": []}
            if method == "item.single.get":
                return {
                    "title": "条码商品", "type": 0,
                    "outerId": "6901234567890",
                    "sysItemId": 88888, "items": [],
                }
            raise Exception("unexpected call")

        client.request_with_retry = AsyncMock(side_effect=mock_request)
        result = await identify_code(client, "6901234567890")
        assert "主编码(outer_id)" in result

    @pytest.mark.asyncio
    async def test_product_direct_hit(self):
        """字母编码 → 直接进商品分支命中"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "title": "普通商品", "type": 0, "outerId": "ABC123",
            "sysItemId": 11111, "items": [],
        })
        result = await identify_code(client, "ABC123")
        assert "主编码(outer_id)" in result
        # 应该只调用1次（直接走商品分支）
        assert client.request_with_retry.call_count == 1

    @pytest.mark.asyncio
    async def test_order_other_miss_fallback_to_product(self):
        """12位纯数字 → 订单未命中 → 回退商品分支命中"""
        client = AsyncMock()

        async def mock_request(method, params, **kwargs):
            if method == "erp.trade.list.query":
                return {"list": []}
            if method == "item.single.get":
                return {
                    "title": "数字商品编码", "type": 0,
                    "outerId": "345908383885",
                    "sysItemId": 77777, "items": [],
                }
            raise Exception("unexpected call")

        client.request_with_retry = AsyncMock(side_effect=mock_request)
        result = await identify_code(client, "345908383885")
        assert "主编码(outer_id)" in result
        assert "数字商品编码" in result

    @pytest.mark.asyncio
    async def test_xhs_order_direct_hit(self):
        """P+18位 → 直接走订单分支（不回退）"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "list": [{
                "tid": "P123456789012345678", "sid": "1234567890123456",
                "buyerNick": "", "sysStatus": "待发货", "source": "小红书",
            }],
        })
        result = await identify_code(client, "P123456789012345678")
        assert "平台订单号(order_id)" in result
        assert "小红书" in result


# ── 新增字段测试：_format_product ────────────────


class TestFormatProductFields:

    @pytest.mark.asyncio
    async def test_suite_with_suit_single_list(self):
        """套件商品含 suitSingleList → 输出子单品列表"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "title": "天竺棉套装", "type": 1, "outerId": "TJ-01",
            "sysItemId": 23456, "items": [],
            "suitSingleList": [
                {"outerId": "DBXL01", "ratio": 1,
                 "skuOuterId": "DBXL01-01", "propertiesName": "白色M"},
                {"outerId": "DBTX02", "ratio": 2,
                 "skuOuterId": "DBTX02-01", "propertiesName": "黑色L"},
            ],
        })
        result = await _identify_product(client, "TJ-01")
        assert "套件子单品(2个)" in result
        assert "DBXL01(x1, sku=DBXL01-01, 白色M)" in result
        assert "DBTX02(x2, sku=DBTX02-01, 黑色L)" in result
        assert "stock_status(outer_id=子单品编码)" in result

    @pytest.mark.asyncio
    async def test_suite_empty_suit_single_list(self):
        """套件但 suitSingleList 为空 → 不输出子单品行"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "title": "空套件", "type": 2, "outerId": "EMPTY-01",
            "sysItemId": 34567, "items": [], "suitSingleList": [],
        })
        result = await _identify_product(client, "EMPTY-01")
        assert "纯套件(type=2)" in result
        assert "套件子单品" not in result

    @pytest.mark.asyncio
    async def test_product_stopped(self):
        """activeStatus=0 → 显示停用"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "title": "停用商品", "type": 0, "outerId": "STOP01",
            "sysItemId": 45678, "activeStatus": 0, "items": [],
        })
        result = await _identify_product(client, "STOP01")
        assert "状态: 停用" in result

    @pytest.mark.asyncio
    async def test_product_virtual(self):
        """isVirtual=1 → 显示虚拟商品"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "title": "虚拟卡", "type": 0, "outerId": "VIR01",
            "sysItemId": 56789, "isVirtual": 1, "items": [],
        })
        result = await _identify_product(client, "VIR01")
        assert "虚拟商品" in result

    @pytest.mark.asyncio
    async def test_product_with_barcode_price(self):
        """barcode/purchasePrice 非空 → 输出条码和采购价"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "title": "有码商品", "type": 0, "outerId": "BC01",
            "sysItemId": 67890, "barcode": "6901234567890",
            "purchasePrice": 25.5, "items": [],
        })
        result = await _identify_product(client, "BC01")
        assert "条码: 6901234567890" in result
        assert "采购价: ¥25.5" in result


# ── 新增字段测试：_format_sku ──────────────────


class TestFormatSkuFields:

    @pytest.mark.asyncio
    async def test_sku_item_outer_id(self):
        """itemOuterId 优先于 outerId 作为主编码"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(side_effect=[
            KuaiMaiBusinessError(message="not found", code="20110"),
            {"skuOuterId": "SKU-01", "sysSkuId": 100,
             "propertiesName": "红色", "outerId": "OLD-MAIN",
             "itemOuterId": "NEW-MAIN", "type": 0},
        ])
        result = await _identify_product(client, "SKU-01")
        assert "对应主编码: NEW-MAIN" in result
        assert "OLD-MAIN" not in result

    @pytest.mark.asyncio
    async def test_sku_suite_auto_fetch(self):
        """套件 SKU → 自动获取子单品列表"""
        client = AsyncMock()

        async def mock_request(method, params, **kwargs):
            if method == "item.single.get" and "outerId" in params:
                if params["outerId"] == "SKU-SUITE":
                    # 第一次查主编码 → 没有 sysItemId
                    return {"item": {}}
                # auto-fetch 子单品
                return {"item": {
                    "suitSingleList": [
                        {"outerId": "SUB01", "ratio": 1,
                         "skuOuterId": "SUB01-01", "propertiesName": "白M"},
                    ],
                }}
            if method == "erp.item.single.sku.get":
                return {"itemSku": [{
                    "skuOuterId": "SKU-SUITE", "sysSkuId": 200,
                    "propertiesName": "套装A", "outerId": "MAIN-SUITE",
                    "itemOuterId": "MAIN-SUITE", "type": 1,
                }]}
            raise Exception("unexpected")

        client.request_with_retry = AsyncMock(side_effect=mock_request)
        result = await _identify_product(client, "SKU-SUITE")
        assert "SKU编码(sku_outer_id)" in result
        assert "套件子单品(1个)" in result
        assert "SUB01(x1, sku=SUB01-01, 白M)" in result

    @pytest.mark.asyncio
    async def test_sku_suite_fetch_fail(self):
        """套件 SKU 自动获取失败 → 输出提示"""
        client = AsyncMock()

        async def mock_request(method, params, **kwargs):
            if method == "item.single.get":
                raise Exception("timeout")
            if method == "erp.item.single.sku.get":
                return {"itemSku": [{
                    "skuOuterId": "SKU-FAIL", "sysSkuId": 300,
                    "propertiesName": "套装B", "outerId": "MAIN-FAIL",
                    "type": 2,
                }]}
            raise Exception("unexpected")

        client.request_with_retry = AsyncMock(side_effect=mock_request)
        result = await _identify_product(client, "SKU-FAIL")
        assert "套件SKU，查子单品请用" in result
        assert "erp_identify(code=MAIN-FAIL)" in result

    @pytest.mark.asyncio
    async def test_sku_extras(self):
        """SKU 含 barcode/price/brand → 输出可选行"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(side_effect=[
            KuaiMaiBusinessError(message="not found", code="20110"),
            {"skuOuterId": "SKU-EX", "sysSkuId": 400,
             "propertiesName": "蓝L", "outerId": "MAIN-EX", "type": 0,
             "barcode": "6909999999999", "purchasePrice": 18.8,
             "brand": "对白"},
        ])
        result = await _identify_product(client, "SKU-EX")
        assert "条码: 6909999999999" in result
        assert "采购价: ¥18.8" in result
        assert "品牌: 对白" in result


# ── 新增字段测试：_format_order ──────────────────


class TestFormatOrderFields:

    @pytest.mark.asyncio
    async def test_order_with_sub_orders(self):
        """订单含 orders[] → 输出商品明细"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "list": [{
                "tid": "126036803257340376", "sid": "5759422420146938",
                "buyerNick": "张三", "sysStatus": "已发货", "source": "淘宝",
                "orders": [
                    {"sysOuterId": "DBTXL01-02", "sysTitle": "天竺棉T恤",
                     "num": 2},
                    {"sysOuterId": "ABC123", "sysTitle": "纯棉袜", "num": 1},
                ],
            }],
        })
        result = await _identify_order(client, "126036803257340376", "order_18")
        assert "商品(3件)" in result
        assert "DBTXL01-02 天竺棉T恤 x2" in result
        assert "ABC123 纯棉袜 x1" in result

    @pytest.mark.asyncio
    async def test_order_sub_orders_truncated(self):
        """>5 条子订单截断"""
        client = AsyncMock()
        orders = [
            {"sysOuterId": f"SKU{i}", "sysTitle": f"商品{i}", "num": 1}
            for i in range(7)
        ]
        client.request_with_retry = AsyncMock(return_value={
            "list": [{
                "tid": "126036803257340376", "sid": "5759422420146938",
                "buyerNick": "李四", "sysStatus": "已发货", "source": "淘宝",
                "orders": orders,
            }],
        })
        result = await _identify_order(client, "126036803257340376", "order_18")
        assert "等7件" in result
        # 只显示前5条
        assert "SKU0" in result
        assert "SKU4" in result
        assert "SKU5" not in result

    @pytest.mark.asyncio
    async def test_order_with_pay_amount(self):
        """payAmount → 显示实付"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "list": [{
                "tid": "126036803257340376", "sid": "5759422420146938",
                "buyerNick": "王五", "sysStatus": "已发货", "source": "淘宝",
                "payAmount": "128.00",
            }],
        })
        result = await _identify_order(client, "126036803257340376", "order_18")
        assert "实付: ¥128.00" in result

    @pytest.mark.asyncio
    async def test_order_with_logistics_memo(self):
        """shopName/outSid/sellerMemo/buyerMessage → 输出"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "list": [{
                "tid": "126036803257340376", "sid": "5759422420146938",
                "buyerNick": "赵六", "sysStatus": "已发货", "source": "淘宝",
                "shopName": "对白旗舰店", "warehouseName": "主仓",
                "outSid": "SF1234567890",
                "sellerMemo": "加急发", "buyerMessage": "请包装好",
            }],
        })
        result = await _identify_order(client, "126036803257340376", "order_18")
        assert "店铺: 对白旗舰店" in result
        assert "仓库: 主仓" in result
        assert "快递单号: SF1234567890" in result
        assert "卖家备注: 加急发" in result
        assert "买家留言: 请包装好" in result

    @pytest.mark.asyncio
    async def test_order_has_suit(self):
        """hasSuit=1 → 商品行含(含套件)"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "list": [{
                "tid": "126036803257340376", "sid": "5759422420146938",
                "buyerNick": "孙七", "sysStatus": "已发货", "source": "淘宝",
                "hasSuit": 1,
                "orders": [
                    {"sysOuterId": "TJ-01", "sysTitle": "套装", "num": 1},
                ],
            }],
        })
        result = await _identify_order(client, "126036803257340376", "order_18")
        assert "(含套件)" in result


# ── 新增字段测试：_identify_barcode ───────────────


class TestBarcodeFields:

    @pytest.mark.asyncio
    async def test_barcode_with_sku_info(self):
        """skuOuterId/propertiesName → 商品行含 SKU 信息"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "list": [{
                "outerId": "MAIN01", "title": "测试商品",
                "skuOuterId": "SKU01", "propertiesName": "白色M",
            }],
        })
        result = await _identify_barcode(client, "6901234567890")
        assert "sku_outer_id=SKU01" in result
        assert "规格: 白色M" in result

    @pytest.mark.asyncio
    async def test_barcode_with_system_ids(self):
        """sysItemId/sysSkuId → 系统 ID 行"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "list": [{
                "outerId": "MAIN01", "title": "测试商品",
                "sysItemId": 12345, "sysSkuId": 67890,
            }],
        })
        result = await _identify_barcode(client, "6901234567890")
        assert "item_id=12345" in result
        assert "sku_id=67890" in result

    @pytest.mark.asyncio
    async def test_barcode_with_multi_codes(self):
        """multiCodes >1 个 → 显示关联编码"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "list": [{
                "outerId": "MAIN01", "title": "测试商品",
                "multiCodes": ["AT1", "AT2", "6901234567890"],
            }],
        })
        result = await _identify_barcode(client, "6901234567890")
        assert "关联编码(3个)" in result
        assert "AT1" in result
        assert "AT2" in result


# ── 辅助函数测试 ──────────────────────────────


class TestSuitSinglesHelper:

    def test_format_suit_singles(self):
        """_format_suit_singles 格式化正确"""
        items = [
            {"outerId": "A01", "ratio": 1, "skuOuterId": "A01-01",
             "propertiesName": "白M"},
            {"outerId": "B02", "ratio": 2},
        ]
        result = _format_suit_singles(items)
        assert "套件子单品(2个)" in result
        assert "A01(x1, sku=A01-01, 白M)" in result
        assert "B02(x2)" in result
        assert "stock_status" in result

    @pytest.mark.asyncio
    async def test_fetch_suit_singles_success(self):
        """_fetch_suit_singles 成功获取"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "item": {
                "suitSingleList": [
                    {"outerId": "SUB01", "ratio": 1},
                ],
            },
        })
        result = await _fetch_suit_singles(client, "MAIN-01")
        assert result is not None
        assert "套件子单品(1个)" in result

    @pytest.mark.asyncio
    async def test_fetch_suit_singles_empty(self):
        """_fetch_suit_singles 无子单品 → None"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(return_value={
            "item": {"suitSingleList": []},
        })
        result = await _fetch_suit_singles(client, "MAIN-01")
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_suit_singles_error(self):
        """_fetch_suit_singles API 异常 → None"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(
            side_effect=Exception("timeout"),
        )
        result = await _fetch_suit_singles(client, "MAIN-01")
        assert result is None
