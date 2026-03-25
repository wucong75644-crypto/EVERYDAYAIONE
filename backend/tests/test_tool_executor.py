"""
ToolExecutor 单元测试

覆盖：execute 分发、web_search、get_conversation_context、
      search_knowledge、ERP 工具（query_erp_*）

注意：tool_executor.py 使用函数内延迟导入，patch 路径需指向原始模块。
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.tool_executor import ToolExecutor


# ============================================================
# Helpers
# ============================================================


def _make_executor() -> ToolExecutor:
    return ToolExecutor(db=MagicMock(), user_id="u1", conversation_id="c1")


# ============================================================
# TestExecuteDispatch
# ============================================================


class TestExecuteDispatch:

    @pytest.mark.asyncio
    async def test_unknown_tool_raises(self):
        """未知工具→ValueError"""
        exe = _make_executor()
        with pytest.raises(ValueError, match="Unknown sync tool"):
            await exe.execute("not_exist", {})

    @pytest.mark.asyncio
    async def test_dispatches_to_handler(self):
        """已注册工具→调用对应 handler"""
        exe = _make_executor()
        exe._handlers["web_search"] = AsyncMock(return_value="result")
        result = await exe.execute("web_search", {"search_query": "test"})
        assert result == "result"


# ============================================================
# TestWebSearch
# ============================================================


# ============================================================
# TestGetConversationContext
# ============================================================


class TestGetConversationContext:

    @pytest.mark.asyncio
    @patch("services.message_service.MessageService")
    async def test_empty_messages(self, MockService):
        """无消息→返回提示"""
        mock_svc = AsyncMock()
        mock_svc.get_messages.return_value = {"messages": []}
        MockService.return_value = mock_svc

        exe = _make_executor()
        result = await exe._get_conversation_context({"limit": 10})
        assert "暂无历史消息" in result

    @pytest.mark.asyncio
    @patch("services.message_service.MessageService")
    async def test_text_messages_formatted(self, MockService):
        """文本消息→格式化为 [role] text"""
        mock_svc = AsyncMock()
        mock_svc.get_messages.return_value = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "你好"}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "你好！"}],
                },
            ],
        }
        MockService.return_value = mock_svc

        exe = _make_executor()
        result = await exe._get_conversation_context({"limit": 10})
        assert "[user]" in result
        assert "[assistant]" in result
        assert "你好" in result

    @pytest.mark.asyncio
    @patch("services.message_service.MessageService")
    async def test_messages_with_images(self, MockService):
        """含图片消息→格式化包含图片 URL"""
        mock_svc = AsyncMock()
        mock_svc.get_messages.return_value = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看这张图"},
                        {"type": "image", "url": "https://img.com/a.jpg"},
                    ],
                },
            ],
        }
        MockService.return_value = mock_svc

        exe = _make_executor()
        result = await exe._get_conversation_context({})
        assert "img.com/a.jpg" in result
        assert "[图片:" in result

    @pytest.mark.asyncio
    @patch("services.message_service.MessageService")
    async def test_limit_capped_at_20(self, MockService):
        """limit 超过 20→cap 到 20"""
        mock_svc = AsyncMock()
        mock_svc.get_messages.return_value = {"messages": []}
        MockService.return_value = mock_svc

        exe = _make_executor()
        await exe._get_conversation_context({"limit": 50})
        call_kwargs = mock_svc.get_messages.call_args
        assert call_kwargs.kwargs.get("limit") == 20

    @pytest.mark.asyncio
    @patch("services.message_service.MessageService")
    async def test_default_limit_is_10(self, MockService):
        """无 limit 参数→默认 10"""
        mock_svc = AsyncMock()
        mock_svc.get_messages.return_value = {"messages": []}
        MockService.return_value = mock_svc

        exe = _make_executor()
        await exe._get_conversation_context({})
        call_kwargs = mock_svc.get_messages.call_args
        assert call_kwargs.kwargs.get("limit") == 10

    @pytest.mark.asyncio
    @patch("services.message_service.MessageService")
    async def test_image_without_url_skipped(self, MockService):
        """image 无 url→不出现在结果中"""
        mock_svc = AsyncMock()
        mock_svc.get_messages.return_value = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "test"},
                        {"type": "image", "url": ""},
                    ],
                },
            ],
        }
        MockService.return_value = mock_svc

        exe = _make_executor()
        result = await exe._get_conversation_context({})
        assert "[图片:" not in result


# ============================================================
# TestSearchKnowledge
# ============================================================


class TestSearchKnowledge:

    @pytest.mark.asyncio
    async def test_empty_query(self):
        """空 query→返回提示"""
        exe = _make_executor()
        result = await exe._search_knowledge({"query": ""})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_missing_query(self):
        """无 query 键→返回提示"""
        exe = _make_executor()
        result = await exe._search_knowledge({})
        assert "不能为空" in result

    @pytest.mark.asyncio
    @patch(
        "services.knowledge_service.search_relevant",
        new_callable=AsyncMock,
    )
    async def test_search_with_results(self, mock_search):
        """有结果→格式化返回"""
        mock_search.return_value = [
            {"title": "模型A", "content": "表现很好"},
            {"title": "模型B", "content": "偶尔超时"},
        ]
        exe = _make_executor()
        result = await exe._search_knowledge({"query": "模型表现"})
        assert "模型A" in result
        assert "表现很好" in result
        assert "模型B" in result

    @pytest.mark.asyncio
    @patch(
        "services.knowledge_service.search_relevant",
        new_callable=AsyncMock,
    )
    async def test_search_no_results(self, mock_search):
        """无结果→返回未找到提示"""
        mock_search.return_value = []
        exe = _make_executor()
        result = await exe._search_knowledge({"query": "不存在"})
        assert "未找到" in result


# ============================================================
# TestERPTools — ERP 工具统一测试
# ============================================================


class TestERPTools:

    @pytest.mark.asyncio
    async def test_step1_returns_param_doc(self):
        """两步调用 Step 1：无 params 时返回参数文档（纯本地）"""
        exe = _make_executor()
        result = await exe._erp_dispatch(
            "erp_trade_query", {"action": "order_list"},
        )
        assert "order_list" in result
        assert "参数" in result
        assert "order_id" in result

    @pytest.mark.asyncio
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_erp_not_configured(self, MockClient):
        """ERP 未配置→返回友好提示（Step 2 带 params 时）"""
        mock_client = AsyncMock()
        mock_client.is_configured = False
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        exe = _make_executor()
        result = await exe._erp_dispatch(
            "erp_trade_query",
            {"action": "order_list", "params": {"order_id": "123"}},
        )
        assert "未配置" in result

    @pytest.mark.asyncio
    @patch("services.kuaimai.dispatcher.ErpDispatcher")
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_trade_query_success(self, MockClient, MockDispatcher):
        """交易查询成功→返回结果（两步调用 Step 2）"""
        mock_client = AsyncMock()
        mock_client.is_configured = True
        mock_client.load_cached_token = AsyncMock()
        MockClient.return_value = mock_client

        mock_disp = AsyncMock()
        mock_disp.execute.return_value = "订单数据"
        mock_disp.close = AsyncMock()
        MockDispatcher.return_value = mock_disp

        exe = _make_executor()
        result = await exe._erp_dispatch("erp_trade_query", {
            "action": "order_list",
            "params": {
                "start_date": "2026-03-01",
                "end_date": "2026-03-10",
            },
        })
        assert result == "订单数据"
        mock_disp.execute.assert_called_once_with(
            "erp_trade_query", "order_list",
            {"start_date": "2026-03-01", "end_date": "2026-03-10"},
        )

    @pytest.mark.asyncio
    @patch("services.kuaimai.dispatcher.ErpDispatcher")
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_trade_query_exception(self, MockClient, MockDispatcher):
        """交易查询异常→返回错误提示"""
        mock_client = AsyncMock()
        mock_client.is_configured = True
        mock_client.load_cached_token = AsyncMock()
        MockClient.return_value = mock_client

        mock_disp = AsyncMock()
        mock_disp.execute.side_effect = Exception("API timeout")
        mock_disp.close = AsyncMock()
        MockDispatcher.return_value = mock_disp

        exe = _make_executor()
        result = await exe._erp_dispatch(
            "erp_trade_query",
            {"action": "order_list", "params": {"order_id": "123"}},
        )
        assert "失败" in result

    @pytest.mark.asyncio
    @patch("services.kuaimai.dispatcher.ErpDispatcher")
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_product_query_success(self, MockClient, MockDispatcher):
        """商品查询成功→返回结果"""
        mock_client = AsyncMock()
        mock_client.is_configured = True
        mock_client.load_cached_token = AsyncMock()
        MockClient.return_value = mock_client

        mock_disp = AsyncMock()
        mock_disp.execute.return_value = "商品列表"
        mock_disp.close = AsyncMock()
        MockDispatcher.return_value = mock_disp

        exe = _make_executor()
        result = await exe._erp_dispatch("erp_product_query", {
            "action": "product_list",
            "params": {"keyword": "手机壳"},
            "page": 1,
        })
        assert result == "商品列表"

    @pytest.mark.asyncio
    @patch("services.kuaimai.dispatcher.ErpDispatcher")
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_product_query_exception(self, MockClient, MockDispatcher):
        """商品查询异常→返回错误提示"""
        mock_client = AsyncMock()
        mock_client.is_configured = True
        mock_client.load_cached_token = AsyncMock()
        MockClient.return_value = mock_client

        mock_disp = AsyncMock()
        mock_disp.execute.side_effect = Exception("网络异常")
        mock_disp.close = AsyncMock()
        MockDispatcher.return_value = mock_disp

        exe = _make_executor()
        result = await exe._erp_dispatch(
            "erp_product_query",
            {"action": "product_list", "params": {"keyword": "测试"}},
        )
        assert "失败" in result

    @pytest.mark.asyncio
    @patch("services.kuaimai.dispatcher.ErpDispatcher")
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_product_stock_query(self, MockClient, MockDispatcher):
        """库存查询走 erp_product_query（两步调用 Step 2）"""
        mock_client = AsyncMock()
        mock_client.is_configured = True
        mock_client.load_cached_token = AsyncMock()
        MockClient.return_value = mock_client

        mock_disp = AsyncMock()
        mock_disp.execute.return_value = "库存数据"
        mock_disp.close = AsyncMock()
        MockDispatcher.return_value = mock_disp

        exe = _make_executor()
        result = await exe._erp_dispatch("erp_product_query", {
            "action": "stock_status",
            "params": {"outer_id": "SKU001"},
        })
        assert result == "库存数据"
        mock_disp.execute.assert_called_once_with(
            "erp_product_query", "stock_status", {"outer_id": "SKU001"},
        )

    @pytest.mark.asyncio
    @patch("services.kuaimai.dispatcher.ErpDispatcher")
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_missing_action(self, MockClient, MockDispatcher):
        """缺少 action 参数→返回提示"""
        mock_client = AsyncMock()
        mock_client.is_configured = True
        mock_client.load_cached_token = AsyncMock()
        MockClient.return_value = mock_client

        mock_disp = AsyncMock()
        mock_disp.close = AsyncMock()
        MockDispatcher.return_value = mock_disp

        exe = _make_executor()
        result = await exe._erp_dispatch("erp_trade_query", {})
        assert "action" in result

    @pytest.mark.asyncio
    @patch("services.kuaimai.dispatcher.ErpDispatcher")
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_erp_execute_write(self, MockClient, MockDispatcher):
        """erp_execute 写操作→通过 category 路由"""
        mock_client = AsyncMock()
        mock_client.is_configured = True
        mock_client.load_cached_token = AsyncMock()
        MockClient.return_value = mock_client

        mock_disp = AsyncMock()
        mock_disp.execute.return_value = "操作成功"
        mock_disp.close = AsyncMock()
        MockDispatcher.return_value = mock_disp

        exe = _make_executor()
        result = await exe._erp_dispatch("erp_execute", {
            "category": "trade",
            "action": "order_cancel",
            "params": {"order_id": "T123"},
        })
        assert result == "操作成功"
        mock_disp.execute.assert_called_once_with(
            "erp_trade_query", "order_cancel", {"order_id": "T123"},
        )

    @pytest.mark.asyncio
    @patch("services.kuaimai.dispatcher.ErpDispatcher")
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_handler_registered_for_all_erp_tools(
        self, MockClient, MockDispatcher,
    ):
        """所有7个ERP工具都已注册handler"""
        exe = _make_executor()
        expected = {
            "erp_info_query", "erp_product_query", "erp_trade_query",
            "erp_aftersales_query", "erp_warehouse_query",
            "erp_purchase_query", "erp_execute",
        }
        for tool in expected:
            assert tool in exe._handlers, f"Missing handler: {tool}"

    @pytest.mark.asyncio
    @patch("services.kuaimai.dispatcher.ErpDispatcher")
    @patch("services.kuaimai.client.KuaiMaiClient")
    async def test_execute_delegates_to_erp_dispatch(
        self, MockClient, MockDispatcher,
    ):
        """execute() 委托 erp_trade_query 到 _erp_dispatch（Step 2 带 params）"""
        mock_client = AsyncMock()
        mock_client.is_configured = True
        mock_client.load_cached_token = AsyncMock()
        MockClient.return_value = mock_client

        mock_disp = AsyncMock()
        mock_disp.execute.return_value = "物流信息"
        mock_disp.close = AsyncMock()
        MockDispatcher.return_value = mock_disp

        exe = _make_executor()
        result = await exe.execute(
            "erp_trade_query",
            {"action": "outstock_query", "params": {"order_id": "123"}},
        )
        assert result == "物流信息"


# ============================================================
# TestSocialCrawler — 社交媒体爬虫工具
# ============================================================


class TestSocialCrawler:

    @pytest.mark.asyncio
    @patch("core.config.get_settings")
    async def test_crawler_disabled(self, mock_settings):
        """爬虫未启用→返回提示"""
        mock_settings.return_value = MagicMock(crawler_enabled=False)
        exe = _make_executor()
        result = await exe._social_crawler({"platform": "xhs", "keywords": "测试"})
        assert "未启用" in result

    @pytest.mark.asyncio
    @patch("services.crawler.service.CrawlerService")
    @patch("core.config.get_settings")
    async def test_crawler_not_installed(self, mock_settings, MockService):
        """爬虫未安装→返回安装提示"""
        mock_settings.return_value = MagicMock(crawler_enabled=True)
        mock_svc = MagicMock()
        mock_svc.is_available.return_value = False
        MockService.return_value = mock_svc

        exe = _make_executor()
        result = await exe._social_crawler({"platform": "xhs", "keywords": "测试"})
        assert "未安装" in result

    @pytest.mark.asyncio
    @patch("services.crawler.service.CrawlerService")
    @patch("core.config.get_settings")
    async def test_empty_keywords(self, mock_settings, MockService):
        """空关键词→返回错误"""
        mock_settings.return_value = MagicMock(crawler_enabled=True)
        mock_svc = MagicMock()
        mock_svc.is_available.return_value = True
        MockService.return_value = mock_svc

        exe = _make_executor()
        result = await exe._social_crawler({"platform": "xhs", "keywords": ""})
        assert "不能为空" in result

    @pytest.mark.asyncio
    @patch("services.crawler.service.CrawlerService")
    @patch("core.config.get_settings")
    async def test_crawl_error(self, mock_settings, MockService):
        """爬取失败→返回错误信息"""
        mock_settings.return_value = MagicMock(crawler_enabled=True)

        from services.crawler.models import CrawlResult
        mock_svc = MagicMock()
        mock_svc.is_available.return_value = True
        mock_svc.execute = AsyncMock(
            return_value=CrawlResult(platform="xhs", error="超时"),
        )
        MockService.return_value = mock_svc

        exe = _make_executor()
        result = await exe._social_crawler({"platform": "xhs", "keywords": "测试"})
        assert "爬取失败" in result

    @pytest.mark.asyncio
    @patch("services.crawler.service.CrawlerService")
    @patch("core.config.get_settings")
    async def test_successful_crawl(self, mock_settings, MockService):
        """正常爬取→返回格式化结果"""
        mock_settings.return_value = MagicMock(crawler_enabled=True)

        from services.crawler.models import CrawlItem, CrawlResult
        items = [CrawlItem(platform="xhs", title="好物推荐")]
        mock_svc = MagicMock()
        mock_svc.is_available.return_value = True
        mock_svc.execute = AsyncMock(
            return_value=CrawlResult(platform="xhs", items=items, total_found=1),
        )
        mock_svc.format_for_brain.return_value = "从小红书找到 1 条结果"
        MockService.return_value = mock_svc

        exe = _make_executor()
        result = await exe._social_crawler({"platform": "xhs", "keywords": "防晒霜"})
        assert "小红书" in result
        mock_svc.format_for_brain.assert_called_once_with(items)

    @pytest.mark.asyncio
    @patch("services.crawler.service.CrawlerService")
    @patch("core.config.get_settings")
    async def test_max_results_capped(self, mock_settings, MockService):
        """max_results 上限 30"""
        mock_settings.return_value = MagicMock(crawler_enabled=True)

        from services.crawler.models import CrawlResult
        mock_svc = MagicMock()
        mock_svc.is_available.return_value = True
        mock_svc.execute = AsyncMock(
            return_value=CrawlResult(platform="xhs"),
        )
        mock_svc.format_for_brain.return_value = "无结果"
        MockService.return_value = mock_svc

        exe = _make_executor()
        await exe._social_crawler({
            "platform": "xhs", "keywords": "test", "max_results": 100,
        })
        call_kwargs = mock_svc.execute.call_args.kwargs
        assert call_kwargs["max_notes"] == 30


# ============================================================
# TestSearchHandlers — 搜索工具 handler
# ============================================================


class TestSearchHandlers:

    @pytest.mark.asyncio
    async def test_erp_api_search_calls_search(self):
        """_erp_api_search 调用 search_erp_api 并返回结果"""
        exe = _make_executor()
        with patch(
            "services.kuaimai.api_search.search_erp_api",
            return_value="找到 3 个匹配",
        ):
            result = await exe._erp_api_search({"query": "订单"})
        assert "匹配" in result

    @pytest.mark.asyncio
    async def test_erp_api_search_empty_query(self):
        """_erp_api_search 空查询→提示输入"""
        exe = _make_executor()
        result = await exe._erp_api_search({"query": ""})
        assert "请输入" in result

    @pytest.mark.asyncio
    async def test_execute_routes_to_erp_api_search(self):
        """execute 分发 erp_api_search 到正确 handler"""
        exe = _make_executor()
        with patch(
            "services.kuaimai.api_search.search_erp_api",
            return_value="结果",
        ):
            result = await exe.execute("erp_api_search", {"query": "库存"})
        assert result == "结果"

