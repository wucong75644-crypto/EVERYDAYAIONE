"""
端到端模拟测试：套件商品查库存全链路

测试链路：
  Step 3: stock_status(outer_id="DBXL01") 查子单品库存
    → format_inventory_list → 正常渲染库存
  Step 4: stock_status(outer_id="不存在的编码") 空结果
    → 中性文案，不含"参数类型选错"
  Step 6: 路由提示词套件处理指引
  Step 7: format_product_detail 套件子单品渲染
  Step 8: 子订单缺货数量渲染
"""

from unittest.mock import AsyncMock, patch

import pytest

# ── 模拟 API 数据（贴近真实快麦响应） ────────────────────


# 套件主商品（item.single.get 响应）
MOCK_SUITE_PRODUCT = {
    "item": {
        "sysItemId": "100001",
        "title": "天竺棉套件-常规四件套",
        "outerId": "TJ-CCNNTXL01",
        "type": 1,  # SKU套件
        "activeStatus": 1,
        "barcode": "6901234567890",
        "purchasePrice": 85.0,
        "isVirtual": 0,
        "skus": [
            {"skuOuterId": "TJ-CCNNTXL01-01", "sysSkuId": "200001",
             "propertiesName": "白色 1.5m"},
            {"skuOuterId": "TJ-CCNNTXL01-02", "sysSkuId": "200002",
             "propertiesName": "灰色 1.8m"},
        ],
        "suitSingleList": [
            {"outerId": "DBXL01", "title": "天竺棉被套",
             "ratio": 1, "skuOuterId": "DBXL01-01",
             "propertiesName": "白色 1.5m"},
            {"outerId": "CDDL02", "title": "天竺棉床单",
             "ratio": 1, "skuOuterId": "CDDL02-01",
             "propertiesName": "白色 1.5m"},
            {"outerId": "ZTL03", "title": "天竺棉枕套",
             "ratio": 2, "skuOuterId": "ZTL03-01",
             "propertiesName": "白色"},
        ],
    }
}

# 套件 SKU（erp.item.single.sku.get 响应）
MOCK_SUITE_SKU = {
    "itemSku": [{
        "sysSkuId": "200001",
        "skuOuterId": "TJ-CCNNTXL01-01",
        "propertiesName": "白色 1.5m",
        "itemOuterId": "TJ-CCNNTXL01",
        "outerId": "TJ-CCNNTXL01",
        "type": 1,  # SKU套件
        "activeStatus": 1,
        "barcode": "6901234567891",
        "purchasePrice": 85.0,
        "brand": "天竺棉家纺",
    }]
}

# 子单品库存（stock.api.status.query 响应）
MOCK_STOCK_DBXL01 = {
    "stockStatusVoList": [
        {
            "title": "天竺棉被套", "mainOuterId": "DBXL01",
            "outerId": "DBXL01-01", "propertiesName": "白色 1.5m",
            "totalAvailableStockSum": 150, "sellableNum": 120,
            "totalLockStock": 30, "purchaseNum": 50,
            "stockStatus": 6, "wareHouseId": "WH001",
        },
    ],
    "total": 1,
}

MOCK_STOCK_CDDL02 = {
    "stockStatusVoList": [
        {
            "title": "天竺棉床单", "mainOuterId": "CDDL02",
            "outerId": "CDDL02-01", "propertiesName": "白色 1.5m",
            "totalAvailableStockSum": 80, "sellableNum": 60,
            "totalLockStock": 20, "purchaseNum": 30,
            "stockStatus": 0, "wareHouseId": "WH001",
        },
    ],
    "total": 1,
}

MOCK_STOCK_EMPTY = {"stockStatusVoList": [], "total": 0}


# ── 辅助：按 method+params 路由 mock 响应 ────────────────


def make_api_router():
    """构造 mock API 路由器，根据 method 和参数返回不同响应"""
    async def route(method, params=None, **kwargs):
        params = params or {}
        if method == "item.single.get":
            outer = params.get("outerId", "")
            if outer == "TJ-CCNNTXL01":
                return MOCK_SUITE_PRODUCT["item"]
            # 子单品查询（_fetch_suit_singles 会调用此 method）
            return {"sysItemId": None}

        if method == "erp.item.single.sku.get":
            sku_code = params.get("skuOuterId", "")
            if sku_code == "TJ-CCNNTXL01-01":
                return MOCK_SUITE_SKU
            return {}

        if method == "stock.api.status.query":
            outer = params.get("mainOuterId", "")
            if "DBXL01" in outer:
                return MOCK_STOCK_DBXL01
            if "CDDL02" in outer:
                return MOCK_STOCK_CDDL02
            return MOCK_STOCK_EMPTY

        return {}

    return route


# ============================================================
# 测试场景
# ============================================================


