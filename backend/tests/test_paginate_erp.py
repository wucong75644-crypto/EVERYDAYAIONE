"""
paginate_erp 翻页引擎测试

从 test_sandbox_functions.py 迁移，测试 tool_executor.py 的 paginate_erp 函数。
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from services.agent.erp_pagination import extract_list, paginate_erp


class TestPaginateErp:
    """paginate_erp 全量翻页测试"""

    @pytest.mark.asyncio
    async def test_single_page(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"id": i} for i in range(50)], "total": 50,
        }
        result = await paginate_erp(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        # 50 < 100 (page_size) → 只查一页
        assert len(result["list"]) == 50
        assert mock_dispatcher.execute_raw.call_count == 1

    @pytest.mark.asyncio
    async def test_multi_page(self):
        """多页翻页终止"""
        call_count = 0

        async def mock_raw(tool, action, params):
            nonlocal call_count
            call_count += 1
            page = params.get("page", 1)
            if page <= 2:
                return {"list": [{"id": i} for i in range(100)]}
            else:
                # 第3页返回30条（< page_size=100）→ 终止
                return {"list": [{"id": i} for i in range(30)]}

        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw = mock_raw

        result = await paginate_erp(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert len(result["list"]) == 230  # 100 + 100 + 30
        assert result["total"] == 230

    @pytest.mark.asyncio
    async def test_max_pages_limit(self):
        """max_pages 上限"""
        async def mock_raw(tool, action, params):
            return {"list": [{"id": 1}] * 100}  # 永远满页

        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw = mock_raw

        result = await paginate_erp(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            max_pages=3,
            _dispatcher=mock_dispatcher,
        )
        assert len(result["list"]) == 300  # 3 页 × 100
        assert "warning" in result

    @pytest.mark.asyncio
    async def test_error_on_first_page(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {"error": "接口错误"}
        result = await paginate_erp(
            "erp_trade_query", "order_list",
            _dispatcher=mock_dispatcher,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_error_on_later_page_returns_partial(self):
        """后续页错误时返回已拉取的数据"""
        call_count = 0

        async def mock_raw(tool, action, params):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"list": [{"id": i} for i in range(100)]}
            return {"error": "接口错误"}

        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw = mock_raw

        result = await paginate_erp(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        # 应返回第一页的数据
        assert len(result["list"]) == 100

    @pytest.mark.asyncio
    async def test_semaphore_concurrency(self):
        """并发控制信号量"""
        semaphore = asyncio.Semaphore(2)

        async def mock_raw(tool, action, params):
            return {"list": [{"id": 1}] * 50}  # < page_size → 终止

        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw = mock_raw

        result = await paginate_erp(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
            _semaphore=semaphore,
        )
        assert len(result["list"]) == 50

    @pytest.mark.asyncio
    async def test_no_dispatcher(self):
        result = await paginate_erp("erp_trade_query", "order_list")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_api_total_from_first_page(self):
        """第一页 API 返回的 total 字段被保留为 api_total"""
        async def mock_raw(tool, action, params):
            return {"list": [{"id": 1}] * 50, "total": 8211}

        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw = mock_raw

        result = await paginate_erp(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert result["api_total"] == 8211
        assert result["total"] == 50  # 实际拉取数

    @pytest.mark.asyncio
    async def test_api_total_zero(self):
        """total=0 时 api_total 应为 0，不被跳过"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [], "total": 0,
        }
        result = await paginate_erp(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert result["api_total"] == 0

    @pytest.mark.asyncio
    async def test_api_total_missing(self):
        """API 未返回 total 字段时，结果不包含 api_total"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"id": 1}] * 50,
        }
        result = await paginate_erp(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert "api_total" not in result
        assert result["total"] == 50

    @pytest.mark.asyncio
    async def test_api_total_non_numeric(self):
        """total 为非数字字符串时，不设置 api_total"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"id": 1}] * 50, "total": "invalid",
        }
        result = await paginate_erp(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert "api_total" not in result

    @pytest.mark.asyncio
    async def test_api_total_from_totalCount_field(self):
        """支持 totalCount 字段作为 fallback"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"id": 1}] * 50, "totalCount": 999,
        }
        result = await paginate_erp(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
        )
        assert result["api_total"] == 999


class TestExtractList:
    """extract_list response_key 探测测试"""

    def test_list_key(self):
        items, key = extract_list({"list": [{"id": 1}], "total": 1})
        assert items == [{"id": 1}]
        assert key == "list"

    def test_items_key(self):
        items, key = extract_list({"items": [{"sku": "A"}]})
        assert items == [{"sku": "A"}]
        assert key == "items"

    def test_stock_status_key(self):
        items, key = extract_list({"stockStatusVoList": [{"qty": 10}]})
        assert items == [{"qty": 10}]
        assert key == "stockStatusVoList"

    def test_trades_key(self):
        items, key = extract_list({"trades": [{"tid": "123"}]})
        assert items == [{"tid": "123"}]
        assert key == "trades"

    def test_priority_order(self):
        """多个 key 同时存在时，按优先级取 list"""
        items, key = extract_list({
            "list": [{"id": 1}],
            "items": [{"id": 2}],
        })
        assert key == "list"
        assert items == [{"id": 1}]

    def test_empty_list_skipped(self):
        """空列表不命中，继续查找下一个 key"""
        items, key = extract_list({
            "list": [],
            "items": [{"id": 1}],
        })
        assert key == "items"
        assert items == [{"id": 1}]

    def test_no_matching_key(self):
        """没有任何已知 key → 返回空列表 + "list" """
        items, key = extract_list({"unknown_field": [1, 2, 3]})
        assert items == []
        assert key == "list"

    def test_empty_response(self):
        items, key = extract_list({})
        assert items == []
        assert key == "list"

    def test_non_list_value_skipped(self):
        """值不是 list 类型时跳过"""
        items, key = extract_list({"list": "not_a_list", "items": [{"id": 1}]})
        assert key == "items"
        assert items == [{"id": 1}]
