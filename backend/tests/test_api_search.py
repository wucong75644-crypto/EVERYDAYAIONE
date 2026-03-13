"""
ERP API 搜索单元测试

覆盖：精确查询、关键词搜索、无匹配、最多5条
"""

from services.kuaimai.api_search import search_erp_api


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
        """搜索结果不超过5条"""
        result = search_erp_api("查询")
        # 计算结果条目数（以 "- " 开头的行）
        entries = [
            line for line in result.split("\n")
            if line.strip().startswith("- ")
        ]
        assert len(entries) <= 5

    def test_empty_query(self):
        """空查询"""
        result = search_erp_api("")
        assert "请输入" in result

    def test_keyword_stock(self):
        """搜索库存相关 API"""
        result = search_erp_api("库存")
        assert "stock" in result.lower() or "库存" in result
