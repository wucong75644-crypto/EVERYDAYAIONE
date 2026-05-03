"""测试 erp_tool_executor — ErpToolMixin AgentResult 错误返回"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Mock pydantic_settings + 依赖链模块 以避免环境依赖
if "pydantic_settings" not in sys.modules:
    sys.modules["pydantic_settings"] = MagicMock()

# 预注入无法 import 的模块（它们内部依赖 pydantic_settings）
import types
for mod_path in ["core.redis", "services.org", "services.org.config_resolver"]:
    if mod_path not in sys.modules:
        sys.modules[mod_path] = types.ModuleType(mod_path)

# core.redis 需要 get_redis 和 RedisClient 属性（函数内 from core.redis import ...）
_redis_mod = sys.modules["core.redis"]
_redis_mod.get_redis = AsyncMock(return_value=None)
_redis_mod.RedisClient = MagicMock()

import pytest

from services.agent.agent_result import AgentResult
from services.agent.erp_tool_executor import ErpToolMixin


# ── Fixture ──


class FakeErpMixin(ErpToolMixin):
    """模拟宿主类属性"""

    def __init__(self):
        self.db = MagicMock()
        self.user_id = "u1"
        self.org_id = "org1"
        self.conversation_id = "conv1"
        self.request_ctx = None


@pytest.fixture
def mixin():
    return FakeErpMixin()


# ============================================================
# _get_erp_dispatcher 错误分支
# ============================================================


class TestGetErpDispatcherErrors:

    @pytest.mark.asyncio
    async def test_org_credentials_not_found(self, mixin):
        """企业凭证不存在 → AgentResult(error, retryable=False)"""
        mock_resolver = MagicMock()
        mock_resolver.get_erp_credentials.side_effect = ValueError(
            "企业 org1 未配置 ERP 凭证"
        )

        with patch("services.org.config_resolver.OrgConfigResolver", create=True,
                   return_value=mock_resolver):
            result = await mixin._get_erp_dispatcher()

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert result.metadata.get("retryable") is False
        assert "未配置" in result.summary

    @pytest.mark.asyncio
    async def test_personal_user_not_configured(self, mixin):
        """散客 + ERP 未配置 → AgentResult(error)"""
        mixin.org_id = None

        mock_client = MagicMock()
        mock_client.is_configured = False
        mock_client.close = AsyncMock()

        with patch("services.kuaimai.client.KuaiMaiClient",
                   return_value=mock_client):
            result = await mixin._get_erp_dispatcher()

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "未配置" in result.summary
        mock_client.close.assert_called_once()


# ============================================================
# _erp_dispatch 错误分支
# ============================================================


class TestErpDispatchValidation:

    @pytest.mark.asyncio
    async def test_missing_action(self, mixin):
        """查询工具缺少 action → error"""
        # 非 erp_execute 的查询工具走两步模式
        result = await mixin._erp_dispatch("erp_trade_query", {"action": ""})

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "action" in result.summary
        assert result.metadata.get("retryable") is True

    @pytest.mark.asyncio
    async def test_dispatcher_error_propagates(self, mixin):
        """_get_erp_dispatcher 返回 AgentResult(error) → 直接透传"""
        err = AgentResult(
            summary="ERP未配置", status="error",
            error_message="not configured", metadata={"retryable": False},
        )
        mixin._get_erp_dispatcher = AsyncMock(return_value=err)

        result = await mixin._erp_dispatch("erp_trade_query", {
            "action": "order_list", "params": {},
        })

        assert result is err

    @pytest.mark.asyncio
    async def test_query_exception_returns_error(self, mixin):
        """dispatcher.execute 抛异常 → AgentResult(error, retryable=True)"""
        mock_dispatcher = MagicMock()
        mock_dispatcher.execute = AsyncMock(
            side_effect=RuntimeError("API timeout")
        )
        mock_dispatcher.close = AsyncMock()
        mixin._get_erp_dispatcher = AsyncMock(return_value=mock_dispatcher)

        result = await mixin._erp_dispatch("erp_trade_query", {
            "action": "order_list", "params": {},
        })

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "API timeout" in result.summary
        assert result.metadata.get("retryable") is True


class TestErpExecuteWrite:

    @pytest.mark.asyncio
    async def test_redis_unavailable(self, mixin):
        """Redis 不可用 → error, retryable=True"""
        mock_dispatcher = MagicMock()
        mock_dispatcher.close = AsyncMock()
        mixin._get_erp_dispatcher = AsyncMock(return_value=mock_dispatcher)

        with patch("core.redis.get_redis", create=True,
                   new_callable=AsyncMock, return_value=None):
            result = await mixin._erp_dispatch("erp_execute", {
                "category": "trade", "action": "confirm", "params": {},
            })

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "缓存服务" in result.summary
        assert result.metadata.get("retryable") is True

    @pytest.mark.asyncio
    async def test_idempotency_duplicate(self, mixin):
        """10分钟内重复写操作 → error, retryable=False"""
        mock_dispatcher = MagicMock()
        mock_dispatcher.close = AsyncMock()
        mixin._get_erp_dispatcher = AsyncMock(return_value=mock_dispatcher)

        mock_redis = AsyncMock()
        mock_redis.get.return_value = "1"  # 模拟已完成标记

        with patch("core.redis.get_redis", create=True,
                   new_callable=AsyncMock, return_value=mock_redis):
            result = await mixin._erp_dispatch("erp_execute", {
                "category": "trade", "action": "confirm", "params": {},
            })

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "已执行过" in result.summary
        assert result.metadata.get("retryable") is False

    @pytest.mark.asyncio
    async def test_concurrent_lock_conflict(self, mixin):
        """并发锁冲突 → error, retryable=True"""
        mock_dispatcher = MagicMock()
        mock_dispatcher.close = AsyncMock()
        mixin._get_erp_dispatcher = AsyncMock(return_value=mock_dispatcher)

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # 未完成过

        with patch("core.redis.get_redis", create=True,
                   new_callable=AsyncMock, return_value=mock_redis), \
             patch("core.redis.RedisClient", create=True) as mock_rc:
            mock_rc.acquire_lock = AsyncMock(return_value=None)  # 获取锁失败
            result = await mixin._erp_dispatch("erp_execute", {
                "category": "trade", "action": "confirm", "params": {},
            })

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "正在执行中" in result.summary
        assert result.metadata.get("retryable") is True


# ============================================================
# _local_dispatch 错误分支
# ============================================================


class TestLocalDispatchErrors:

    @pytest.mark.asyncio
    async def test_unknown_tool(self, mixin):
        """未知本地工具 → error, retryable=False"""
        result = await mixin._local_dispatch("local_nonexistent", {})

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "Unknown" in result.summary
        assert result.metadata.get("retryable") is False

    @pytest.mark.asyncio
    async def test_query_exception(self, mixin):
        """本地查询抛异常 → error, retryable=True"""
        with patch("services.kuaimai.erp_local_query.local_stock_query",
                   new_callable=AsyncMock, side_effect=RuntimeError("DB connection lost")):
            result = await mixin._local_dispatch("local_stock_query", {})

        assert isinstance(result, AgentResult)
        assert result.is_failure
        assert "DB connection lost" in result.summary
        assert result.metadata.get("retryable") is True


# ============================================================
# _erp_dispatch 成功路径（param_doc）
# ============================================================


class TestErpDispatchSuccess:

    @pytest.mark.asyncio
    async def test_param_doc_returns_success(self, mixin):
        """Step 1 params=None → 返回参数文档 AgentResult(success)"""
        with patch("services.kuaimai.param_doc.generate_param_doc",
                   return_value="## order_list\n参数: start_time, end_time"):
            result = await mixin._erp_dispatch("erp_trade_query", {
                "action": "order_list",
                # params 未传 → None → Step 1
            })

        assert isinstance(result, AgentResult)
        assert result.status == "success"
        assert "order_list" in result.summary
