"""沙盒数据源函数测试"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.sandbox.functions import (
    build_sandbox_executor,
    compute_code_hash,
    erp_query,
    erp_query_all,
)


class TestErpQuery:
    """erp_query 单页查询测试"""

    @pytest.mark.asyncio
    async def test_basic_query(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"tid": "123"}], "total": 1,
        }
        result = await erp_query(
            "erp_trade_query", "order_list",
            {"status": "WAIT_SELLER_SEND_GOODS"},
            _dispatcher=mock_dispatcher,
        )
        assert result["total"] == 1
        assert result["list"][0]["tid"] == "123"
        mock_dispatcher.execute_raw.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_dispatcher(self):
        result = await erp_query("erp_trade_query", "order_list")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_params(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {"list": [], "total": 0}
        result = await erp_query(
            "erp_trade_query", "order_list",
            _dispatcher=mock_dispatcher,
        )
        assert result["total"] == 0
        # 应传入空 dict
        mock_dispatcher.execute_raw.assert_called_once_with(
            "erp_trade_query", "order_list", {},
        )


class TestErpQueryAll:
    """erp_query_all 全量翻页测试"""

    @pytest.mark.asyncio
    async def test_single_page(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {
            "list": [{"id": i} for i in range(50)], "total": 50,
        }
        result = await erp_query_all(
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

        result = await erp_query_all(
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

        result = await erp_query_all(
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
        result = await erp_query_all(
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

        result = await erp_query_all(
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

        result = await erp_query_all(
            "erp_trade_query", "order_list",
            {"page_size": 100},
            _dispatcher=mock_dispatcher,
            _semaphore=semaphore,
        )
        assert len(result["list"]) == 50

    @pytest.mark.asyncio
    async def test_no_dispatcher(self):
        result = await erp_query_all("erp_trade_query", "order_list")
        assert "error" in result


class TestBuildSandboxExecutor:
    """build_sandbox_executor 工厂函数测试"""

    def test_creates_executor_with_functions(self):
        executor = build_sandbox_executor()
        # 应注册 4 个数据源函数
        assert "erp_query" in executor._registered_funcs
        assert "erp_query_all" in executor._registered_funcs
        assert "web_search" in executor._registered_funcs
        assert "search_knowledge" in executor._registered_funcs

    def test_custom_timeout(self):
        executor = build_sandbox_executor(timeout=60.0)
        assert executor._timeout == 60.0

    def test_custom_max_result_chars(self):
        executor = build_sandbox_executor(max_result_chars=5000)
        assert executor._max_result_chars == 5000

    @pytest.mark.asyncio
    async def test_erp_query_with_dispatcher(self):
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute_raw.return_value = {"list": [], "total": 0}

        executor = build_sandbox_executor(dispatcher=mock_dispatcher)

        code = "data = await erp_query('erp_trade_query', 'shop_list')\ndata['total']"
        result = await executor.execute(code, "测试ERP查询")
        assert "0" in result

    @pytest.mark.asyncio
    async def test_erp_query_without_dispatcher(self):
        executor = build_sandbox_executor()

        code = "data = await erp_query('erp_trade_query', 'shop_list')\nstr(data)"
        result = await executor.execute(code, "测试无dispatcher")
        assert "error" in result


class TestComputeCodeHash:
    """compute_code_hash 测试"""

    def test_same_code_same_hash(self):
        code = "x = 1 + 1"
        assert compute_code_hash(code) == compute_code_hash(code)

    def test_different_code_different_hash(self):
        assert compute_code_hash("x = 1") != compute_code_hash("x = 2")

    def test_strips_whitespace(self):
        assert compute_code_hash("  x = 1  ") == compute_code_hash("x = 1")

    def test_returns_12_chars(self):
        result = compute_code_hash("test")
        assert len(result) == 12
