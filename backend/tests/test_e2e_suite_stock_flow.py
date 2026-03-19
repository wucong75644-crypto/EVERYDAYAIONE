"""
端到端模拟测试：套件商品查库存全链路

复现场景：用户问"TJ-CCNNTXL01-01 库存多少？"
修复前：AI 重试 7 次烧掉 100K tokens（code_identifier 返回信息不足）
修复后：一次 identify 即返回子单品列表 + 可执行指引

测试链路：
  Step 1: erp_identify(code="TJ-CCNNTXL01-01")
    → code_identifier.identify_code → _identify_product
    → 主编码命中 → _format_product → 含 suitSingleList + 指引
  Step 2: erp_identify(code="TJ-CCNNTXL01-01-SKU01")
    → SKU编码命中 → _format_sku → 自动 _fetch_suit_singles → 含子单品
  Step 3: stock_status(outer_id="DBXL01") 查子单品库存
    → format_inventory_list → 正常渲染库存
  Step 4: stock_status(outer_id="不存在的编码") 空结果
    → 中性文案，不含"参数类型选错"
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

    # ── Step 1: 主编码识别套件商品 ────────────────────

    @pytest.mark.asyncio
    async def test_step1_identify_suite_main_code(self, mock_client):
        """主编码 TJ-CCNNTXL01 → 识别为套件 + 返回子单品列表"""
        from services.kuaimai.code_identifier import identify_code

        result = await identify_code(mock_client, "TJ-CCNNTXL01")
        print("\n" + "=" * 60)
        print("STEP 1: erp_identify(code='TJ-CCNNTXL01')")
        print("=" * 60)
        print(result)

        # 基础识别
        assert "✓ 商品存在" in result
        assert "主编码(outer_id)" in result
        assert "SKU套件" in result
        assert "套件没有独立库存" in result

        # 子单品列表
        assert "套件子单品(3个)" in result
        assert "DBXL01" in result
        assert "天竺棉被套" in result or "DBXL01" in result
        assert "CDDL02" in result
        assert "ZTL03" in result

        # 可执行指引
        assert "stock_status" in result
        assert "outer_id=子单品编码" in result

        # SKU 列表
        assert "TJ-CCNNTXL01-01" in result
        assert "白色 1.5m" in result

    # ── Step 2: SKU编码识别 → 自动获取子单品 ──────────

    @pytest.mark.asyncio
    async def test_step2_identify_suite_sku_code(self, mock_client):
        """SKU编码 TJ-CCNNTXL01-01 → 识别为套件SKU + 自动获取子单品"""
        from services.kuaimai.code_identifier import identify_code

        # _fetch_suit_singles 会调用 item.single.get(outerId=TJ-CCNNTXL01)
        # mock_client 已路由正确响应
        result = await identify_code(mock_client, "TJ-CCNNTXL01-01")
        print("\n" + "=" * 60)
        print("STEP 2: erp_identify(code='TJ-CCNNTXL01-01')")
        print("=" * 60)
        print(result)

        # SKU 识别
        assert "✓ 商品存在" in result
        assert "SKU编码(sku_outer_id)" in result
        assert "对应主编码: TJ-CCNNTXL01" in result
        assert "白色 1.5m" in result

        # itemOuterId 优先级（不是 outerId）
        assert "TJ-CCNNTXL01" in result

        # 自动获取的子单品列表
        assert "套件子单品" in result
        assert "DBXL01" in result
        assert "CDDL02" in result
        assert "ZTL03" in result
        assert "stock_status" in result

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

    # ── Step 5: 套件 SKU 自动获取失败时的降级 ────────

    @pytest.mark.asyncio
    async def test_step5_suite_sku_fetch_fail_graceful(self):
        """_fetch_suit_singles 失败时降级输出提示"""
        from services.kuaimai.code_identifier import identify_code

        client = AsyncMock()

        async def route_with_fail(method, params=None, **kwargs):
            params = params or {}
            if method == "erp.item.single.sku.get":
                return MOCK_SUITE_SKU
            if method == "item.single.get":
                # 模拟获取子单品失败（API 超时）
                raise TimeoutError("API timeout")
            return {}

        client.request_with_retry = AsyncMock(side_effect=route_with_fail)

        result = await identify_code(client, "TJ-CCNNTXL01-01")
        print("\n" + "=" * 60)
        print("STEP 5: SKU识别成功 but _fetch_suit_singles 失败")
        print("=" * 60)
        print(result)

        # SKU 识别本身成功
        assert "✓ 商品存在" in result
        assert "SKU编码(sku_outer_id)" in result

        # 降级提示（引导用户手动查主编码）
        assert "套件SKU" in result
        assert "erp_identify(code=TJ-CCNNTXL01)" in result

    # ── Step 6: 路由提示词可执行性验证 ───────────────

    def test_step6_routing_prompt_actionable(self):
        """路由提示词包含可执行的套件处理指引"""
        from config.erp_tools import ERP_ROUTING_PROMPT

        print("\n" + "=" * 60)
        print("STEP 6: 路由提示词套件处理指引")
        print("=" * 60)

        # 关键：不再是不可执行的"告知用户需查子单品"
        assert "erp_identify 会返回子单品列表" in ERP_ROUTING_PROMPT
        assert "stock_status(outer_id=子单品编码)" in ERP_ROUTING_PROMPT
        assert "汇总后告知用户" in ERP_ROUTING_PROMPT

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
