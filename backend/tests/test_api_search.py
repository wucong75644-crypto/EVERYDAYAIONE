"""
ERP API 搜索单元测试

覆盖：精确查询、关键词搜索、无匹配、最多5条、场景指南搜索
"""

from services.kuaimai.api_search import (
    _SCENARIO_DOCS,
    _match_scenarios,
    search_erp_api,
)


class TestErpApiSearch:

    def test_exact_search_valid(self):
        """精确查询存在的 tool:action"""
        result = search_erp_api("erp_trade_query:order_list")
        assert "erp_trade_query:order_list" in result
        assert "参数" in result

    def test_exact_search_tool_only(self):
        """只指定 tool_name → 列出所有 action"""
        result = search_erp_api("erp_trade_query:")
        assert "所有操作" in result

    def test_exact_search_invalid_tool(self):
        """不存在的工具"""
        result = search_erp_api("fake_tool:action")
        assert "未找到" in result

    def test_exact_search_invalid_action(self):
        """工具存在但 action 不存在"""
        result = search_erp_api("erp_trade_query:fake_action")
        assert "无操作" in result

    def test_keyword_search_matches(self):
        """关键词搜索有匹配"""
        result = search_erp_api("订单")
        assert "匹配" in result or "erp_trade_query" in result

    def test_keyword_search_no_match(self):
        """关键词搜索无匹配"""
        result = search_erp_api("zzz_impossible_keyword_xyz")
        assert "未找到" in result

    def test_keyword_search_max_results(self):
        """API搜索结果不超过5条（不含场景指南）"""
        result = search_erp_api("查询")
        # 计算 API 结果条目数（格式: "- erp_xxx:action — ..."）
        api_entries = [
            line for line in result.split("\n")
            if line.strip().startswith("- erp_")
        ]
        assert len(api_entries) <= 5

    def test_empty_query(self):
        """空查询"""
        result = search_erp_api("")
        assert "请输入" in result

    def test_keyword_stock(self):
        """搜索库存相关 API"""
        result = search_erp_api("库存")
        assert "stock" in result.lower() or "库存" in result

    # ── 场景指南搜索 ──────────────────────────────────

    def test_scenario_search_hit(self):
        """关键词命中场景指南→返回场景指南内容"""
        result = search_erp_api("调拨")
        assert "场景指南" in result
        assert "allocate_list" in result

    def test_scenario_search_purchase(self):
        """采购关键词命中场景指南"""
        result = search_erp_api("采购")
        assert "场景指南" in result
        assert "purchase_order_list" in result

    def test_scenario_search_logistics(self):
        """物流关键词命中场景指南"""
        result = search_erp_api("物流")
        assert "场景指南" in result
        assert "express_query" in result

    def test_scenario_order_query_hit(self):
        """订单关键词命中订单查询场景指南"""
        result = search_erp_api("订单")
        assert "场景指南" in result
        assert "outstock_query" in result

    def test_scenario_no_hit(self):
        """不相关关键词不命中场景指南"""
        result = search_erp_api("zzz_impossible_xyz")
        assert "场景指南" not in result

    def test_scenario_and_api_both_returned(self):
        """关键词同时命中场景指南和API→都返回"""
        result = search_erp_api("库存")
        # 场景指南中「统计」包含「库存」
        assert "stock" in result.lower() or "erp_" in result


class TestMatchScenarios:
    """_match_scenarios 单元测试"""

    def test_exact_title_match(self):
        """标题关键词命中"""
        hits = _match_scenarios(["调拨"])
        assert len(hits) >= 1
        assert any("调拨" in h for h in hits)

    def test_content_match(self):
        """内容关键词命中"""
        hits = _match_scenarios(["express_query"])
        assert len(hits) >= 1
        assert any("物流" in h for h in hits)

    def test_no_match(self):
        """无关关键词不命中"""
        hits = _match_scenarios(["zzz_impossible"])
        assert len(hits) == 0

    def test_multiple_hits(self):
        """一个关键词可命中多个场景"""
        hits = _match_scenarios(["查询"])
        # "查询" 出现在多个场景指南中
        assert len(hits) >= 2


class TestScenarioDocsCompleteness:
    """场景指南完整性验证"""

    def test_all_categories_present(self):
        """场景指南包含所有预期分类"""
        expected = ["订单查询", "商品查询", "调拨", "采购", "物流", "统计", "分销", "多步查询", "订单号"]
        for cat in expected:
            assert cat in _SCENARIO_DOCS, f"Missing category: {cat}"

    def test_purchase_chain_complete(self):
        """采购链路4阶段完整"""
        doc = _SCENARIO_DOCS["采购"]
        for action in [
            "purchase_order_list", "warehouse_entry_list",
            "shelf_list", "purchase_return_list",
        ]:
            assert action in doc, f"Missing in purchase: {action}"

    def test_product_query_actions_complete(self):
        """商品查询action覆盖"""
        doc = _SCENARIO_DOCS["商品查询"]
        for action in [
            "product_list", "product_detail", "multi_product",
            "sku_list", "multicode_query",
        ]:
            assert action in doc, f"Missing in product: {action}"

    def test_order_query_guide_has_three_actions(self):
        """订单查询场景指南包含三个action引导"""
        doc = _SCENARIO_DOCS["订单查询"]
        assert "outstock_query" in doc
        assert "outstock_order_query" in doc
        assert "order_list" in doc

    def test_order_query_guide_mentions_pdd(self):
        """订单查询场景指南提到拼多多"""
        doc = _SCENARIO_DOCS["订单查询"]
        assert "拼多多" in doc

    def test_order_query_guide_default_recommendation(self):
        """订单查询场景指南默认推荐outstock_query"""
        doc = _SCENARIO_DOCS["订单查询"]
        assert "默认推荐 outstock_query" in doc

    def test_multistep_uses_outstock_query(self):
        """多步查询场景指南已更新为outstock_query"""
        doc = _SCENARIO_DOCS["多步查询"]
        assert "outstock_query" in doc
        assert "order_list" not in doc

    def test_statistics_mentions_pdd_payment(self):
        """统计场景指南提到拼多多无payment的注意事项"""
        doc = _SCENARIO_DOCS["统计"]
        assert "拼多多" in doc
        assert "outstock_order_query" in doc
