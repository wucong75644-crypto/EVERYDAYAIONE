"""测试 tool_loop_helpers — invoke_tool_with_cache 的 AgentResult 包装与 audit_status 同步"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.agent.agent_result import AgentResult
from services.agent.tool_loop_helpers import invoke_tool_with_cache


def _mock_cache(hit_value=None):
    """构造 mock cache：get 返回 hit_value，put 不做事"""
    cache = MagicMock()
    cache.get.return_value = hit_value
    cache.put.return_value = None
    return cache


def _mock_executor(return_value):
    """构造 mock executor：execute 返回指定值"""
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=return_value)
    return executor


def _mock_executor_raises(exc):
    """构造 mock executor：execute 抛出指定异常"""
    executor = MagicMock()
    executor.execute = AsyncMock(side_effect=exc)
    return executor


class TestNormalExecution:
    """工具正常执行"""

    @pytest.mark.asyncio
    async def test_str_result_audit_success(self):
        """工具返回 str → audit_status = success"""
        executor = _mock_executor("查询结果文本")
        cache = _mock_cache()

        result, status, is_cached, ms = await invoke_tool_with_cache(
            executor, cache, "web_search", {"query": "test"},
            budget=None, default_timeout=30.0,
        )

        assert result == "查询结果文本"
        assert status == "success"
        assert is_cached is False

    @pytest.mark.asyncio
    async def test_agent_result_success_audit_success(self):
        """工具返回 AgentResult(success) → audit_status = success"""
        ar = AgentResult(summary="ok", status="success")
        executor = _mock_executor(ar)
        cache = _mock_cache()

        result, status, is_cached, ms = await invoke_tool_with_cache(
            executor, cache, "data_query", {"file": "x"},
            budget=None, default_timeout=30.0,
        )

        assert result is ar
        assert status == "success"

    @pytest.mark.asyncio
    async def test_agent_result_error_syncs_audit_status(self):
        """工具返回 AgentResult(error) → audit_status 同步为 error"""
        ar = AgentResult(summary="SQL错误", status="error", error_message="bad")
        executor = _mock_executor(ar)
        cache = _mock_cache()

        result, status, is_cached, ms = await invoke_tool_with_cache(
            executor, cache, "data_query", {"sql": "bad"},
            budget=None, default_timeout=30.0,
        )

        assert result is ar
        assert status == "error"

    @pytest.mark.asyncio
    async def test_agent_result_timeout_syncs_audit_status(self):
        """工具返回 AgentResult(timeout) → audit_status 同步为 timeout"""
        ar = AgentResult(summary="超时", status="timeout", error_message="t/o")
        executor = _mock_executor(ar)
        cache = _mock_cache()

        result, status, is_cached, ms = await invoke_tool_with_cache(
            executor, cache, "data_query", {},
            budget=None, default_timeout=30.0,
        )

        assert result is ar
        assert status == "timeout"

    @pytest.mark.asyncio
    async def test_agent_result_empty_audit_success(self):
        """工具返回 AgentResult(empty) → audit_status 保持 success"""
        ar = AgentResult(summary="无结果", status="empty")
        executor = _mock_executor(ar)
        cache = _mock_cache()

        result, status, is_cached, ms = await invoke_tool_with_cache(
            executor, cache, "data_query", {},
            budget=None, default_timeout=30.0,
        )

        assert result is ar
        assert status == "success"


class TestCacheHit:
    """缓存命中"""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_directly(self):
        """缓存命中 → 直接返回，不调用 executor"""
        cached_ar = AgentResult(summary="cached", status="success")
        executor = _mock_executor("should not be called")
        cache = _mock_cache(hit_value=cached_ar)

        result, status, is_cached, ms = await invoke_tool_with_cache(
            executor, cache, "data_query", {"file": "x"},
            budget=None, default_timeout=30.0,
        )

        assert result is cached_ar
        assert status == "success"
        assert is_cached is True
        executor.execute.assert_not_called()


class TestExceptionHandling:
    """异常包装"""

    @pytest.mark.asyncio
    async def test_timeout_wraps_as_agent_result(self):
        """asyncio.TimeoutError → AgentResult(status=timeout)"""
        executor = _mock_executor_raises(asyncio.TimeoutError())
        cache = _mock_cache()

        result, status, is_cached, ms = await invoke_tool_with_cache(
            executor, cache, "data_query", {},
            budget=None, default_timeout=5.0,
        )

        assert isinstance(result, AgentResult)
        assert result.status == "timeout"
        assert result.is_failure
        assert "5" in result.summary
        assert status == "timeout"

    @pytest.mark.asyncio
    async def test_generic_exception_wraps_as_agent_result(self):
        """其他异常 → AgentResult(status=error)"""
        executor = _mock_executor_raises(RuntimeError("connection refused"))
        cache = _mock_cache()

        result, status, is_cached, ms = await invoke_tool_with_cache(
            executor, cache, "data_query", {},
            budget=None, default_timeout=30.0,
        )

        assert isinstance(result, AgentResult)
        assert result.status == "error"
        assert result.is_failure
        assert "connection refused" in result.summary
        assert status == "error"


class TestBudgetIntegration:
    """预算控制"""

    @pytest.mark.asyncio
    async def test_budget_tool_timeout_used(self):
        """有 budget 时用 budget.tool_timeout 的返回值"""
        budget = MagicMock()
        budget.tool_timeout.return_value = 10.0

        executor = _mock_executor("ok")
        cache = _mock_cache()

        await invoke_tool_with_cache(
            executor, cache, "data_query", {},
            budget=budget, default_timeout=30.0,
        )

        budget.tool_timeout.assert_called_once_with(30.0)