class TestE2ESuiteStockFlow:
    """端到端：套件商品查库存全链路模拟"""

    @pytest.fixture
    def mock_client(self):
        """构造 mock KuaiMaiClient"""
        client = AsyncMock()
        client.request_with_retry = AsyncMock(side_effect=make_api_router())
        return client

    # ── Step 3: 查子单品库存 ─────────────────────────

    @pytest.mark.asyncio
    async def test_step3_query_sub_item_stock(self):
        """stock_status(outer_id=DBXL01) → 正常渲染库存"""
        from services.kuaimai.formatters.product import format_inventory_list
        from services.kuaimai.registry.base import ApiEntry

        entry = ApiEntry(
            method="stock.api.status.query",
            description="库存状态查询",
            response_key="stockStatusVoList",
        )

        result = format_inventory_list(MOCK_STOCK_DBXL01, entry)
        print("\n" + "=" * 60)
        print("STEP 3: stock_status(outer_id='DBXL01') → 被套库存")
        print("=" * 60)
        print(result)

        assert "天竺棉被套" in result
        assert "DBXL01" in result
        assert "总库存: 150" in result
        assert "可售: 120" in result
        assert "锁定: 30" in result
        assert "采购在途: 50" in result
        assert "有货" in result  # stockStatus=6
        assert "参数类型选错" not in result

    @pytest.mark.asyncio
    async def test_step3b_query_second_sub_item(self):
        """stock_status(outer_id=CDDL02) → 第二个子单品"""
        from services.kuaimai.formatters.product import format_inventory_list
        from services.kuaimai.registry.base import ApiEntry

        entry = ApiEntry(
            method="stock.api.status.query",
            description="库存状态查询",
            response_key="stockStatusVoList",
        )

        result = format_inventory_list(MOCK_STOCK_CDDL02, entry)
        print("\n" + "=" * 60)
        print("STEP 3b: stock_status(outer_id='CDDL02') → 床单库存")
        print("=" * 60)
        print(result)

        assert "天竺棉床单" in result
        assert "CDDL02" in result
        assert "可售: 60" in result
        assert "正常" in result  # stockStatus=0

    # ── Step 4: 空结果中性文案 ──────────────────────

    @pytest.mark.asyncio
    async def test_step4_empty_stock_neutral_message(self):
        """空库存结果不含误导文案"""
        from services.kuaimai.formatters.product import format_inventory_list
        from services.kuaimai.registry.base import ApiEntry

        entry = ApiEntry(
            method="stock.api.status.query",
            description="库存状态查询",
            response_key="stockStatusVoList",
        )

        result = format_inventory_list(MOCK_STOCK_EMPTY, entry)
        print("\n" + "=" * 60)
        print("STEP 4: stock_status(outer_id='不存在') → 空结果")
        print("=" * 60)
        print(result)

        assert "0 条" in result
        # 关键：不含误导 AI 重试的文案
        assert "参数类型选错" not in result
        assert "outer_id/sku_outer_id 混用" not in result

    # ── Step 6: 路由提示词可执行性验证 ───────────────

    def test_step6_routing_prompt_actionable(self):
        """路由提示词包含可执行的套件处理指引"""
        from config.erp_tools import ERP_ROUTING_PROMPT

        print("\n" + "=" * 60)
        print("STEP 6: 路由提示词套件处理指引")
        print("=" * 60)

        # 套件无独立库存，需查子单品
        assert "套件" in ERP_ROUTING_PROMPT
        assert "local_product_identify" in ERP_ROUTING_PROMPT
        assert "两步查询" in ERP_ROUTING_PROMPT

        # 打印相关段落
        for line in ERP_ROUTING_PROMPT.split("\n"):
            if "套件" in line or "suit" in line.lower():
                print(f"  {line.strip()}")

    # ── Step 7: Formatter 套件子单品渲染 ─────────────

    def test_step7_formatter_renders_suit_singles(self):
        """format_product_detail 正确渲染套件子单品"""
        from services.kuaimai.formatters.product import format_product_detail

        data = MOCK_SUITE_PRODUCT["item"]
        result = format_product_detail(data, None)
        print("\n" + "=" * 60)
        print("STEP 7: format_product_detail 套件子单品渲染")
        print("=" * 60)
        print(result)

        assert "天竺棉套件-常规四件套" in result
        assert "套件子单品" in result
        assert "3个" in result
        assert "DBXL01" in result
        assert "天竺棉被套" in result
        assert "x1" in result
        assert "sku=DBXL01-01" in result
        assert "CDDL02" in result
        assert "ZTL03" in result
        assert "x2" in result  # 枕套 ratio=2

    # ── Step 8: 子订单缺货数量渲染 ──────────────────

    def test_step8_sub_order_diff_stock_num(self):
        """子订单缺货数量正确渲染"""
        from services.kuaimai.formatters.trade import format_order_list

        data = {
            "list": [{
                "tid": "T20260319001", "sid": "S12345",
                "sysStatus": "缺货中", "buyerNick": "张三",
                "payment": 256.0,
                "orders": [
                    {"sysTitle": "天竺棉被套", "sysOuterId": "DBXL01-01",
                     "num": 2, "diffStockNum": 1, "price": 128.0},
                ],
            }],
            "total": 1,
        }
        result = format_order_list(data, None)
        print("\n" + "=" * 60)
        print("STEP 8: 子订单缺货数量渲染")
        print("=" * 60)
        print(result)

        assert "天竺棉被套" in result
        assert "缺货数量: 1" in result
