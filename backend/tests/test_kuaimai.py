"""
快麦ERP API 集成测试

覆盖：签名算法、客户端请求、Token刷新、业务服务、工具注册。
"""

import hashlib
import hmac as hmac_mod
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

import pytest

from config.agent_tools import (
    ALL_TOOLS,
    SYNC_TOOLS,
    TOOL_SCHEMAS,
    validate_tool_call,
)
from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.errors import (
    KuaiMaiBusinessError,
    KuaiMaiError,
    KuaiMaiSignatureError,
    KuaiMaiTokenExpiredError,
)


# ============================================================
# 签名算法测试
# ============================================================


class TestGenerateSign:
    """签名算法单元测试"""

    def setup_method(self):
        self.client = KuaiMaiClient(
            app_key="test_key",
            app_secret="test_secret",
            access_token="test_token",
        )

    def test_hmac_sign(self):
        """HMAC_MD5 签名"""
        params = {"method": "test.api", "appKey": "test_key", "version": "1.0"}
        sign = self.client.generate_sign(params, sign_method="hmac")

        sorted_str = "appKeytest_keymethodtest.apiversion1.0"
        expected = hmac_mod.new(
            b"test_secret", sorted_str.encode("utf-8"), hashlib.md5
        ).hexdigest().upper()

        assert sign == expected
        assert len(sign) == 32

    def test_md5_sign(self):
        """MD5 签名"""
        params = {"a": "1", "b": "2"}
        sign = self.client.generate_sign(params, sign_method="md5")

        sorted_str = "a1b2"
        sign_str = "test_secret" + sorted_str + "test_secret"
        expected = hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()

        assert sign == expected

    def test_hmac_sha256_sign(self):
        """HMAC_SHA256 签名"""
        params = {"x": "hello"}
        sign = self.client.generate_sign(params, sign_method="hmac-sha256")

        sorted_str = "xhello"
        expected = hmac_mod.new(
            b"test_secret", sorted_str.encode("utf-8"), hashlib.sha256
        ).hexdigest().upper()

        assert sign == expected

    def test_sign_excludes_none_and_sign(self):
        """签名排除 None 值和 sign 参数"""
        params = {"a": "1", "b": None, "sign": "old_sign", "c": "3"}
        sign = self.client.generate_sign(params, sign_method="md5")

        sorted_str = "a1c3"
        sign_str = "test_secret" + sorted_str + "test_secret"
        expected = hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()

        assert sign == expected

    def test_sign_ascii_order(self):
        """参数按 ASCII 排序"""
        params = {"z": "1", "a": "2", "m": "3"}
        sign = self.client.generate_sign(params, sign_method="md5")

        sorted_str = "a2m3z1"
        sign_str = "test_secret" + sorted_str + "test_secret"
        expected = hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()

        assert sign == expected

    def test_sign_is_uppercase_hex(self):
        """签名为32位大写HEX"""
        params = {"test": "value"}
        sign = self.client.generate_sign(params)

        assert len(sign) == 32
        assert sign == sign.upper()
        assert all(c in "0123456789ABCDEF" for c in sign)


# ============================================================
# 客户端测试
# ============================================================


class TestKuaiMaiClient:
    """客户端请求测试"""

    def test_is_configured_true(self):
        """配置完整时返回 True"""
        client = KuaiMaiClient(
            app_key="key", app_secret="secret", access_token="token"
        )
        assert client.is_configured is True

    @patch("services.kuaimai.client.settings")
    def test_is_configured_false(self, mock_settings):
        """配置缺失时返回 False（mock settings 避免读 .env）"""
        mock_settings.kuaimai_app_key = None
        mock_settings.kuaimai_app_secret = None
        mock_settings.kuaimai_access_token = None
        mock_settings.kuaimai_refresh_token = None
        mock_settings.kuaimai_base_url = "https://gw.superboss.cc/router"
        mock_settings.kuaimai_timeout = 10.0

        client = KuaiMaiClient(app_key="", app_secret="", access_token="")
        assert client.is_configured is False

    @pytest.mark.asyncio
    @patch("services.kuaimai.client.settings")
    async def test_request_not_configured(self, mock_settings):
        """未配置时抛出 KuaiMaiError"""
        mock_settings.kuaimai_app_key = None
        mock_settings.kuaimai_app_secret = None
        mock_settings.kuaimai_access_token = None
        mock_settings.kuaimai_refresh_token = None
        mock_settings.kuaimai_base_url = "https://gw.superboss.cc/router"
        mock_settings.kuaimai_timeout = 10.0

        client = KuaiMaiClient(app_key="", app_secret="", access_token="")
        with pytest.raises(KuaiMaiError, match="未配置"):
            await client.request("test.api")

    def test_handle_response_success(self):
        """成功响应正常返回"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t"
        )
        data = {"success": True, "list": [{"id": 1}], "total": 1}
        result = client._handle_response(data, "test.api")
        assert result == data

    def test_handle_response_signature_error(self):
        """签名错误抛出 KuaiMaiSignatureError"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t"
        )
        data = {"success": False, "code": "25", "msg": "签名无效"}
        with pytest.raises(KuaiMaiSignatureError):
            client._handle_response(data, "test.api")

    def test_handle_response_token_expired(self):
        """Token 过期错误码抛出 KuaiMaiTokenExpiredError"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t"
        )
        for code in ["27", "105", "106"]:
            data = {"success": False, "code": code, "msg": "token expired"}
            with pytest.raises(KuaiMaiTokenExpiredError):
                client._handle_response(data, "test.api")

    def test_handle_response_business_error(self):
        """其他业务错误抛出 KuaiMaiBusinessError"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t"
        )
        data = {"success": False, "code": "99", "msg": "页码不能为空"}
        with pytest.raises(KuaiMaiBusinessError, match="页码不能为空"):
            client._handle_response(data, "test.api")

    @pytest.mark.asyncio
    async def test_request_with_retry_token_refresh(self):
        """Token 过期后自动刷新并重试"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t", refresh_token="r"
        )

        call_count = 0

        async def mock_request(method, biz_params=None, sign_method="hmac", **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise KuaiMaiTokenExpiredError()
            return {"success": True, "list": []}

        client.request = mock_request
        client.refresh_token = AsyncMock(return_value=True)

        result = await client.request_with_retry("test.api")
        assert result == {"success": True, "list": []}
        assert call_count == 2
        client.refresh_token.assert_called_once()

    @pytest.mark.asyncio
    async def test_request_with_retry_refresh_fails(self):
        """Token 刷新失败时抛出异常"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t", refresh_token="r"
        )

        async def mock_request(method, biz_params=None, sign_method="hmac", **kwargs):
            raise KuaiMaiTokenExpiredError()

        client.request = mock_request
        client.refresh_token = AsyncMock(return_value=False)

        with pytest.raises(KuaiMaiTokenExpiredError):
            await client.request_with_retry("test.api")

    @pytest.mark.asyncio
    async def test_close(self):
        """关闭客户端"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t"
        )
        mock_http = AsyncMock()
        mock_http.is_closed = False
        client._client = mock_http

        await client.close()
        mock_http.aclose.assert_called_once()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_close_already_closed(self):
        """已关闭的客户端不重复关闭"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t"
        )
        client._client = None
        await client.close()  # 不应抛异常

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """支持 async context manager"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t"
        )
        mock_http = AsyncMock()
        mock_http.is_closed = False
        client._client = mock_http

        async with client:
            pass
        mock_http.aclose.assert_called_once()


# ============================================================
# Token 刷新测试
# ============================================================


class TestTokenRefresh:
    """Token 刷新和缓存测试"""

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        """Token 刷新成功"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="old_token",
            refresh_token="refresh_123",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "session": {
                "accessToken": "new_token_abc",
                "refreshToken": "new_refresh_xyz",
            },
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        mock_http.is_closed = False
        client._client = mock_http

        with patch.object(client, "_cache_token", new_callable=AsyncMock):
            result = await client.refresh_token()

        assert result is True
        assert client._access_token == "new_token_abc"
        assert client._refresh_token == "new_refresh_xyz"

    @pytest.mark.asyncio
    async def test_refresh_token_no_refresh_token(self):
        """无 refresh_token 时返回 False"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t",
            refresh_token="",
        )
        result = await client.refresh_token()
        assert result is False

    @pytest.mark.asyncio
    async def test_refresh_token_api_failure(self):
        """API 返回失败时返回 False"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="t",
            refresh_token="r",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": False, "code": "99", "msg": "refresh failed"
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_response
        mock_http.is_closed = False
        client._client = mock_http

        result = await client.refresh_token()
        assert result is False

    @pytest.mark.asyncio
    async def test_load_cached_token_from_redis(self):
        """从 Redis 加载缓存 Token"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="env_token",
        )

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=["cached_token", "cached_refresh"])

        with patch("core.redis.get_redis", new_callable=AsyncMock) as mock_get_redis:
            mock_get_redis.return_value = mock_redis
            await client.load_cached_token()

        assert client._access_token == "cached_token"
        assert client._refresh_token == "cached_refresh"

    @pytest.mark.asyncio
    async def test_load_cached_token_redis_empty(self):
        """Redis 无缓存时保持原值"""
        client = KuaiMaiClient(
            app_key="k", app_secret="s", access_token="env_token",
        )

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch("core.redis.get_redis", new_callable=AsyncMock) as mock_get_redis:
            mock_get_redis.return_value = mock_redis
            await client.load_cached_token()

        assert client._access_token == "env_token"


# ============================================================
# 工具执行器测试
# ============================================================


class TestToolExecutorERP:
    """tool_executor ERP handler 测试"""

    @pytest.mark.asyncio
    @patch("services.kuaimai.client.settings")
    async def test_get_erp_dispatcher_not_configured(self, mock_settings):
        """ERP 未配置时返回友好提示"""
        mock_settings.kuaimai_app_key = None
        mock_settings.kuaimai_app_secret = None
        mock_settings.kuaimai_access_token = None
        mock_settings.kuaimai_refresh_token = None
        mock_settings.kuaimai_base_url = "https://gw.superboss.cc/router"
        mock_settings.kuaimai_timeout = 10.0

        from services.tool_executor import ToolExecutor
        executor = ToolExecutor(
            db=MagicMock(), user_id="u1", conversation_id="c1"
        )
        result = await executor._get_erp_dispatcher()
        assert isinstance(result, str)
        assert "未配置" in result

    @pytest.mark.asyncio
    async def test_erp_dispatch_error(self):
        """ERP 调度异常时返回错误信息"""
        from services.tool_executor import ToolExecutor
        executor = ToolExecutor(
            db=MagicMock(), user_id="u1", conversation_id="c1"
        )

        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute.side_effect = KuaiMaiBusinessError(
            message="参数错误", code="100"
        )
        mock_dispatcher.close = AsyncMock()

        with patch.object(executor, "_get_erp_dispatcher", return_value=mock_dispatcher):
            result = await executor._erp_dispatch(
                "erp_trade_query",
                {"action": "order_list", "params": {"order_id": "123"}},
            )
        assert "失败" in result

    async def test_erp_dispatch_step1_returns_doc(self):
        """两步调用 Step 1：无 params 时返回参数文档"""
        from services.tool_executor import ToolExecutor
        executor = ToolExecutor(
            db=MagicMock(), user_id="u1", conversation_id="c1"
        )
        result = await executor._erp_dispatch(
            "erp_trade_query", {"action": "order_list"}
        )
        assert "order_list" in result
        assert "参数" in result
        assert "order_id" in result


# ============================================================
# 工具注册测试
# ============================================================


class TestToolRegistration:
    """Agent 工具注册验证"""

    def test_erp_tools_in_sync_tools(self):
        """ERP 工具注册为同步工具"""
        erp_tools = {
            "erp_info_query",
            "erp_product_query",
            "erp_trade_query",
            "erp_aftersales_query",
            "erp_warehouse_query",
            "erp_purchase_query",
            "erp_taobao_query",
            "erp_execute",
        }
        assert erp_tools.issubset(SYNC_TOOLS)
        assert erp_tools.issubset(ALL_TOOLS)

    def test_erp_tool_schemas_exist(self):
        """ERP 工具 Schema 已注册"""
        for tool_name in [
            "erp_info_query",
            "erp_product_query",
            "erp_trade_query",
            "erp_aftersales_query",
            "erp_warehouse_query",
            "erp_purchase_query",
            "erp_taobao_query",
            "erp_execute",
        ]:
            assert tool_name in TOOL_SCHEMAS

    def test_validate_erp_tool_calls(self):
        """验证 ERP 工具调用参数"""
        assert validate_tool_call(
            "erp_trade_query", {"action": "order_list"}
        ) is True
        assert validate_tool_call(
            "erp_product_query", {"action": "product_list"}
        ) is True
        assert validate_tool_call(
            "erp_execute", {"category": "trade", "action": "order_create"}
        ) is True

        # 缺少必填 action
        assert validate_tool_call("erp_trade_query", {}) is False
        assert validate_tool_call("erp_product_query", {}) is False
        # 未知工具
        assert validate_tool_call("unknown_erp_tool", {}) is False

    def test_all_tools_contains_erp_and_routing(self):
        """ALL_TOOLS 包含 ERP 工具和路由工具"""
        erp_tools = {
            "erp_info_query", "erp_product_query", "erp_trade_query",
            "erp_aftersales_query", "erp_warehouse_query",
            "erp_purchase_query", "erp_taobao_query", "erp_execute",
        }
        routing_tools = {
            "route_to_chat", "route_to_image",
            "route_to_video", "ask_user",
        }
        assert erp_tools.issubset(ALL_TOOLS)
        assert routing_tools.issubset(ALL_TOOLS)

    def test_erp_tool_schemas_have_action(self):
        """ERP 查询工具 Schema 有 action 必填字段"""
        for tool_name in [
            "erp_product_query", "erp_trade_query",
            "erp_aftersales_query", "erp_warehouse_query",
            "erp_purchase_query",
        ]:
            schema = TOOL_SCHEMAS[tool_name]
            assert "action" in schema["required"]


# ============================================================
# 异常体系测试
# ============================================================


class TestErrors:
    """异常类测试"""

    def test_kuaimai_error_hierarchy(self):
        """异常继承关系"""
        from core.exceptions import ExternalServiceError
        assert issubclass(KuaiMaiError, ExternalServiceError)
        assert issubclass(KuaiMaiSignatureError, KuaiMaiError)
        assert issubclass(KuaiMaiTokenExpiredError, KuaiMaiError)
        assert issubclass(KuaiMaiBusinessError, KuaiMaiError)

    def test_signature_error_has_code(self):
        """签名错误包含错误码"""
        err = KuaiMaiSignatureError()
        assert err.error_code == "25"
        assert "签名" in err.message

    def test_business_error_message(self):
        """业务错误保留原始消息"""
        err = KuaiMaiBusinessError(message="页码不能为空", code="100")
        assert err.message == "快麦ERP: 页码不能为空"
        assert err.error_code == "100"

    def test_token_expired_error(self):
        """Token 过期错误消息"""
        err = KuaiMaiTokenExpiredError()
        assert "过期" in err.message

    def test_rate_limit_error(self):
        """频率限制错误"""
        from services.kuaimai.errors import KuaiMaiRateLimitError
        err = KuaiMaiRateLimitError()
        assert "频繁" in err.message


# ============================================================
# TestErpDispatcher — 统一调度引擎
# ============================================================


class TestErpDispatcher:

    def _make_entry(self, **overrides):
        """构造 ApiEntry"""
        from services.kuaimai.registry.base import ApiEntry
        defaults = {
            "method": "erp.test.query",
            "description": "测试接口",
            "param_map": {"order_id": "tid"},
            "required_params": [],
            "defaults": {},
            "response_key": "list",
        }
        defaults.update(overrides)
        return ApiEntry(**defaults)

    def _make_dispatcher(self, client=None):
        from services.kuaimai.dispatcher import ErpDispatcher
        return ErpDispatcher(client or AsyncMock())

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        """未知工具名→错误提示"""
        d = self._make_dispatcher()
        result = await d.execute("erp_nonexistent", "list", {})
        assert "未知的ERP工具" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        """未知操作名→提示可选操作"""
        d = self._make_dispatcher()
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_trade_query": {"order_list": self._make_entry()},
        }):
            result = await d.execute("erp_trade_query", "bad_action", {})
            assert "未知的操作" in result
            assert "order_list" in result

    @pytest.mark.asyncio
    async def test_missing_required_params(self):
        """缺少必填参数→错误提示"""
        entry = self._make_entry(required_params=["order_id"])
        d = self._make_dispatcher()
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_trade_query": {"order_list": entry},
        }):
            result = await d.execute("erp_trade_query", "order_list", {})
            assert "缺少必填参数" in result
            assert "order_id" in result

    @pytest.mark.asyncio
    async def test_execute_success(self):
        """正常调用→格式化结果"""
        entry = self._make_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "list": [{"id": 1}], "total": 1,
        }
        d = self._make_dispatcher(mock_client)
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_trade_query": {"order_list": entry},
        }):
            result = await d.execute(
                "erp_trade_query", "order_list", {},
            )
            assert "1" in result
            mock_client.request_with_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_api_error(self):
        """API调用失败→错误提示"""
        entry = self._make_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.side_effect = Exception("timeout")
        d = self._make_dispatcher(mock_client)
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_trade_query": {"order_list": entry},
        }):
            result = await d.execute(
                "erp_trade_query", "order_list", {},
            )
            assert "调用失败" in result

    def test_format_response_with_formatter(self):
        """有格式化函数→调用格式化"""
        entry = self._make_entry(formatter="test_fmt")
        d = self._make_dispatcher()
        with patch("services.kuaimai.dispatcher.get_formatter") as mock_fmt:
            mock_fmt.return_value = lambda data, e: "格式化结果"
            result = d._format_response({"list": []}, entry, "test")
            assert result == "格式化结果"

    def test_format_response_formatter_error_fallback(self):
        """格式化函数异常→兜底通用格式化"""
        entry = self._make_entry(formatter="bad_fmt")
        d = self._make_dispatcher()
        with patch("services.kuaimai.dispatcher.get_formatter") as mock_fmt:
            mock_fmt.return_value = MagicMock(side_effect=Exception("err"))
            result = d._format_response(
                {"list": [{"id": 1}], "total": 1}, entry, "test",
            )
            assert "1" in result

    def test_generic_format_list(self):
        """通用格式化：列表数据"""
        entry = self._make_entry(response_key="list", formatter="__nonexistent__")
        d = self._make_dispatcher()
        data = {"list": [{"name": "A"}, {"name": "B"}], "total": 2}
        result = d._format_response(data, entry, "test")
        assert "2" in result
        assert "A" in result

    def test_generic_format_empty_list(self):
        """通用格式化：空列表"""
        entry = self._make_entry(response_key="list", formatter="__nonexistent__")
        d = self._make_dispatcher()
        result = d._format_response({"list": []}, entry, "test")
        assert "暂无数据" in result

    def test_generic_format_detail(self):
        """通用格式化：非列表响应（详情类）"""
        entry = self._make_entry(response_key=None, formatter="__nonexistent__")
        d = self._make_dispatcher()
        result = d._format_response({"id": 1, "name": "A"}, entry, "test")
        assert "测试接口" in result

    def test_generic_format_non_dict(self):
        """通用格式化：非dict响应"""
        entry = self._make_entry(formatter="__nonexistent__")
        d = self._make_dispatcher()
        result = d._format_response("plain text", entry, "test")
        assert "plain text" in result

    def test_global_char_budget_truncation(self):
        """全局安全网：超长输出按行截断"""
        entry = self._make_entry(
            response_key="list",
            formatter="format_generic_list",
        )
        d = self._make_dispatcher()
        # 构造超长数据
        big_items = [{"name": f"item_{i}", "desc": "x" * 200} for i in range(50)]
        data = {"list": big_items, "total": 50}
        result = d._format_response(data, entry, "test")
        assert len(result) <= d._GLOBAL_CHAR_BUDGET + 50  # 允许尾部提示余量
        assert "截断" in result or len(result) <= d._GLOBAL_CHAR_BUDGET


# ============================================================
# TestDispatcherGuardrails — dispatcher 集成护栏
# ============================================================


class TestDispatcherGuardrails:
    """测试 dispatcher 集成 preprocess_params + diagnose_empty_result"""

    def _make_entry(self, **overrides):
        from services.kuaimai.registry.base import ApiEntry
        defaults = {
            "method": "erp.test.query",
            "description": "测试接口",
            "param_map": {
                "outer_id": "mainOuterId",
                "sku_outer_id": "skuOuterId",
            },
            "response_key": "list",
        }
        defaults.update(overrides)
        return ApiEntry(**defaults)

    def _make_dispatcher(self, client=None):
        from services.kuaimai.dispatcher import ErpDispatcher
        return ErpDispatcher(client or AsyncMock())

    @pytest.mark.asyncio
    async def test_corrections_shown_in_result(self):
        """参数自动纠正记录出现在结果开头（order_id→system_id）"""
        entry = self._make_entry(param_map={
            "system_id": "sid",
            "status": "status",
        })
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "list": [{"id": 1}], "total": 1,
        }
        d = self._make_dispatcher(mock_client)
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_product_query": {"stock_status": entry},
        }):
            result = await d.execute(
                "erp_product_query", "stock_status",
                {"order_id": "5759422420146938"},
            )
            assert "参数自动纠正" in result
            assert "system_id" in result

    @pytest.mark.asyncio
    async def test_no_corrections_when_normal_param(self):
        """正常参数不触发纠正"""
        entry = self._make_entry()
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "list": [{"id": 1}], "total": 1,
        }
        d = self._make_dispatcher(mock_client)
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_product_query": {"stock_status": entry},
        }):
            result = await d.execute(
                "erp_product_query", "stock_status",
                {"outer_id": "ABC123"},
            )
            assert "参数自动纠正" not in result

    @pytest.mark.asyncio
    async def test_diagnosis_suggestion_on_empty_result(self):
        """零结果时诊断建议追加到结果末尾"""
        entry = self._make_entry(
            retry_alt_params={"outer_id": "sku_outer_id"},
            response_key="list",
        )
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "list": [], "total": 0,
        }
        d = self._make_dispatcher(mock_client)
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_product_query": {"stock_status": entry},
        }):
            result = await d.execute(
                "erp_product_query", "stock_status",
                {"outer_id": "ABC123"},
            )
            assert "sku_outer_id" in result
            assert "重试" in result

    @pytest.mark.asyncio
    async def test_no_diagnosis_when_results_found(self):
        """有结果时不生成诊断建议"""
        entry = self._make_entry(
            retry_alt_params={"outer_id": "sku_outer_id"},
        )
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "list": [{"outerId": "ABC123"}], "total": 1,
        }
        d = self._make_dispatcher(mock_client)
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_product_query": {"stock_status": entry},
        }):
            result = await d.execute(
                "erp_product_query", "stock_status",
                {"outer_id": "ABC123"},
            )
            assert "重试" not in result

    @pytest.mark.asyncio
    async def test_broadened_query_replaces_empty_result(self):
        """零结果时宽泛查询命中 → 结果包含匹配数据和说明"""
        entry = self._make_entry(
            retry_alt_params={"outer_id": "sku_outer_id"},
            response_key="stockStatusVoList",
        )
        mock_client = AsyncMock()
        # 第1次调用：初始查询返回空
        # 第2次调用：宽泛查询(outer_id=DBTXL)返回多条
        mock_client.request_with_retry.side_effect = [
            {"stockStatusVoList": [], "total": 0},
            {
                "stockStatusVoList": [
                    {"outerId": "DBTXL01-01", "mainOuterId": "DBTXL"},
                    {"outerId": "DBTXL01-02", "mainOuterId": "DBTXL"},
                ],
                "total": 2,
            },
        ]
        d = self._make_dispatcher(mock_client)
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_product_query": {"stock_status": entry},
        }):
            result = await d.execute(
                "erp_product_query", "stock_status",
                {"outer_id": "DBTXL01-02"},
            )
            assert "编码智能匹配" in result
            assert "DBTXL" in result

    @pytest.mark.asyncio
    async def test_broadened_query_pure_letter_code_still_dual_param(self):
        """纯字母编码仍做双参数依次试（无宽泛打包，但双参数兜底）"""
        entry = self._make_entry(
            retry_alt_params={"outer_id": "sku_outer_id"},
        )
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "list": [], "total": 0,
        }
        d = self._make_dispatcher(mock_client)
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_product_query": {"stock_status": entry},
        }):
            result = await d.execute(
                "erp_product_query", "stock_status",
                {"outer_id": "DBTXL"},
            )
            # 新架构：纯字母也会走双参数依次试
            assert "编码智能匹配" in result
            # diagnose_empty_result 路径仍生效
            assert "重试" in result


# ============================================================
# TestParamMapper — 参数映射
# ============================================================


class TestParamMapper:

    def _make_entry(self, **overrides):
        from services.kuaimai.registry.base import ApiEntry
        defaults = {
            "method": "erp.test",
            "description": "test",
            "param_map": {"order_id": "tid", "start_date": "startTime"},
            "defaults": {"status": "TRADE_FINISHED"},
            "page_size": 20,
        }
        defaults.update(overrides)
        return ApiEntry(**defaults)

    def test_applies_defaults(self):
        """默认值被应用"""
        from services.kuaimai.param_mapper import map_params
        entry = self._make_entry()
        result, warnings = map_params(entry, {})
        assert result["status"] == "TRADE_FINISHED"
        assert warnings == []

    def test_maps_user_params(self):
        """用户参数通过 param_map 映射"""
        from services.kuaimai.param_mapper import map_params
        entry = self._make_entry()
        result, warnings = map_params(entry, {"order_id": "12345"})
        assert result["tid"] == "12345"
        assert warnings == []

    def test_skips_none_values(self):
        """None 值被跳过"""
        from services.kuaimai.param_mapper import map_params
        entry = self._make_entry()
        result, warnings = map_params(entry, {"order_id": None})
        assert "tid" not in result

    def test_default_pagination(self):
        """未指定分页→默认 pageNo=1"""
        from services.kuaimai.param_mapper import map_params
        entry = self._make_entry()
        result, warnings = map_params(entry, {})
        assert result["pageNo"] == 1
        assert result["pageSize"] == 20

    def test_user_page_override(self):
        """用户指定 page→映射到 pageNo"""
        from services.kuaimai.param_mapper import map_params
        entry = self._make_entry()
        result, warnings = map_params(entry, {"page": 3})
        assert result["pageNo"] == 3
        assert warnings == []

    def test_invalid_params_return_warnings(self):
        """无效参数不传入 API，返回警告列表"""
        from services.kuaimai.param_mapper import map_params
        entry = self._make_entry()
        result, warnings = map_params(entry, {"fake_param": "test"})
        assert "fake_param" not in result
        assert "fake_param" in warnings

    def test_mixed_valid_invalid_params(self):
        """有效和无效参数混合：有效映射，无效返回警告"""
        from services.kuaimai.param_mapper import map_params
        entry = self._make_entry()
        result, warnings = map_params(
            entry, {"order_id": "123", "unknown_field": "x"}
        )
        assert result["tid"] == "123"
        assert "unknown_field" in warnings

    def test_normalize_dates_start(self):
        """日期参数补全：start 补 00:00:00"""
        from services.kuaimai.param_mapper import _normalize_dates
        params = {"startTime": "2026-03-01"}
        _normalize_dates(params)
        assert params["startTime"] == "2026-03-01 00:00:00"

    def test_normalize_dates_end(self):
        """日期参数补全：end 补 23:59:59"""
        from services.kuaimai.param_mapper import _normalize_dates
        params = {"endTime": "2026-03-01"}
        _normalize_dates(params)
        assert params["endTime"] == "2026-03-01 23:59:59"

    def test_normalize_dates_skips_full_datetime(self):
        """已有完整时间→不修改"""
        from services.kuaimai.param_mapper import _normalize_dates
        params = {"startTime": "2026-03-01 12:30:00"}
        _normalize_dates(params)
        assert params["startTime"] == "2026-03-01 12:30:00"

    def test_normalize_dates_skips_non_string(self):
        """非字符串值→跳过"""
        from services.kuaimai.param_mapper import _normalize_dates
        params = {"startTime": 12345}
        _normalize_dates(params)
        assert params["startTime"] == 12345



class TestParamAliases:
    """参数别名解析测试"""

    def _make_entry(self, **overrides):
        from services.kuaimai.registry.base import ApiEntry
        defaults = {
            "method": "erp.test",
            "description": "test",
            "param_map": {
                "outer_id": "mainOuterId",
                "sku_outer_id": "skuOuterId",
                "code": "code",
                "order_id": "tid",
            },
            "defaults": {},
            "page_size": 20,
        }
        defaults.update(overrides)
        return ApiEntry(**defaults)

    def test_chinese_alias_resolves(self):
        """中文别名'商品编码'解析为 outer_id"""
        from services.kuaimai.param_mapper import _resolve_aliases, _COMMON_PARAMS
        valid_keys = {"outer_id", "code"} | _COMMON_PARAMS
        result = _resolve_aliases({"商品编码": "ABC123"}, valid_keys)
        assert result == {"outer_id": "ABC123"}

    def test_standard_key_preserved(self):
        """标准参数名不被别名覆盖"""
        from services.kuaimai.param_mapper import _resolve_aliases, _COMMON_PARAMS
        valid_keys = {"outer_id", "code"} | _COMMON_PARAMS
        result = _resolve_aliases(
            {"编码": "A", "outer_id": "B"}, valid_keys,
        )
        assert result["outer_id"] == "B"

    def test_unknown_key_passthrough(self):
        """未知 key 原样保留（进入 warning 流程）"""
        from services.kuaimai.param_mapper import _resolve_aliases, _COMMON_PARAMS
        valid_keys = {"outer_id"} | _COMMON_PARAMS
        result = _resolve_aliases({"未知参数": "X"}, valid_keys)
        assert result == {"未知参数": "X"}

    def test_sku_alias_resolves(self):
        """规格商家编码别名解析为 sku_outer_id"""
        from services.kuaimai.param_mapper import _resolve_aliases, _COMMON_PARAMS
        valid_keys = {"outer_id", "sku_outer_id"} | _COMMON_PARAMS
        result = _resolve_aliases({"规格商家编码": "ABC-01"}, valid_keys)
        assert result == {"sku_outer_id": "ABC-01"}

    def test_map_params_e2e_with_alias(self):
        """端到端：中文别名经 map_params 正确映射到 API 参数"""
        from services.kuaimai.param_mapper import map_params
        entry = self._make_entry()
        result, warnings = map_params(entry, {"商品编码": "HM-2026"})
        assert result["mainOuterId"] == "HM-2026"
        assert warnings == []

    def test_map_params_e2e_barcode_alias(self):
        """端到端：条码别名正确映射"""
        from services.kuaimai.param_mapper import map_params
        entry = self._make_entry()
        result, warnings = map_params(entry, {"条码": "6901234567890"})
        assert result["code"] == "6901234567890"
        assert warnings == []

    def test_map_params_e2e_order_alias(self):
        """端到端：订单号别名正确映射"""
        from services.kuaimai.param_mapper import map_params
        entry = self._make_entry()
        result, warnings = map_params(entry, {"订单号": "123456789012345678"})
        assert result["tid"] == "123456789012345678"
        assert warnings == []


class TestTradeRegistryParamMap:

    def test_order_list_has_time_type_mapping(self):
        """order_list param_map 包含 time_type → timeType 映射"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_list"]
        assert "time_type" in entry.param_map
        assert entry.param_map["time_type"] == "timeType"

    def test_order_list_no_phantom_params(self):
        """order_list param_map 不含 API 无效的幽灵参数"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_list"]
        for phantom in ("shop_name", "outer_id", "receiver_name",
                        "receiver_phone", "warehouse_name"):
            assert phantom not in entry.param_map

    def test_time_type_mapped_correctly(self):
        """time_type 参数通过 param_map 正确映射到 timeType"""
        from services.kuaimai.param_mapper import map_params
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_list"]
        result, warnings = map_params(entry, {"time_type": "created"})
        assert result["timeType"] == "created"
        assert "time_type" not in result
        assert warnings == []


# ============================================================
# TestQimenRegistry — 奇门注册表
# ============================================================


class TestQimenRegistry:

    def test_order_list_entry(self):
        """order_list 注册表配置正确"""
        from services.kuaimai.registry.qimen import QIMEN_REGISTRY
        entry = QIMEN_REGISTRY["order_list"]
        assert entry.method == "kuaimai.order.list.query"
        assert entry.response_key == "trades"
        assert entry.base_url is not None
        assert "target_app_key" in entry.system_params

    def test_refund_list_entry(self):
        """refund_list 注册表配置正确"""
        from services.kuaimai.registry.qimen import QIMEN_REGISTRY
        entry = QIMEN_REGISTRY["refund_list"]
        assert entry.method == "kuaimai.refund.list.query"
        assert entry.response_key == "workOrders"
        assert entry.base_url is not None
        assert entry.defaults.get("asVersion") == 2

    def test_order_param_map(self):
        """order_list param_map 含核心映射"""
        from services.kuaimai.registry.qimen import QIMEN_REGISTRY
        pm = QIMEN_REGISTRY["order_list"].param_map
        assert pm["date_type"] == "dateType"
        assert pm["shop_id"] == "userId"
        assert pm["tid"] == "tid"

    def test_refund_param_map(self):
        """refund_list param_map 含核心映射"""
        from services.kuaimai.registry.qimen import QIMEN_REGISTRY
        pm = QIMEN_REGISTRY["refund_list"].param_map
        assert pm["refund_type"] == "refundType"
        assert pm["refund_id"] == "id"
        assert pm["shop_id"] == "userId"

    def test_qimen_in_tool_registries(self):
        """QIMEN_REGISTRY 已注册到 TOOL_REGISTRIES"""
        from services.kuaimai.registry import TOOL_REGISTRIES
        assert "erp_taobao_query" in TOOL_REGISTRIES


# ============================================================
# TestQimenFormatters — 奇门格式化器
# ============================================================


class TestQimenFormatters:

    def test_order_list_empty(self):
        """空订单列表→提示无数据"""
        from services.kuaimai.formatters.qimen import format_qimen_order_list
        result = format_qimen_order_list({"trades": []}, None)
        assert "未找到" in result

    def test_order_list_with_data(self):
        """有订单→格式化包含关键字段"""
        from services.kuaimai.formatters.qimen import format_qimen_order_list
        data = {
            "total": 1,
            "trades": [{
                "tid": "T001", "sid": "S001",
                "sysStatus": "已审核", "buyerNick": "买家A",
                "payment": "99.00", "shopName": "测试店铺",
                "created": "2026-03-10 10:00:00",
                "payTime": "2026-03-10 10:05:00",
                "type": "0",
            }],
        }
        result = format_qimen_order_list(data, None)
        assert "T001" in result
        assert "买家A" in result
        assert "99.00" in result
        assert "普通" in result

    def test_order_list_with_sub_orders(self):
        """订单含子订单明细"""
        from services.kuaimai.formatters.qimen import format_qimen_order_list
        data = {
            "total": 1,
            "trades": [{
                "tid": "T002", "sid": "S002",
                "sysStatus": "待发货", "payment": "50.00",
                "orders": [{"sysTitle": "商品A", "num": 2, "sysOuterId": "SKU001"}],
            }],
        }
        result = format_qimen_order_list(data, None)
        assert "商品A" in result
        assert "SKU001" in result

    def test_refund_list_empty(self):
        """空售后列表→提示无数据"""
        from services.kuaimai.formatters.qimen import format_qimen_refund_list
        result = format_qimen_refund_list({"workOrders": []}, None)
        assert "未找到" in result

    def test_refund_list_with_data(self):
        """有售后单→格式化包含关键字段"""
        from services.kuaimai.formatters.qimen import format_qimen_refund_list
        data = {
            "total": 1,
            "workOrders": [{
                "id": "WO001", "tid": "T001", "sid": "S001",
                "shopName": "测试店铺",
                "afterSaleType": 2, "status": 9,
                "refundMoney": 88.5,
                "textReason": "质量问题",
                "created": "2026-03-10 12:00:00",
            }],
        }
        result = format_qimen_refund_list(data, None)
        assert "WO001" in result
        assert "退货" in result
        assert "处理完成" in result
        assert "88.5" in result
        assert "质量问题" in result

    def test_order_type_mapping(self):
        """订单类型映射覆盖常见类型"""
        from services.kuaimai.formatters.qimen import _ORDER_TYPE_MAP
        assert _ORDER_TYPE_MAP["7"] == "合并"
        assert _ORDER_TYPE_MAP["8"] == "拆分"
        assert _ORDER_TYPE_MAP["33"] == "分销"

    def test_refund_type_mapping(self):
        """售后类型映射覆盖5种类型"""
        from services.kuaimai.formatters.qimen import _REFUND_TYPE_MAP
        assert len(_REFUND_TYPE_MAP) == 5
        assert _REFUND_TYPE_MAP[1] == "退款"
        assert _REFUND_TYPE_MAP[4] == "换货"

    def test_refund_status_map_covers_10_states(self):
        """售后工单状态映射覆盖10种状态"""
        from services.kuaimai.formatters.qimen import _REFUND_STATUS_MAP
        assert len(_REFUND_STATUS_MAP) == 10
        assert _REFUND_STATUS_MAP[1] == "未分配"
        assert _REFUND_STATUS_MAP[9] == "处理完成"
        assert _REFUND_STATUS_MAP[10] == "作废"

    def test_order_list_pagination_hint(self):
        """total > items 时显示分页提示"""
        from services.kuaimai.formatters.qimen import format_qimen_order_list
        data = {
            "total": 50,
            "trades": [{"tid": f"T{i}", "sysStatus": "ok"} for i in range(20)],
        }
        result = format_qimen_order_list(data, None)
        assert "共找到 50 条" in result
        assert "显示前20条" in result
        assert "共50条" in result

    def test_refund_list_with_items(self):
        """售后工单含商品明细"""
        from services.kuaimai.formatters.qimen import format_qimen_refund_list
        data = {
            "total": 1,
            "workOrders": [{
                "id": "WO002", "tid": "T002", "sid": "S002",
                "afterSaleType": 3, "status": 4,
                "refundMoney": 30,
                "items": [
                    {"title": "退货商品A", "receivableCount": 1, "outerId": "RET001"},
                ],
            }],
        }
        result = format_qimen_refund_list(data, None)
        assert "退货商品A" in result
        assert "RET001" in result
        assert "补发" in result

    def test_formatters_registered(self):
        """奇门格式化器已注册到全局"""
        from services.kuaimai.formatters import get_formatter
        assert get_formatter("format_qimen_order_list") is not None
        assert get_formatter("format_qimen_refund_list") is not None


# ============================================================
# TestBuildGatewayParams — 网关参数构建
# ============================================================


class TestBuildGatewayParams:

    def _make_entry(self, **overrides):
        from services.kuaimai.registry.base import ApiEntry
        defaults = {
            "method": "test.method",
            "description": "test",
        }
        defaults.update(overrides)
        return ApiEntry(**defaults)

    def test_normal_entry_returns_none(self):
        """普通ERP条目（无base_url）→返回 (None, None)"""
        from services.kuaimai.dispatcher import ErpDispatcher
        entry = self._make_entry()
        base_url, sys_params = ErpDispatcher._build_gateway_params(entry)
        assert base_url is None
        assert sys_params is None

    def test_qimen_entry_returns_gateway(self):
        """奇门条目→返回网关地址和系统参数"""
        from services.kuaimai.dispatcher import ErpDispatcher
        entry = self._make_entry(
            base_url="http://test.api.taobao.com/router/qm",
            system_params={"target_app_key": "23204092"},
        )
        with patch("core.config.settings") as mock_settings:
            mock_settings.qimen_customer_id = "65109"
            base_url, sys_params = ErpDispatcher._build_gateway_params(entry)
            assert base_url == "http://test.api.taobao.com/router/qm"
            assert sys_params["target_app_key"] == "23204092"
            assert sys_params["customerId"] == "65109"

    def test_qimen_entry_no_customer_id(self):
        """奇门条目但无 customerId→不含 customerId"""
        from services.kuaimai.dispatcher import ErpDispatcher
        entry = self._make_entry(
            base_url="http://test.api.taobao.com/router/qm",
            system_params={"target_app_key": "23204092"},
        )
        with patch("core.config.settings") as mock_settings:
            mock_settings.qimen_customer_id = None
            base_url, sys_params = ErpDispatcher._build_gateway_params(entry)
            assert "customerId" not in sys_params

    @pytest.mark.asyncio
    async def test_dispatcher_passes_gateway_params(self):
        """Dispatcher.execute 对奇门条目传递网关参数"""
        from services.kuaimai.dispatcher import ErpDispatcher
        from services.kuaimai.registry.base import ApiEntry
        entry = ApiEntry(
            method="kuaimai.order.list.query",
            description="淘宝订单",
            base_url="http://test.taobao.com/router/qm",
            system_params={"target_app_key": "23204092"},
            response_key="trades",
        )
        mock_client = AsyncMock()
        mock_client.request_with_retry.return_value = {
            "trades": [{"tid": "T1"}], "total": 1,
        }
        d = ErpDispatcher(mock_client)
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_taobao_query": {"order_list": entry},
        }), patch("core.config.settings") as mock_settings:
            mock_settings.qimen_customer_id = "65109"
            await d.execute("erp_taobao_query", "order_list", {})
            call_kwargs = mock_client.request_with_retry.call_args
            assert call_kwargs.kwargs["base_url"] == "http://test.taobao.com/router/qm"
            assert call_kwargs.kwargs["extra_system_params"]["customerId"] == "65109"


# ============================================================
# TestFormatActionDesc — 丰富 action 描述
# ============================================================


class TestFormatActionDesc:

    def test_with_params(self):
        """有参数时生成 name=描述(参数列表)"""
        from config.erp_tools import _format_action_desc
        from services.kuaimai.registry.base import ApiEntry
        entry = ApiEntry(
            method="test.method",
            description="测试操作",
            param_map={"order_id": "tid", "status": "status"},
            required_params=["order_id"],
        )
        result = _format_action_desc("test_action", entry)
        assert "test_action=" in result
        assert "测试操作" in result
        assert "*order_id" in result  # 必填标记
        assert "status" in result
        assert "*status" not in result  # 非必填无标记

    def test_without_params(self):
        """无参数时不加括号"""
        from config.erp_tools import _format_action_desc
        from services.kuaimai.registry.base import ApiEntry
        entry = ApiEntry(
            method="test.method",
            description="无参操作",
            param_map={},
        )
        result = _format_action_desc("simple", entry)
        assert result == "simple=无参操作"
        assert "(" not in result

    def test_all_required(self):
        """全部必填参数都有 * 前缀"""
        from config.erp_tools import _format_action_desc
        from services.kuaimai.registry.base import ApiEntry
        entry = ApiEntry(
            method="test.method",
            description="必填测试",
            param_map={"a": "A", "b": "B"},
            required_params=["a", "b"],
        )
        result = _format_action_desc("req", entry)
        assert "*a" in result
        assert "*b" in result


class TestRecordParamKnowledge:

    def test_missing_params_triggers_knowledge(self):
        """缺少必填参数→触发知识记录"""
        from services.kuaimai.dispatcher import ErpDispatcher
        mock_task = MagicMock()
        with patch("services.kuaimai.dispatcher.asyncio.create_task", mock_task), \
             patch(
                 "services.knowledge_extractor.extract_and_save",
                 new_callable=AsyncMock,
             ):
            ErpDispatcher._record_param_knowledge(
                "erp_trade_query", "order_list",
                "缺少必填参数: 订单号",
            )
            mock_task.assert_called_once()

    def test_invalid_params_triggers_knowledge(self):
        """无效参数→触发知识记录"""
        from services.kuaimai.dispatcher import ErpDispatcher
        mock_task = MagicMock()
        with patch("services.kuaimai.dispatcher.asyncio.create_task", mock_task), \
             patch(
                 "services.knowledge_extractor.extract_and_save",
                 new_callable=AsyncMock,
             ):
            ErpDispatcher._record_param_knowledge(
                "erp_trade_query", "order_list",
                "无效参数: fake_param",
            )
            mock_task.assert_called_once()

    def test_import_error_silenced(self):
        """import 失败→静默跳过"""
        from services.kuaimai.dispatcher import ErpDispatcher
        with patch.dict("sys.modules", {"services.knowledge_extractor": None}):
            # 不应抛异常
            ErpDispatcher._record_param_knowledge(
                "tool", "action", "error",
            )


class TestBuildErpSearchTool:

    def test_tool_structure(self):
        """build_erp_search_tool 返回合法的工具定义"""
        from config.erp_tools import build_erp_search_tool
        tool = build_erp_search_tool()
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "erp_api_search"
        params = tool["function"]["parameters"]
        assert "query" in params["properties"]
        assert "query" in params["required"]


# ============================================================
# TestAllRegistryStructure — 全注册表结构验证
# ============================================================


class TestAllRegistryStructure:
    """验证所有 registry 文件的 ApiEntry 结构正确性"""

    @staticmethod
    def _all_registries():
        from services.kuaimai.registry import (
            BASIC_REGISTRY, PRODUCT_REGISTRY, TRADE_REGISTRY,
            AFTERSALES_REGISTRY, WAREHOUSE_REGISTRY,
            PURCHASE_REGISTRY, DISTRIBUTION_REGISTRY, QIMEN_REGISTRY,
        )
        return {
            "basic": BASIC_REGISTRY,
            "product": PRODUCT_REGISTRY,
            "trade": TRADE_REGISTRY,
            "aftersales": AFTERSALES_REGISTRY,
            "warehouse": WAREHOUSE_REGISTRY,
            "purchase": PURCHASE_REGISTRY,
            "distribution": DISTRIBUTION_REGISTRY,
            "qimen": QIMEN_REGISTRY,
        }

    def test_all_entries_have_method(self):
        """每个 ApiEntry 都有 method 字段"""
        for cat, reg in self._all_registries().items():
            for name, entry in reg.items():
                assert entry.method, f"{cat}.{name} 缺少 method"

    def test_all_entries_have_description(self):
        """每个 ApiEntry 都有 description"""
        for cat, reg in self._all_registries().items():
            for name, entry in reg.items():
                assert entry.description, f"{cat}.{name} 缺少 description"

    def test_required_params_subset_of_param_map(self):
        """required_params 中的 key 必须在 param_map 中存在"""
        for cat, reg in self._all_registries().items():
            for name, entry in reg.items():
                pm_keys = set(entry.param_map.keys())
                for rp in entry.required_params:
                    assert rp in pm_keys, (
                        f"{cat}.{name}: required_param '{rp}' "
                        f"不在 param_map {pm_keys} 中"
                    )

    def test_formatters_exist(self):
        """每个 entry 的 formatter 在 _FORMATTER_REGISTRY 中能找到"""
        from services.kuaimai.formatters import _FORMATTER_REGISTRY
        for cat, reg in self._all_registries().items():
            for name, entry in reg.items():
                assert entry.formatter in _FORMATTER_REGISTRY, (
                    f"{cat}.{name}: formatter '{entry.formatter}' "
                    f"不在 _FORMATTER_REGISTRY 中"
                )

    def test_write_entries_have_is_write_true(self):
        """写操作 entry 的 is_write 必须为 True"""
        write_keywords = ["create", "add", "update", "save", "cancel",
                          "delete", "revert", "out", "receive", "halt",
                          "unhalt", "intercept", "consign", "pack",
                          "seed", "generate", "validate", "change",
                          "import", "resolve", "process", "pay",
                          "un_audit", "anti_audit", "submit"]
        for cat, reg in self._all_registries().items():
            for name, entry in reg.items():
                if entry.is_write:
                    # 写操作的 response_key 应为 None
                    assert entry.response_key is None, (
                        f"{cat}.{name}: 写操作 response_key 应为 None"
                    )

    def test_date_params_in_normalize_keys(self):
        """param_map 中映射到的日期参数必须被 _normalize_dates 覆盖"""
        from services.kuaimai.param_mapper import _normalize_dates
        # 收集所有 _normalize_dates 支持的 key
        test_params = {}
        # 通过一个足够大的字典来探测支持的 key
        candidate_keys = [
            "startTime", "endTime",
            "startModified", "endModified",
            "startApplyTime", "endApplyTime",
            "startFinished", "endFinished",
            "timeBegin", "timeEnd",
            "startCreated", "endCreated",
            "pickStartTime", "pickEndTime",
            "timeStart",
            "modifiedStart", "modifiedEnd",
            "productTimeStart", "productTimeEnd",
            "finishedTimeStart", "finishedTimeEnd",
            "createdStart", "createdEnd",
            "operateStartTime", "operateEndTime",
            "modifiedTimeStart", "modifiedTimeEnd",
            "updateTimeBegin", "updateTimeEnd",
            "operateTimeBegin", "operateTimeEnd",
            "operateTimeStart",
            "receiveTimeStart", "receiveTimeEnd",
            "createStart", "createEnd",
            "receivedTime", "operatorTime",
            "startStockModified", "endStockModified",
        ]
        for k in candidate_keys:
            test_params[k] = "2026-01-01"
        _normalize_dates(test_params)
        supported_keys = {
            k for k, v in test_params.items()
            if v != "2026-01-01"  # 被修改 = 被支持
        }

        # 检查每个 registry 中映射到日期相关的 API 参数
        date_indicators = ["start", "end", "time", "modified", "begin",
                           "created", "finished", "operate", "update"]
        for cat, reg in self._all_registries().items():
            for name, entry in reg.items():
                for user_key, api_key in entry.param_map.items():
                    # 判断是否像日期参数
                    lower = api_key.lower()
                    is_date_like = any(d in lower for d in date_indicators)
                    # 排除不是日期的参数（如 timeType, activeStatus 等）
                    exclude = ["type", "status", "begin", "number",
                               "start", "end"]
                    if api_key in ("timeType", "activeStatus", "customType",
                                   "operateType", "updateStart", "updateEnd",
                                   "consignStart", "consignEnd"):
                        continue
                    if is_date_like and len(api_key) > 6:
                        # 只检查看起来明确是日期时间的 key
                        if api_key.endswith(("Time", "Modified",
                                             "Start", "End",
                                             "Begin")):
                            assert api_key in supported_keys, (
                                f"{cat}.{name}: 日期参数 '{api_key}' "
                                f"(来自 {user_key}) 不在 _normalize_dates "
                                f"支持列表中"
                            )

    def test_no_duplicate_methods_in_same_registry(self):
        """同一 registry 中不应有重复 method"""
        for cat, reg in self._all_registries().items():
            methods = [e.method for e in reg.values()]
            dups = [m for m in methods if methods.count(m) > 1]
            assert not dups, f"{cat}: 重复 method {set(dups)}"


# ============================================================
# TestRegistryParamCompleteness — 参数完整性校验
# ============================================================


class TestRegistryParamCompleteness:
    """注册表参数完整性校验（官方文档对齐后新增）"""

    @staticmethod
    def _all_registries():
        from services.kuaimai.registry import (
            BASIC_REGISTRY, PRODUCT_REGISTRY, TRADE_REGISTRY,
            AFTERSALES_REGISTRY, WAREHOUSE_REGISTRY,
            PURCHASE_REGISTRY, DISTRIBUTION_REGISTRY, QIMEN_REGISTRY,
        )
        return {
            "basic": BASIC_REGISTRY,
            "product": PRODUCT_REGISTRY,
            "trade": TRADE_REGISTRY,
            "aftersales": AFTERSALES_REGISTRY,
            "warehouse": WAREHOUSE_REGISTRY,
            "purchase": PURCHASE_REGISTRY,
            "distribution": DISTRIBUTION_REGISTRY,
            "qimen": QIMEN_REGISTRY,
        }

    def test_detail_actions_have_id_param(self):
        """所有 detail/get 操作必须有 ID 类参数"""
        # 排除已知无需ID的查询（如 classify_list、cat_list 等无参数查询）
        skip = {
            "product.cat_list", "product.classify_list", "product.brand_list",
        }
        for cat, reg in self._all_registries().items():
            for name, entry in reg.items():
                key = f"{cat}.{name}"
                if key in skip:
                    continue
                # 只检查名字含 detail 或 method 含 .get 的
                is_detail = "detail" in name or (
                    entry.method.endswith(".get")
                    and not entry.method.endswith("list.get")
                )
                if not is_detail:
                    continue
                assert entry.param_map, (
                    f"{key}: detail/get 操作必须有 param_map 来传递 ID"
                )

    def test_write_actions_have_param_map(self):
        """所有写操作必须有非空 param_map（排除已知复杂JSON的）"""
        # 这些API参数结构复杂（嵌套JSON），通过raw JSON传参
        skip = {
            "trade.wave_pick_hand", "trade.wave_seed",
            "trade.order_create_new",
            "purchase.purchase_add", "purchase.purchase_status_update",
            "purchase.purchase_un_audit", "purchase.purchase_return_out",
            "purchase.warehouse_entry_receive",
            "purchase.warehouse_entry_fast_receive",
            "purchase.warehouse_entry_add_update",
            "purchase.shelf_save",
            "purchase.pre_in_order_add", "purchase.pre_in_order_update",
            "purchase.pre_in_order_anti_audit",
            "warehouse.allocate_add", "warehouse.allocate_create",
            "warehouse.allocate_out_direct",
            "warehouse.inventory_batch_update",
            "warehouse.unshelve_save", "warehouse.goods_section_delete",
            "warehouse.upshelf_batch",
            "product.product_add_update", "product.import_platform_item",
            "product.supplier_update", "product.supplier_modify",
            "product.cat_add", "product.classify_add",
            "product.virtual_stock_batch_update", "product.item_type_change",
        }
        for cat, reg in self._all_registries().items():
            for name, entry in reg.items():
                key = f"{cat}.{name}"
                if key in skip:
                    continue
                if entry.is_write:
                    assert entry.param_map, (
                        f"{key}: 写操作必须有 param_map"
                    )


# ============================================================
# TestRegistrySpecificEntries — 关键 entry 参数验证
# ============================================================


class TestRegistrySpecificEntries:
    """验证本次修改的关键 entry 参数映射"""

    # ── trade.py ──────────────────────────────────────────

    def test_upload_memo_flag_has_memo_and_flag(self):
        """upload_memo_flag 必须有 memo/flag/userId 参数"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["upload_memo_flag"]
        assert entry.param_map["memo"] == "memo"
        assert entry.param_map["flag"] == "flag"
        assert entry.param_map["shop_id"] == "userId"
        assert "order_id" in entry.required_params

    def test_wave_sorting_query_requires_wave_id(self):
        """wave_sorting_query 的 waveId 是必填"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["wave_sorting_query"]
        assert entry.param_map["wave_id"] == "waveId"
        assert "wave_id" in entry.required_params

    def test_order_list_has_shop_ids(self):
        """order_list 支持 shop_ids → userIds"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_list"]
        assert entry.param_map["shop_ids"] == "userIds"

    def test_order_list_has_order_types(self):
        """order_list 支持 order_types → types"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_list"]
        assert entry.param_map["order_types"] == "types"

    def test_order_list_has_query_type(self):
        """order_list 支持 query_type → queryType"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_list"]
        assert entry.param_map["query_type"] == "queryType"

    def test_order_log_uses_sids(self):
        """order_log 使用 sids（复数）"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_log"]
        assert entry.param_map["system_ids"] == "sids"

    # ── aftersales.py ─────────────────────────────────────

    def test_aftersale_list_defaults_asversion_2(self):
        """aftersale_list 默认 asVersion=2"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["aftersale_list"]
        assert entry.defaults.get("asVersion") == 2

    def test_aftersale_list_has_user_ids(self):
        """aftersale_list 支持 shop_ids → userIds"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["aftersale_list"]
        assert entry.param_map["shop_ids"] == "userIds"

    def test_workorder_cancel_uses_workOrderIds(self):
        """workorder_cancel 使用 workOrderIds（非 workOrderNo）"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["workorder_cancel"]
        assert entry.param_map["work_order_ids"] == "workOrderIds"
        assert "work_order_ids" in entry.required_params

    def test_workorder_resolve_uses_workOrderIds(self):
        """workorder_resolve 使用 workOrderIds"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["workorder_resolve"]
        assert entry.param_map["work_order_ids"] == "workOrderIds"

    def test_workorder_tag_update_has_params(self):
        """workorder_tag_update 有 type/workOrderId/tagNames 参数"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["workorder_tag_update"]
        assert entry.param_map["type"] == "type"
        assert entry.param_map["work_order_id"] == "workOrderId"
        assert "type" in entry.required_params
        assert "work_order_id" in entry.required_params

    def test_refund_warehouse_has_wangwang(self):
        """refund_warehouse 支持 wangwang → wangwangNum"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["refund_warehouse"]
        assert entry.param_map["wangwang"] == "wangwangNum"

    # ── purchase.py ───────────────────────────────────────

    def test_purchase_order_history_requires_dates(self):
        """purchase_order_history 的 start_date/end_date 是必填"""
        from services.kuaimai.registry import PURCHASE_REGISTRY
        entry = PURCHASE_REGISTRY["purchase_order_history"]
        assert "start_date" in entry.required_params
        assert "end_date" in entry.required_params

    def test_purchase_order_list_uses_code(self):
        """purchase_order_list 使用 code（非 purchaseNo）"""
        from services.kuaimai.registry import PURCHASE_REGISTRY
        entry = PURCHASE_REGISTRY["purchase_order_list"]
        assert entry.param_map["code"] == "code"

    def test_purchase_order_detail_uses_id(self):
        """purchase_order_detail 使用 id（非 purchaseId）"""
        from services.kuaimai.registry import PURCHASE_REGISTRY
        entry = PURCHASE_REGISTRY["purchase_order_detail"]
        assert entry.param_map["purchase_id"] == "id"

    # ── product.py ────────────────────────────────────────

    def test_stock_in_out_no_biz_type(self):
        """stock_in_out 不应有 biz_type（已移除）"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["stock_in_out"]
        assert "biz_type" not in entry.param_map

    def test_stock_in_out_has_order_type(self):
        """stock_in_out 使用 order_type → orderType"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["stock_in_out"]
        assert entry.param_map["order_type"] == "orderType"

    def test_stock_in_out_time_params(self):
        """stock_in_out 时间参数使用 operateTimeBegin/End"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["stock_in_out"]
        assert entry.param_map["start_date"] == "operateTimeBegin"
        assert entry.param_map["end_date"] == "operateTimeEnd"

    def test_history_cost_price_requires_ids(self):
        """history_cost_price 的 item_id 和 sku_id 是必填"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["history_cost_price"]
        assert "item_id" in entry.required_params
        assert "sku_id" in entry.required_params

    # ── distribution.py ───────────────────────────────────

    def test_distribution_entries_have_param_maps(self):
        """distribution 所有查询 entry 都有 param_map"""
        from services.kuaimai.registry import DISTRIBUTION_REGISTRY
        query_entries = [
            "distributor_item_list", "distributor_item_detail",
            "supplier_view_item_list", "supplier_view_item_detail",
            "distributor_list",
        ]
        for name in query_entries:
            entry = DISTRIBUTION_REGISTRY[name]
            assert entry.param_map, (
                f"distribution.{name} param_map 不应为空"
            )

    def test_distributor_item_list_requires_ids(self):
        """distributor_item_list 必填 distributor/supplier company ID"""
        from services.kuaimai.registry import DISTRIBUTION_REGISTRY
        entry = DISTRIBUTION_REGISTRY["distributor_item_list"]
        assert "distributor_company_id" in entry.required_params
        assert "supplier_company_id" in entry.required_params

    # ── warehouse.py ──────────────────────────────────────

    def test_allocate_list_uses_code(self):
        """allocate_list 使用 code（非 allocateNo）"""
        from services.kuaimai.registry import WAREHOUSE_REGISTRY
        entry = WAREHOUSE_REGISTRY["allocate_list"]
        assert entry.param_map["code"] == "code"

    def test_allocate_list_time_params(self):
        """allocate_list 使用 startModified/endModified"""
        from services.kuaimai.registry import WAREHOUSE_REGISTRY
        entry = WAREHOUSE_REGISTRY["allocate_list"]
        assert entry.param_map["start_date"] == "startModified"
        assert entry.param_map["end_date"] == "endModified"


# ============================================================
# TestNewDateKeys — 新增日期 key 归一化
# ============================================================


class TestNewDateKeys:
    """验证新增的 16 个日期 key 能被 _normalize_dates 正确处理"""

    def _check_start(self, key):
        from services.kuaimai.param_mapper import _normalize_dates
        params = {key: "2026-03-14"}
        _normalize_dates(params)
        assert params[key] == "2026-03-14 00:00:00", (
            f"{key} 应补全为 00:00:00"
        )

    def _check_end(self, key):
        from services.kuaimai.param_mapper import _normalize_dates
        params = {key: "2026-03-14"}
        _normalize_dates(params)
        assert params[key] == "2026-03-14 23:59:59", (
            f"{key} 应补全为 23:59:59"
        )

    def test_timeStart(self):
        self._check_start("timeStart")

    def test_timeEnd(self):
        self._check_end("timeEnd")

    def test_modifiedStart(self):
        self._check_start("modifiedStart")

    def test_modifiedEnd(self):
        self._check_end("modifiedEnd")

    def test_productTimeStart(self):
        self._check_start("productTimeStart")

    def test_productTimeEnd(self):
        self._check_end("productTimeEnd")

    def test_finishedTimeStart(self):
        self._check_start("finishedTimeStart")

    def test_finishedTimeEnd(self):
        self._check_end("finishedTimeEnd")

    def test_createdStart(self):
        self._check_start("createdStart")

    def test_createdEnd(self):
        self._check_end("createdEnd")

    def test_operateStartTime(self):
        self._check_start("operateStartTime")

    def test_operateEndTime(self):
        self._check_end("operateEndTime")

    def test_modifiedTimeStart(self):
        self._check_start("modifiedTimeStart")

    def test_modifiedTimeEnd(self):
        self._check_end("modifiedTimeEnd")

    def test_updateTimeBegin(self):
        self._check_start("updateTimeBegin")

    def test_updateTimeEnd(self):
        self._check_end("updateTimeEnd")

    def test_operateTimeBegin(self):
        self._check_start("operateTimeBegin")

    def test_operateTimeEnd(self):
        self._check_end("operateTimeEnd")

    def test_startStockModified(self):
        self._check_start("startStockModified")

    def test_endStockModified(self):
        self._check_end("endStockModified")


# ============================================================
# TestBuildErpTools — 工具定义完整性
# ============================================================


class TestBuildErpTools:
    """验证 build_erp_tools 生成的工具定义结构"""

    def test_returns_19_tools(self):
        """build_erp_tools 返回 20 个工具（8 API + 12 本地）"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        assert len(tools) == 20

    def test_all_query_tools_have_page_size(self):
        """API 两步查询工具都有 page_size 参数"""
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        from config.erp_tools import build_erp_tools
        skip = {"erp_execute"} | ERP_LOCAL_TOOLS
        tools = build_erp_tools()
        query_tools = [t for t in tools
                       if t["function"]["name"] not in skip]
        for tool in query_tools:
            props = tool["function"]["parameters"]["properties"]
            assert "page_size" in props, (
                f"{tool['function']['name']} 缺少 page_size"
            )

    def test_all_query_tools_have_page(self):
        """API 两步查询工具都有 page 参数"""
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        from config.erp_tools import build_erp_tools
        skip = {"erp_execute"} | ERP_LOCAL_TOOLS
        tools = build_erp_tools()
        query_tools = [t for t in tools
                       if t["function"]["name"] not in skip]
        for tool in query_tools:
            props = tool["function"]["parameters"]["properties"]
            assert "page" in props, (
                f"{tool['function']['name']} 缺少 page"
            )

    def test_query_tools_have_params_object(self):
        """API 两步查询工具有 params: object 参数"""
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        from config.erp_tools import build_erp_tools
        skip = {"erp_execute"} | ERP_LOCAL_TOOLS
        tools = build_erp_tools()
        query_tools = [t for t in tools
                       if t["function"]["name"] not in skip]
        for tool in query_tools:
            props = tool["function"]["parameters"]["properties"]
            assert "params" in props, (
                f"{tool['function']['name']} 缺少 params"
            )
            assert props["params"]["type"] == "object"

    def test_trade_action_desc_has_key_params(self):
        """erp_trade_query action 描述包含关键参数名"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        trade_tool = [t for t in tools
                      if t["function"]["name"] == "erp_trade_query"][0]
        action_desc = trade_tool["function"]["parameters"][
            "properties"]["action"]["description"]
        # action 描述包含 shop_ids/order_types 等关键参数
        assert "shop_ids" in action_desc
        assert "order_types" in action_desc

    def test_execute_tool_has_category_enum(self):
        """erp_execute 有 category enum"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        exec_tool = [t for t in tools
                     if t["function"]["name"] == "erp_execute"][0]
        props = exec_tool["function"]["parameters"]["properties"]
        assert "category" in props
        assert "enum" in props["category"]

    def test_action_desc_includes_required_markers(self):
        """action 描述中包含 * 标记必填参数"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        trade_tool = [t for t in tools
                      if t["function"]["name"] == "erp_trade_query"][0]
        action_desc = trade_tool["function"]["parameters"][
            "properties"]["action"]["description"]
        # wave_sorting_query 有 required wave_id
        assert "*wave_id" in action_desc


# ============================================================
# TestFormatShopList — 店铺列表格式化
# ============================================================


class TestFormatShopList:
    """验证 format_shop_list 输出包含 ID"""

    def test_empty_list(self):
        """空列表→提示无数据"""
        from services.kuaimai.formatters.basic import format_shop_list
        result = format_shop_list({"list": []}, None)
        assert "暂无" in result

    def test_includes_shop_id(self):
        """输出包含店铺编码（API 返回 userId 字段）"""
        from services.kuaimai.formatters.basic import format_shop_list
        data = {
            "list": [
                {"title": "京东旗舰店", "userId": 12345,
                 "source": "jd", "state": 3},
            ]
        }
        result = format_shop_list(data, None)
        assert "店铺编码: 12345" in result
        assert "京东旗舰店" in result

    def test_includes_platform(self):
        """输出包含平台信息"""
        from services.kuaimai.formatters.basic import format_shop_list
        data = {
            "list": [
                {"name": "天猫店", "id": 100, "source": "tmall",
                 "active": 1},
            ]
        }
        result = format_shop_list(data, None)
        assert "平台: tmall" in result

    def test_multiple_shops(self):
        """多个店铺全部输出"""
        from services.kuaimai.formatters.basic import format_shop_list
        data = {
            "list": [
                {"title": "店铺A", "state": 3},
                {"title": "店铺B", "state": 1},
            ]
        }
        result = format_shop_list(data, None)
        assert "共 2 个店铺" in result
        assert "店铺A" in result
        assert "店铺B" in result
        assert "停用" in result


# ============================================================
# 本次 param_map 全量对齐 — 补充覆盖测试
# ============================================================


class TestTradeFieldNameFixes:
    """trade.py: 8个API关键字段名修正验证"""

    def _reg(self):
        from services.kuaimai.registry import TRADE_REGISTRY
        return TRADE_REGISTRY

    def test_express_query_no_tid(self):
        """express_query 不应有 tid（官方只有 sid/outSid）"""
        entry = self._reg()["express_query"]
        api_values = set(entry.param_map.values())
        assert "tid" not in api_values
        assert entry.param_map["system_id"] == "sid"
        assert entry.param_map["express_no"] == "outSid"

    def test_order_cancel_uses_plural(self):
        """order_cancel 使用复数 tids/sids"""
        entry = self._reg()["order_cancel"]
        assert entry.param_map["order_ids"] == "tids"
        assert entry.param_map["system_ids"] == "sids"

    def test_order_intercept_uses_sids(self):
        """order_intercept 只有 sids（无 tid）"""
        entry = self._reg()["order_intercept"]
        assert entry.param_map["system_ids"] == "sids"
        api_values = set(entry.param_map.values())
        assert "tid" not in api_values

    def test_trade_consign_uses_sids_and_new_params(self):
        """trade_consign 使用 sids + consignType/expressCode/outSid/operateType"""
        entry = self._reg()["trade_consign"]
        assert entry.param_map["system_ids"] == "sids"
        assert entry.param_map["consign_type"] == "consignType"
        assert entry.param_map["express_code"] == "expressCode"
        assert entry.param_map["express_no"] == "outSid"
        assert entry.param_map["operate_type"] == "operateType"

    def test_change_warehouse_has_required_params(self):
        """change_warehouse: sids/warehouseId 必填 + force 可选"""
        entry = self._reg()["change_warehouse"]
        assert entry.param_map["system_ids"] == "sids"
        assert entry.param_map["warehouse_id"] == "warehouseId"
        assert entry.param_map["force"] == "force"
        assert "system_ids" in entry.required_params
        assert "warehouse_id" in entry.required_params

    def test_order_remark_update_uses_orderId(self):
        """order_remark_update 使用 orderId（非 tid）"""
        entry = self._reg()["order_remark_update"]
        assert entry.param_map["order_id"] == "orderId"
        assert "order_id" in entry.required_params
        assert "remark" in entry.required_params

    def test_trade_halt_uses_plural_and_required(self):
        """trade_halt: tids/sids(复数) + autoUnHalt(必填)"""
        entry = self._reg()["trade_halt"]
        assert entry.param_map["order_ids"] == "tids"
        assert entry.param_map["system_ids"] == "sids"
        assert entry.param_map["auto_unhalt"] == "autoUnHalt"
        assert "auto_unhalt" in entry.required_params
        # 可选参数
        assert entry.param_map["unhalt_type"] == "unHaltType"
        assert entry.param_map["halt_time_unit"] == "haltTimeUnit"
        assert entry.param_map["is_urgent"] == "isUrgent"

    def test_trade_unhalt_uses_plural(self):
        """trade_unhalt 使用复数 tids/sids"""
        entry = self._reg()["trade_unhalt"]
        assert entry.param_map["order_ids"] == "tids"
        assert entry.param_map["system_ids"] == "sids"

    def test_receiver_update_has_5_required(self):
        """receiver_update: 5个收货信息必填"""
        entry = self._reg()["receiver_update"]
        required = set(entry.required_params)
        assert {"receiver_name", "receiver_state", "receiver_city",
                "receiver_district", "receiver_address"}.issubset(required)
        assert entry.param_map["receiver_mobile"] == "receiverMobile"

    def test_order_create_has_10_required(self):
        """order_create: 10个必填参数"""
        entry = self._reg()["order_create"]
        assert len(entry.required_params) >= 10
        assert "shop_id" in entry.required_params
        assert "warehouse_id" in entry.required_params
        assert "orders" in entry.required_params
        assert "payment" in entry.required_params
        assert entry.param_map["buyer"] == "buyerNick"
        assert entry.param_map["post_fee"] == "postFee"

    def test_seller_memo_update_has_data_fields(self):
        """seller_memo_update 有 sellerMemo/flag 参数"""
        entry = self._reg()["seller_memo_update"]
        assert entry.param_map["seller_memo"] == "sellerMemo"
        assert entry.param_map["flag"] == "flag"
        assert entry.param_map["order_id"] == "tid"

    def test_tag_batch_update_has_required(self):
        """tag_batch_update: tagIds/type 必填"""
        entry = self._reg()["tag_batch_update"]
        assert entry.param_map["tag_ids"] == "tagIds"
        assert entry.param_map["type"] == "type"
        assert "tag_ids" in entry.required_params
        assert "type" in entry.required_params

    def test_unique_code_query_has_new_params(self):
        """unique_code_query 补全的8个新参数"""
        entry = self._reg()["unique_code_query"]
        assert entry.param_map["short_ids"] == "shortIds"
        assert entry.param_map["after_sale_codes"] == "afterSaleOrderCodes"
        assert entry.param_map["wave_id"] == "waveId"
        assert entry.param_map["receive_start"] == "receiveTimeStart"
        assert entry.param_map["receive_end"] == "receiveTimeEnd"
        assert entry.param_map["create_start"] == "createStart"
        assert entry.param_map["create_end"] == "createEnd"
        assert entry.param_map["no_need_total"] == "noNeedTotal"

    def test_order_list_has_cursor_params(self):
        """order_list 补全的光标参数（warehouseName 已确认无效已移除）"""
        entry = self._reg()["order_list"]
        assert entry.param_map["use_has_next"] == "useHasNext"
        assert entry.param_map["use_cursor"] == "useCursor"
        assert entry.param_map["cursor"] == "cursor"
        assert "warehouse_name" not in entry.param_map


class TestProductFieldNameFixes:
    """product.py: 参数名修正 + 补全验证"""

    def _reg(self):
        from services.kuaimai.registry import PRODUCT_REGISTRY
        return PRODUCT_REGISTRY

    def test_stock_status_warehouseId_lowercase_h(self):
        """stock_status: warehouseId 小写h（非 wareHouseId）"""
        entry = self._reg()["stock_status"]
        assert entry.param_map["warehouse_id"] == "warehouseId"

    def test_stock_status_uses_stockStatuses_plural(self):
        """stock_status: stockStatuses 复数"""
        entry = self._reg()["stock_status"]
        assert entry.param_map["stock_statuses"] == "stockStatuses"

    def test_stock_status_no_startModified_endModified(self):
        """stock_status: 不应有 startModified/endModified（官方无此字段）"""
        entry = self._reg()["stock_status"]
        api_values = set(entry.param_map.values())
        assert "startModified" not in api_values
        assert "endModified" not in api_values

    def test_stock_status_has_stock_time_params(self):
        """stock_status: 使用 startStockModified/endStockModified"""
        entry = self._reg()["stock_status"]
        assert entry.param_map["stock_start"] == "startStockModified"
        assert entry.param_map["stock_end"] == "endStockModified"

    def test_stock_update_uses_plural_outerIds(self):
        """stock_update: outerIds/skuOuterIds（复数）+ stockNum 三选一非必填"""
        entry = self._reg()["stock_update"]
        assert entry.param_map["outer_ids"] == "outerIds"
        assert entry.param_map["sku_outer_ids"] == "skuOuterIds"
        assert entry.param_map["stock_num"] == "stockNum"
        assert "warehouse_id" in entry.required_params
        # stockNum/overStockNum/underStockNum 三选一，非 required
        assert "stock_num" not in entry.required_params

    def test_virtual_stock_update_has_full_params(self):
        """virtual_stock_update: 完整 param_map"""
        entry = self._reg()["virtual_stock_update"]
        assert entry.param_map["item_ids"] == "itemIds"
        assert entry.param_map["warehouse_id"] == "warehouseId"
        assert entry.param_map["stock_num"] == "stockNum"
        assert entry.param_map["sku_outer_ids"] == "skuOuterIds"
        assert entry.param_map["outer_ids"] == "outerIds"
        assert "warehouse_id" in entry.required_params

    def test_product_list_has_return_purchase(self):
        """product_list: 有 whetherReturnPurchase"""
        entry = self._reg()["product_list"]
        assert entry.param_map["return_purchase"] == "whetherReturnPurchase"

    def test_product_detail_has_return_purchase(self):
        """product_detail: 有 whetherReturnPurchase"""
        entry = self._reg()["product_detail"]
        assert entry.param_map["return_purchase"] == "whetherReturnPurchase"

    def test_history_cost_price_has_warehouse_ids(self):
        """history_cost_price: 有 warehouseIdList"""
        entry = self._reg()["history_cost_price"]
        assert entry.param_map["warehouse_ids"] == "warehouseIdList"

    def test_product_add_update_simple_has_full_params(self):
        """product_add_update_simple: 完整 param_map + 2个必填"""
        entry = self._reg()["product_add_update_simple"]
        assert entry.param_map["outer_id"] == "outerId"
        assert entry.param_map["title"] == "title"
        assert entry.param_map["purchase_price"] == "purchasePrice"
        assert entry.param_map["weight"] == "weight"
        assert "outer_id" in entry.required_params
        assert "title" in entry.required_params

    def test_supplier_add_has_params(self):
        """supplier_add: 有 itemId/outerId/suppliers 等"""
        entry = self._reg()["supplier_add"]
        assert entry.param_map["item_id"] == "itemId"
        assert entry.param_map["suppliers"] == "suppliers"

    def test_supplier_delete_has_params(self):
        """supplier_delete: 有 supplierIds/supplierCodes"""
        entry = self._reg()["supplier_delete"]
        assert entry.param_map["supplier_ids"] == "supplierIds"
        assert entry.param_map["supplier_codes"] == "supplierCodes"

    def test_phantom_params_removed(self):
        """warehouse_stock 的 sysItemId/warehouseId 已确认 API 无效，已移除"""
        entry = self._reg()["warehouse_stock"]
        assert "item_id" not in entry.param_map
        assert "warehouse_id" not in entry.param_map


class TestPurchaseNewParamMaps:
    """purchase.py: 新增 param_map 验证"""

    def _reg(self):
        from services.kuaimai.registry import PURCHASE_REGISTRY
        return PURCHASE_REGISTRY

    def test_purchase_strategy_calculate_has_warehouse_code(self):
        """purchase_strategy_calculate: warehouseCode 必填"""
        entry = self._reg()["purchase_strategy_calculate"]
        assert entry.param_map["warehouse_code"] == "warehouseCode"
        assert "warehouse_code" in entry.required_params

    def test_purchase_progress_has_progress_type(self):
        """purchase_progress: progressType 有默认值 4"""
        entry = self._reg()["purchase_progress"]
        assert entry.param_map["progress_type"] == "progressType"
        assert entry.defaults.get("progressType") == 4

    def test_warehouse_entry_history_requires_dates(self):
        """warehouse_entry_history: start_date/end_date 必填"""
        entry = self._reg()["warehouse_entry_history"]
        assert entry.param_map["start_date"] == "startModified"
        assert entry.param_map["end_date"] == "endModified"
        assert "start_date" in entry.required_params
        assert "end_date" in entry.required_params

    def test_purchase_return_history_requires_dates(self):
        """purchase_return_history: start_date/end_date 必填"""
        entry = self._reg()["purchase_return_history"]
        assert "start_date" in entry.required_params
        assert "end_date" in entry.required_params

    def test_shelf_history_requires_dates(self):
        """shelf_history: start_date/end_date 必填"""
        entry = self._reg()["shelf_history"]
        assert "start_date" in entry.required_params
        assert "end_date" in entry.required_params

    def test_detail_queries_have_id(self):
        """7个详情查询都有 ID 参数"""
        reg = self._reg()
        details = {
            "purchase_return_detail": "id",
            "warehouse_entry_detail": "id",
            "shelf_detail": "id",
            "purchase_order_history_detail": "id",
            "warehouse_entry_history_detail": "id",
            "purchase_return_history_detail": "id",
            "shelf_history_detail": "id",
        }
        for name, expected_api_val in details.items():
            entry = reg[name]
            assert entry.param_map, f"purchase.{name} 应有 param_map"
            assert expected_api_val in entry.param_map.values(), (
                f"purchase.{name} 应映射到 {expected_api_val}"
            )

    def test_warehouse_entry_list_has_prein_order_id(self):
        """warehouse_entry_list: 有 preinOrderId"""
        entry = self._reg()["warehouse_entry_list"]
        assert entry.param_map["prein_order_id"] == "preinOrderId"

    def test_supplier_add_update_has_required(self):
        """supplier_add_update: code/name 必填"""
        entry = self._reg()["supplier_add_update"]
        assert entry.param_map["code"] == "code"
        assert entry.param_map["name"] == "name"
        assert "code" in entry.required_params
        assert "name" in entry.required_params

    def test_purchase_add_update_has_required(self):
        """purchase_add_update: supplierCode/items/warehouseCode 必填"""
        entry = self._reg()["purchase_add_update"]
        assert "supplier_code" in entry.required_params
        assert "items" in entry.required_params
        assert "warehouse_code" in entry.required_params

    def test_purchase_return_save_has_required(self):
        """purchase_return_save: supplierCode/items/warehouseCode 必填"""
        entry = self._reg()["purchase_return_save"]
        assert "supplier_code" in entry.required_params
        assert "items" in entry.required_params
        assert "warehouse_code" in entry.required_params

    def test_purchase_return_cancel_has_id(self):
        """purchase_return_cancel: return_id 必填"""
        entry = self._reg()["purchase_return_cancel"]
        assert entry.param_map["return_id"] == "id"
        assert "return_id" in entry.required_params

    def test_warehouse_entry_cancel_has_ids(self):
        """warehouse_entry_cancel: entry_ids 必填"""
        entry = self._reg()["warehouse_entry_cancel"]
        assert entry.param_map["entry_ids"] == "ids"
        assert "entry_ids" in entry.required_params


class TestWarehouseDetailAndWrite:
    """warehouse.py: 详情ID + 写操作验证"""

    def _reg(self):
        from services.kuaimai.registry import WAREHOUSE_REGISTRY
        return WAREHOUSE_REGISTRY

    def test_detail_queries_have_id_param(self):
        """7个详情查询都有 ID 参数"""
        reg = self._reg()
        details = [
            "allocate_in_detail", "allocate_out_detail",
            "other_in_detail", "other_out_detail",
            "unshelve_detail", "process_order_detail",
        ]
        for name in details:
            entry = reg[name]
            assert entry.param_map, f"warehouse.{name} 应有 param_map"

    def test_inventory_sheet_detail_requires_code(self):
        """inventory_sheet_detail: code 必填"""
        entry = self._reg()["inventory_sheet_detail"]
        assert entry.param_map["code"] == "code"
        assert "code" in entry.required_params

    def test_allocate_in_receive_has_required(self):
        """allocate_in_receive: allocate_id + receive_details 必填"""
        entry = self._reg()["allocate_in_receive"]
        assert entry.param_map["allocate_id"] == "id"
        assert entry.param_map["receive_details"] == "receiveDetails"
        assert "allocate_id" in entry.required_params
        assert "receive_details" in entry.required_params

    def test_other_in_add_has_required(self):
        """other_in_add: items/warehouseCode 必填"""
        entry = self._reg()["other_in_add"]
        assert entry.param_map["items"] == "items"
        assert entry.param_map["warehouse_code"] == "warehouseCode"
        assert "items" in entry.required_params

    def test_other_in_cancel_has_ids(self):
        """other_in_cancel: ids 必填"""
        entry = self._reg()["other_in_cancel"]
        assert entry.param_map["ids"] == "ids"
        assert "ids" in entry.required_params

    def test_other_out_add_has_required(self):
        """other_out_add: items/warehouseCode 必填"""
        entry = self._reg()["other_out_add"]
        assert entry.param_map["items"] == "items"
        assert entry.param_map["warehouse_code"] == "warehouseCode"
        assert "items" in entry.required_params

    def test_other_out_cancel_has_ids(self):
        """other_out_cancel: ids 必填"""
        entry = self._reg()["other_out_cancel"]
        assert entry.param_map["ids"] == "ids"
        assert "ids" in entry.required_params

    def test_unshelve_execute_has_id(self):
        """unshelve_execute: unshelve_id 必填"""
        entry = self._reg()["unshelve_execute"]
        assert entry.param_map["unshelve_id"] == "id"
        assert "unshelve_id" in entry.required_params


class TestAftersalesParamFixes:
    """aftersales.py: workorder_create 补全 + 写操作验证"""

    def _reg(self):
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        return AFTERSALES_REGISTRY

    def test_workorder_create_has_14_params(self):
        """workorder_create: 至少14个参数"""
        entry = self._reg()["workorder_create"]
        assert len(entry.param_map) >= 13
        assert entry.param_map["warehouse_id"] == "refundWarehouseId"
        assert entry.param_map["reason"] == "reason"
        assert entry.param_map["after_sale_type"] == "afterSaleType"
        assert entry.param_map["reissue_list"] == "reissueOrRefundList"

    def test_workorder_create_has_4_required(self):
        """workorder_create: 4个必填"""
        entry = self._reg()["workorder_create"]
        required = set(entry.required_params)
        assert {"warehouse_id", "reason", "after_sale_type", "reissue_list"}.issubset(required)

    def test_workorder_remark_update_has_required(self):
        """workorder_remark_update: work_order_id + remark 必填"""
        entry = self._reg()["workorder_remark_update"]
        assert entry.param_map["work_order_id"] == "workOrderId"
        assert entry.param_map["remark"] == "remark"
        assert "work_order_id" in entry.required_params
        assert "remark" in entry.required_params

    def test_workorder_explains_update_has_required(self):
        """workorder_explains_update: work_order_id + explains 必填"""
        entry = self._reg()["workorder_explains_update"]
        assert entry.param_map["work_order_id"] == "workOrderId"
        assert entry.param_map["explains"] == "explains"
        assert "work_order_id" in entry.required_params
        assert "explains" in entry.required_params

    def test_repair_process_has_params(self):
        """repair_process: repair_no 必填"""
        entry = self._reg()["repair_process"]
        assert entry.param_map["repair_no"] == "repairOrderNum"
        assert "repair_no" in entry.required_params
        assert entry.param_map["has_fee"] == "hasFee"

    def test_repair_edit_money_has_required(self):
        """repair_edit_money: repair_no + repair_money 必填"""
        entry = self._reg()["repair_edit_money"]
        assert entry.param_map["repair_no"] == "repairOrderNum"
        assert entry.param_map["repair_money"] == "repairMoney"
        assert "repair_no" in entry.required_params
        assert "repair_money" in entry.required_params

    def test_repair_pay_has_params(self):
        """repair_pay: repair_no 必填 + 付款参数"""
        entry = self._reg()["repair_pay"]
        assert entry.param_map["repair_no"] == "repairOrderNum"
        assert "repair_no" in entry.required_params
        assert entry.param_map["received_time"] == "receivedTime"
        assert entry.param_map["current_price"] == "currentPrice"

    def test_update_platform_refund_money_has_required(self):
        """update_platform_refund_money: work_order_id 必填"""
        entry = self._reg()["update_platform_refund_money"]
        assert entry.param_map["work_order_id"] == "id"
        assert "work_order_id" in entry.required_params

    def test_update_express_has_required(self):
        """update_express: work_order_id 必填"""
        entry = self._reg()["update_express"]
        assert entry.param_map["work_order_id"] == "id"
        assert entry.param_map["express_name"] == "expressName"
        assert "work_order_id" in entry.required_params

    def test_aftersale_list_has_suite_single(self):
        """aftersale_list: 有 suiteSingle 参数"""
        entry = self._reg()["aftersale_list"]
        assert entry.param_map["suite_single"] == "suiteSingle"

    def test_workorder_goods_received_has_params(self):
        """workorder_goods_received: received_orders 必填"""
        entry = self._reg()["workorder_goods_received"]
        assert entry.param_map["received_orders"] == "receivedAsOrders"
        assert "received_orders" in entry.required_params

    def test_workorder_batch_change_type_has_required(self):
        """workorder_batch_change_type: work_order_ids + change_type 必填"""
        entry = self._reg()["workorder_batch_change_type"]
        assert "work_order_ids" in entry.required_params
        assert "change_type" in entry.required_params


class TestBasicAndDistributionFixes:
    """basic.py + distribution.py: 新增参数验证"""

    def test_customer_create_has_new_params(self):
        """customer_create: 包含 typeCode/qqNumber/fax/url/zipCode"""
        from services.kuaimai.registry import BASIC_REGISTRY
        entry = BASIC_REGISTRY["customer_create"]
        assert entry.param_map["type_code"] == "typeCode"
        assert entry.param_map["qq"] == "qqNumber"
        assert entry.param_map["fax"] == "fax"
        assert entry.param_map["url"] == "url"
        assert entry.param_map["zip_code"] == "zipCode"

    def test_add_distributor_has_version_number(self):
        """add_distributor: 有 versionNumber"""
        from services.kuaimai.registry import DISTRIBUTION_REGISTRY
        entry = DISTRIBUTION_REGISTRY["add_distributor"]
        assert entry.param_map["version_number"] == "versionNumber"

    def test_tag_batch_update_product_has_item_info_list(self):
        """product tag_batch_update: itemInfoList 参数"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["tag_batch_update"]
        assert entry.param_map["item_info_list"] == "itemInfoList"
        assert "type" in entry.required_params
        assert "item_info_list" in entry.required_params


class TestRegistryDocAlignment:
    """API文档对齐修正验证：确认所有 registry 与官方文档一致"""

    # ── product.py 修正 ──

    def test_sku_info_has_return_purchase(self):
        """sku_info: 补全 whetherReturnPurchase"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["sku_info"]
        assert entry.param_map["return_purchase"] == "whetherReturnPurchase"

    def test_sku_list_response_key_itemSkus(self):
        """sku_list: response_key 应为 itemSkus（非 items）"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["sku_list"]
        assert entry.response_key == "itemSkus"
        assert entry.param_map["return_purchase"] == "whetherReturnPurchase"

    def test_multicode_query_response_key_list(self):
        """multicode_query: response_key 应为 list（非 items）"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["multicode_query"]
        assert entry.response_key == "list"

    def test_virtual_warehouse_response_key_list(self):
        """virtual_warehouse: 补全 response_key=list"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["virtual_warehouse"]
        assert entry.response_key == "list"

    def test_outer_id_list_has_taobao_id(self):
        """outer_id_list: 补全 taobaoId"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["outer_id_list"]
        assert entry.param_map["taobao_id"] == "taobaoId"

    def test_product_list_no_keyword(self):
        """product_list: keyword 已移除（API实测不支持按名称搜索）"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["product_list"]
        # keyword 已确认为幽灵参数（API忽略），已移除
        for phantom in ("keyword", "outer_id", "barcode", "tag_name"):
            assert phantom not in entry.param_map

    # ── trade.py 修正 ──

    def test_outstock_query_no_phantom_and_has_official_params(self):
        """outstock_query: 移除幽灵参数 + 补全官方参数"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["outstock_query"]
        # 幽灵参数已移除
        assert "shop_name" not in entry.param_map
        assert "warehouse_name" not in entry.param_map
        # 官方参数已补全
        assert entry.param_map["except_ids"] == "exceptIds"
        assert entry.param_map["exception_status"] == "exceptionStatus"
        assert entry.param_map["only_contain"] == "onlyContain"
        assert entry.param_map["use_has_next"] == "useHasNext"
        assert entry.param_map["use_cursor"] == "useCursor"
        assert entry.param_map["cursor"] == "cursor"

    def test_fast_stock_update_rewritten_param_map(self):
        """fast_stock_update: 重写后的完整 param_map"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["fast_stock_update"]
        assert entry.param_map["outer_id"] == "outerId"
        assert entry.param_map["num"] == "num"
        assert entry.param_map["type"] == "type"
        assert entry.param_map["need_goods_section"] == "needGoodsSection"
        assert entry.param_map["warehouse_code"] == "warehouseCode"
        # 旧的 sid 已移除
        assert "system_id" not in entry.param_map

    def test_unique_code_validate_rewritten_param_map(self):
        """unique_code_validate: 重写后的完整 param_map"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["unique_code_validate"]
        assert entry.param_map["unique_codes"] == "uniqueCodes"
        assert entry.param_map["start_time"] == "startTime"
        assert entry.param_map["end_time"] == "endTime"
        # 旧参数已移除
        assert "wave_id" not in entry.param_map
        assert "system_id" not in entry.param_map

    # ── purchase.py 修正 ──

    def test_purchase_strategy_response_key(self):
        """purchase_strategy: response_key 应为 purchaseStrategyList"""
        from services.kuaimai.registry import PURCHASE_REGISTRY
        entry = PURCHASE_REGISTRY["purchase_strategy"]
        assert entry.response_key == "purchaseStrategyList"

    def test_purchase_detail_entries_have_required_params(self):
        """采购详情接口：都有 required_params"""
        from services.kuaimai.registry import PURCHASE_REGISTRY
        checks = {
            "purchase_return_detail": ["return_id"],
            "warehouse_entry_detail": ["entry_id"],
            "shelf_detail": ["shelf_id"],
            "purchase_order_history_detail": ["purchase_id"],
            "warehouse_entry_history_detail": ["entry_id"],
            "purchase_return_history_detail": ["return_id"],
            "shelf_history_detail": ["shelf_id"],
        }
        for key, expected in checks.items():
            entry = PURCHASE_REGISTRY[key]
            for param in expected:
                assert param in entry.required_params, f"{key} 缺少 required_param: {param}"

    # ── aftersales.py 修正 ──

    def test_refund_warehouse_requires_time_type(self):
        """refund_warehouse: time_type 为必填"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["refund_warehouse"]
        assert "time_type" in entry.required_params

    def test_workorder_explains_requires_is_append(self):
        """workorder_explains_update: is_append 为必填"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["workorder_explains_update"]
        assert "is_append" in entry.required_params

    def test_repair_process_requires_has_fee_and_failure_cause(self):
        """repair_process: has_fee/failure_cause 为必填"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["repair_process"]
        assert "has_fee" in entry.required_params
        assert "failure_cause" in entry.required_params

    def test_repair_pay_requires_received_time_and_price(self):
        """repair_pay: received_time/current_price 为必填"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["repair_pay"]
        assert "received_time" in entry.required_params
        assert "current_price" in entry.required_params

    def test_update_platform_refund_money_requires_amount(self):
        """update_platform_refund_money: platform_refund_money 为必填"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["update_platform_refund_money"]
        assert "platform_refund_money" in entry.required_params

    # ── warehouse.py 修正 ──

    def test_goods_section_delete_not_write(self):
        """goods_section_delete: 不是写操作（查询接口）"""
        from services.kuaimai.registry import WAREHOUSE_REGISTRY
        entry = WAREHOUSE_REGISTRY["goods_section_delete"]
        assert entry.is_write is False

    # ── distribution.py 修正 ──

    def test_distributor_item_list_response_key_data(self):
        """distributor_item_list: response_key 应为 data + request_source 必填"""
        from services.kuaimai.registry import DISTRIBUTION_REGISTRY
        entry = DISTRIBUTION_REGISTRY["distributor_item_list"]
        assert entry.response_key == "data"
        assert "request_source" in entry.required_params

    def test_add_distributor_requires_source(self):
        """add_distributor: source 为必填"""
        from services.kuaimai.registry import DISTRIBUTION_REGISTRY
        entry = DISTRIBUTION_REGISTRY["add_distributor"]
        assert "source" in entry.required_params


# ============================================================
# TestTwoStepParamsDispatch — 两步调用 params 分发
# ============================================================


class TestTwoStepParamsDispatch:
    """验证两步调用中 params=None vs params={} 的分发逻辑"""

    @pytest.mark.asyncio
    async def test_params_none_returns_param_doc(self):
        """Step 1: params=None → 返回参数文档"""
        from services.erp_tool_executor import ErpToolMixin

        class FakeExecutor(ErpToolMixin):
            def __init__(self):
                self.db = None
                self.user_id = "u1"
                self.org_id = "org1"

        executor = FakeExecutor()
        result = await executor._erp_dispatch(
            "erp_info_query", {"action": "shop_list"},
        )
        assert "📋 shop_list" in result
        assert "参数" in result

    @pytest.mark.asyncio
    async def test_params_empty_dict_executes_query(self):
        """Step 2: params={} → 执行查询（不回到 Step 1）"""
        from services.erp_tool_executor import ErpToolMixin
        from unittest.mock import AsyncMock, patch

        class FakeExecutor(ErpToolMixin):
            def __init__(self):
                self.db = None
                self.user_id = "u1"
                self.org_id = "org1"

        executor = FakeExecutor()
        mock_dispatcher = AsyncMock()
        mock_dispatcher.execute = AsyncMock(return_value="共3个店铺")
        mock_dispatcher.close = AsyncMock()

        with patch.object(executor, "_get_erp_dispatcher", return_value=mock_dispatcher):
            result = await executor._erp_dispatch(
                "erp_info_query", {"action": "shop_list", "params": {}},
            )
        # 应该执行查询，不应该返回参数文档
        assert "📋 shop_list" not in result
        mock_dispatcher.execute.assert_called_once()


# ============================================================
# TestParamDocsCoverage — param_docs 覆盖率
# ============================================================


class TestParamDocsCoverage:
    """验证每个读操作的 param_map key 都有 param_docs 条目"""

    @staticmethod
    def _all_registries():
        from services.kuaimai.registry import (
            BASIC_REGISTRY, PRODUCT_REGISTRY, TRADE_REGISTRY,
            AFTERSALES_REGISTRY, WAREHOUSE_REGISTRY,
            PURCHASE_REGISTRY, DISTRIBUTION_REGISTRY, QIMEN_REGISTRY,
        )
        return {
            "basic": BASIC_REGISTRY,
            "product": PRODUCT_REGISTRY,
            "trade": TRADE_REGISTRY,
            "aftersales": AFTERSALES_REGISTRY,
            "warehouse": WAREHOUSE_REGISTRY,
            "purchase": PURCHASE_REGISTRY,
            "distribution": DISTRIBUTION_REGISTRY,
            "qimen": QIMEN_REGISTRY,
        }

    def test_all_read_actions_have_param_docs(self):
        """每个读操作的 param_map key 必须有 param_docs 条目"""
        missing = []
        for cat, reg in self._all_registries().items():
            for name, entry in reg.items():
                if entry.is_write:
                    continue
                for key in entry.param_map:
                    if key not in entry.param_docs:
                        missing.append(f"{cat}.{name}.{key}")
        assert not missing, (
            f"缺失 param_docs 的参数 ({len(missing)} 个): "
            + ", ".join(missing[:20])
        )

    def test_param_docs_no_empty_values(self):
        """param_docs 的值不能为空字符串"""
        empty = []
        for cat, reg in self._all_registries().items():
            for name, entry in reg.items():
                if entry.is_write:
                    continue
                for key, doc in entry.param_docs.items():
                    if not doc.strip():
                        empty.append(f"{cat}.{name}.{key}")
        assert not empty, (
            f"空 param_docs ({len(empty)} 个): "
            + ", ".join(empty[:20])
        )

    def test_param_docs_keys_match_param_map(self):
        """param_docs 的 key 不能有 param_map 中不存在的"""
        extra = []
        for cat, reg in self._all_registries().items():
            for name, entry in reg.items():
                if entry.is_write:
                    continue
                for key in entry.param_docs:
                    if key not in entry.param_map:
                        extra.append(f"{cat}.{name}.{key}")
        assert not extra, (
            f"param_docs 多余的 key ({len(extra)} 个): "
            + ", ".join(extra[:20])
        )


# ============================================================
# TestParamDocGeneration — 参数文档生成器
# ============================================================


class TestParamDocGeneration:
    """验证 generate_param_doc() 输出格式"""

    def test_unknown_tool(self):
        """未知工具返回提示"""
        from services.kuaimai.param_doc import generate_param_doc
        result = generate_param_doc("erp_nonexistent", "foo")
        assert "未知工具" in result

    def test_unknown_action(self):
        """未知操作返回可用操作列表"""
        from services.kuaimai.param_doc import generate_param_doc
        result = generate_param_doc("erp_trade_query", "nonexistent")
        assert "未知操作" in result
        assert "order_list" in result  # 应列出可用操作

    def test_valid_action_returns_doc(self):
        """合法操作返回参数文档"""
        from services.kuaimai.param_doc import generate_param_doc
        result = generate_param_doc("erp_trade_query", "order_list")
        assert "order_list" in result
        assert "参数:" in result
        assert "order_id" in result
        assert "通用参数:" in result
        assert "page" in result

    def test_required_params_marked(self):
        """必填参数有标记"""
        from services.kuaimai.param_doc import generate_param_doc
        from services.kuaimai.registry import TRADE_REGISTRY
        # 找一个有 required_params 的 action
        for name, entry in TRADE_REGISTRY.items():
            if entry.required_params and not entry.is_write:
                result = generate_param_doc("erp_trade_query", name)
                assert "必填" in result
                break

    def test_no_param_action(self):
        """无参数的操作输出正确"""
        from services.kuaimai.param_doc import generate_param_doc
        from services.kuaimai.registry import TRADE_REGISTRY
        for name, entry in TRADE_REGISTRY.items():
            if not entry.param_map and not entry.is_write:
                result = generate_param_doc("erp_trade_query", name)
                assert "仅需指定 action" in result
                break

    def test_includes_param_docs_content(self):
        """输出包含 param_docs 中的描述"""
        from services.kuaimai.param_doc import generate_param_doc
        result = generate_param_doc("erp_trade_query", "order_list")
        # order_list 的 param_docs 应包含"平台订单号"
        assert "平台订单号" in result or "订单号" in result

    def test_no_error_codes_when_action_has_none(self):
        """无 error_codes 的 action 不显示错误码区域"""
        from services.kuaimai.param_doc import generate_param_doc
        result = generate_param_doc("erp_trade_query", "wave_query")
        assert "错误码:" not in result

    def test_shows_api_specific_error_codes_only(self):
        """有 error_codes 的 action 只显示自身的专属错误码"""
        from services.kuaimai.param_doc import generate_param_doc
        result = generate_param_doc("erp_trade_query", "order_log")
        assert "错误码:" in result
        assert "20009" in result  # API 专属
        assert "20001" in result  # API 专属

    # ── param_hints 渲染 ──────────────────────────────

    def test_param_hints_rendered_with_warning(self):
        """有 param_hints 的参数显示 ⚠ 前缀提示"""
        from services.kuaimai.param_doc import generate_param_doc
        result = generate_param_doc("erp_product_query", "stock_status")
        assert "⚠" in result
        assert "sku_outer_id" in result

    def test_param_hints_outer_id_shown(self):
        """stock_status 的 outer_id 有歧义消解提示"""
        from services.kuaimai.param_doc import generate_param_doc
        result = generate_param_doc("erp_product_query", "stock_status")
        # outer_id 的 hint 应提及 sku_outer_id
        lines = result.split("\n")
        hint_lines = [l for l in lines if l.strip().startswith("⚠")]
        assert len(hint_lines) >= 1

    def test_no_hints_when_entry_has_none(self):
        """无 param_hints 的 action 不显示 ⚠"""
        from services.kuaimai.param_doc import generate_param_doc
        result = generate_param_doc("erp_trade_query", "order_log")
        lines = result.split("\n")
        hint_lines = [l for l in lines if l.strip().startswith("⚠")]
        assert len(hint_lines) == 0

    def test_order_list_hints_rendered(self):
        """order_list 的 param_hints 正确渲染"""
        from services.kuaimai.param_doc import generate_param_doc
        result = generate_param_doc("erp_trade_query", "order_list")
        assert "⚠" in result
        assert "system_id" in result


# ============================================================
# TestParamHints — generate_param_hints 精简参数提示
# ============================================================


class TestParamHints:
    """验证 generate_param_hints() 按需返回精简提示"""

    def test_unknown_tool_returns_empty(self):
        """未知工具→空字符串"""
        from services.kuaimai.param_doc import generate_param_hints
        result = generate_param_hints("nonexistent_tool", "action", {"k": "v"})
        assert result == ""

    def test_unknown_action_returns_empty(self):
        """未知 action→空字符串"""
        from services.kuaimai.param_doc import generate_param_hints
        result = generate_param_hints("erp_trade_query", "nonexistent", {"k": "v"})
        assert result == ""

    def test_missing_required_param_warned(self):
        """缺少必填参数→输出⚠提示"""
        from services.kuaimai.param_doc import generate_param_hints
        # multicode_query 必填 code，只传 keyword 触发缺失提示
        result = generate_param_hints(
            "erp_product_query", "multicode_query", {"keyword": "test"},
        )
        assert "⚠" in result
        assert "缺少必填" in result

    def test_hint_for_used_param(self):
        """已传参数有 hint→输出💡提示"""
        from services.kuaimai.param_doc import generate_param_hints
        # order_list 的 order_id 有 param_hints
        result = generate_param_hints(
            "erp_trade_query", "order_list", {"order_id": "123"},
        )
        assert "💡" in result
        assert "order_id" in result

    def test_unused_params_suggested(self):
        """未传但有文档的参数→输出📎提示"""
        from services.kuaimai.param_doc import generate_param_hints
        # product_list 有很多可选参数，只传 keyword
        result = generate_param_hints(
            "erp_product_query", "product_list", {"keyword": "手机"},
        )
        assert "📎" in result
        assert "其他可用参数" in result

    def test_skip_pagination_params(self):
        """分页参数不出现在提示中"""
        from services.kuaimai.param_doc import generate_param_hints
        result = generate_param_hints(
            "erp_trade_query", "order_list",
            {"order_id": "123", "page": 1, "page_size": 20},
        )
        # page/page_size 不应被当作"已传参数"触发 hint
        assert "page_size" not in result
        assert "page:" not in result

    def test_all_params_covered_returns_minimal(self):
        """所有参数都传了→只返回 hint（无缺失、无推荐）"""
        from services.kuaimai.param_doc import generate_param_hints
        from services.kuaimai.registry import TOOL_REGISTRIES
        entry = TOOL_REGISTRIES["erp_trade_query"]["order_list"]
        all_params = {k: "test" for k in entry.param_map}
        result = generate_param_hints("erp_trade_query", "order_list", all_params)
        assert "缺少必填" not in result
        assert "其他可用参数" not in result


# ============================================================
# TestFormatterEmptyMessages — 格式化器空结果增强提示
# ============================================================


class TestFormatterEmptyMessages:
    """验证空结果增强提示文本"""

    def _make_entry(self, **overrides):
        from services.kuaimai.registry.base import ApiEntry
        defaults = {
            "method": "test",
            "description": "测试",
            "param_map": {},
            "response_key": "list",
        }
        defaults.update(overrides)
        return ApiEntry(**defaults)

    def test_inventory_empty_neutral_message(self):
        """库存空结果使用中性文案（不暗示参数错误）"""
        from services.kuaimai.formatters.product import format_inventory_list
        entry = self._make_entry(response_key="stockStatusVoList")
        data = {"stockStatusVoList": [], "total": 0}
        result = format_inventory_list(data, entry)
        assert "0 条" in result
        assert "参数类型选错" not in result

    def test_product_list_empty_neutral_message(self):
        """商品空结果使用中性文案"""
        from services.kuaimai.formatters.product import format_product_list
        entry = self._make_entry()
        data = {"items": [], "total": 0}
        result = format_product_list(data, entry)
        assert "0 条" in result
        assert "参数类型选错" not in result

    def test_warehouse_stock_empty_neutral_message(self):
        """仓库库存空结果使用中性文案"""
        from services.kuaimai.formatters.product import format_warehouse_stock
        entry = self._make_entry()
        data = {"list": []}
        result = format_warehouse_stock(data, entry)
        assert "0 条" in result
        assert "参数类型选错" not in result

    def test_nonempty_no_error_hint(self):
        """有结果时不显示参数错误提示"""
        from services.kuaimai.formatters.product import format_inventory_list
        entry = self._make_entry(response_key="stockStatusVoList")
        data = {
            "stockStatusVoList": [{"title": "test", "totalAvailableStockSum": 10}],
            "total": 1,
        }
        result = format_inventory_list(data, entry)
        assert "参数类型选错" not in result


# ============================================================
# TestErrorCodes — 错误码机制
# ============================================================


class TestErrorCodes:
    """验证 error_codes 字段和 GLOBAL_ERROR_CODES"""

    def test_global_error_codes_exists(self):
        """GLOBAL_ERROR_CODES 包含关键错误码"""
        from services.kuaimai.registry.base import GLOBAL_ERROR_CODES
        assert "1" in GLOBAL_ERROR_CODES
        assert "50" in GLOBAL_ERROR_CODES
        assert "401" in GLOBAL_ERROR_CODES

    def test_error_codes_field_default_empty(self):
        """ApiEntry 默认 error_codes 为空"""
        from services.kuaimai.registry.base import ApiEntry
        entry = ApiEntry(method="test", description="test")
        assert entry.error_codes == {}

    def test_trade_order_log_has_error_codes(self):
        """order_log 有 API 专属错误码"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_log"]
        assert "20009" in entry.error_codes
        assert "20020" in entry.error_codes
        assert "20021" in entry.error_codes

    def test_api_search_shows_action_error_codes(self):
        """api_search 精确查询只显示 action 专属错误码"""
        from services.kuaimai.api_search import search_erp_api
        result = search_erp_api("erp_trade_query:order_log")
        assert "错误码:" in result
        assert "20009" in result   # API 专属

    def test_api_search_no_error_codes_when_empty(self):
        """无 error_codes 的 action 不显示错误码区域"""
        from services.kuaimai.api_search import search_erp_api
        result = search_erp_api("erp_trade_query:wave_query")
        assert "错误码:" not in result


# ============================================================
# TestExtractExample — 从 param_docs 提取示例值
# ============================================================


class TestExtractExample:
    """_extract_example 从文档提取示例值"""

    def test_extract_from_example_tag(self):
        """从 '示例:' 标记提取"""
        from services.kuaimai.api_search import _extract_example
        doc = "平台订单号。示例: 126036803257340376"
        assert _extract_example(doc) == "126036803257340376"

    def test_extract_from_option_with_parentheses(self):
        """从 '可选值:' 括号格式提取第一个值"""
        from services.kuaimai.api_search import _extract_example
        doc = "时间类型。可选值: created(下单时间), pay_time(付款时间)"
        assert _extract_example(doc) == "created"

    def test_extract_from_option_with_equals(self):
        """从 '可选值:' 等号格式提取纯值"""
        from services.kuaimai.api_search import _extract_example
        doc = "异常查询状态。可选值: 1=仅包含, 2=排除, 3=同时包含"
        assert _extract_example(doc) == "1"

    def test_extract_from_option_mixed_equals_and_parens(self):
        """等号+括号混合格式"""
        from services.kuaimai.api_search import _extract_example
        doc = "查询范围。可选值: 0=三个月内订单(默认), 1=归档订单"
        assert _extract_example(doc) == "0"

    def test_example_takes_priority_over_option(self):
        """'示例:' 优先于 '可选值:'"""
        from services.kuaimai.api_search import _extract_example
        doc = "状态。可选值: 0=停用, 1=启用。示例: 1"
        assert _extract_example(doc) == "1"

    def test_no_example_no_option_returns_placeholder(self):
        """无示例无可选值返回占位符"""
        from services.kuaimai.api_search import _extract_example
        assert _extract_example("一段普通描述") == "..."
        assert _extract_example("") == "..."


# ============================================================
# TestFormatEntryBrief — 搜索结果格式（传入示例+返回字段）
# ============================================================


class TestFormatEntryBrief:
    """_format_entry_brief 输出包含传入参数示例和返回字段"""

    def test_brief_contains_input_example(self):
        """搜索结果包含传入参数示例"""
        from services.kuaimai.api_search import _format_entry_brief
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_list"]
        result = _format_entry_brief("erp_trade_query", "order_list", entry)
        assert "传入:" in result
        assert "time_type" in result

    def test_brief_contains_return_fields(self):
        """搜索结果包含返回字段"""
        from services.kuaimai.api_search import _format_entry_brief
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_list"]
        result = _format_entry_brief("erp_trade_query", "order_list", entry)
        assert "返回:" in result
        assert "订单号" in result

    def test_brief_time_params_prioritized(self):
        """时间参数优先出现在示例中"""
        from services.kuaimai.api_search import _format_entry_brief
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_list"]
        result = _format_entry_brief("erp_trade_query", "order_list", entry)
        # time_type 应在示例中（因为优先级最高）
        assert '"time_type"' in result

    def test_brief_no_params_action(self):
        """无参数 action 不显示传入行"""
        from services.kuaimai.api_search import _format_entry_brief
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["cat_list"]
        result = _format_entry_brief("erp_product_query", "cat_list", entry)
        assert "传入:" not in result
        assert "返回:" in result

    def test_keyword_search_uses_new_format(self):
        """关键词搜索结果使用新格式（传入+返回）"""
        from services.kuaimai.api_search import search_erp_api
        result = search_erp_api("库存")
        # 至少有一个匹配结果带传入示例
        assert "传入:" in result


# ============================================================
# TestDispatcherRequiredParamsCompat — 必填参数兼容 API 原生名
# ============================================================


class TestDispatcherRequiredParamsCompat:
    """dispatcher 必填参数校验兼容 camelCase API 原生名"""

    def _make_entry(self, **overrides):
        from services.kuaimai.registry.base import ApiEntry
        defaults = {
            "method": "erp.test.query",
            "description": "测试",
            "param_map": {"purchase_id": "purchaseId", "code": "code"},
            "required_params": ["purchase_id"],
        }
        defaults.update(overrides)
        return ApiEntry(**defaults)

    def _make_dispatcher(self, client=None):
        from services.kuaimai.dispatcher import ErpDispatcher
        return ErpDispatcher(client or AsyncMock())

    @pytest.mark.asyncio
    async def test_snake_case_required_param_passes(self):
        """snake_case 必填参数正常通过"""
        entry = self._make_entry()
        d = self._make_dispatcher()
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_test": {"detail": entry},
        }):
            result = await d.execute("erp_test", "detail", {
                "purchase_id": "PO-001",
            })
            assert "缺少必填参数" not in result

    @pytest.mark.asyncio
    async def test_camel_case_required_param_also_passes(self):
        """API 原生名必填参数也通过（不误报缺失）"""
        entry = self._make_entry()
        d = self._make_dispatcher()
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_test": {"detail": entry},
        }):
            result = await d.execute("erp_test", "detail", {
                "purchaseId": "PO-001",
            })
            assert "缺少必填参数" not in result

    @pytest.mark.asyncio
    async def test_truly_missing_required_param_still_fails(self):
        """真正缺少必填参数仍然报错"""
        entry = self._make_entry()
        d = self._make_dispatcher()
        with patch("services.kuaimai.dispatcher.TOOL_REGISTRIES", {
            "erp_test": {"detail": entry},
        }):
            result = await d.execute("erp_test", "detail", {
                "code": "C001",
            })
            assert "缺少必填参数" in result


# ============================================================
# TestParamDocsAccuracy — param_docs 枚举值准确性
# ============================================================


class TestParamDocsAccuracy:
    """验证 param_docs 中的关键枚举值与官方文档一致"""

    def test_order_list_status_uses_official_values(self):
        """order_list status 使用官方 WAIT_BUYER_PAY 格式"""
        from services.kuaimai.registry import TRADE_REGISTRY
        doc = TRADE_REGISTRY["order_list"].param_docs["status"]
        assert "WAIT_BUYER_PAY" in doc
        assert "WAIT_AUDIT" in doc
        assert "SELLER_SEND_GOODS" in doc
        # 不应包含旧的错误值
        assert "wait_check" not in doc

    def test_order_list_time_type_uses_official_values(self):
        """order_list time_type 使用官方 created/pay_time 格式"""
        from services.kuaimai.registry import TRADE_REGISTRY
        doc = TRADE_REGISTRY["order_list"].param_docs["time_type"]
        assert "created" in doc
        assert "pay_time" in doc
        assert "upd_time" in doc

    def test_order_list_query_type_is_archive_scope(self):
        """query_type 是查询范围（三个月内/归档），不是排序"""
        from services.kuaimai.registry import TRADE_REGISTRY
        doc = TRADE_REGISTRY["order_list"].param_docs["query_type"]
        assert "归档" in doc
        assert "排序" not in doc

    def test_outstock_order_status_uses_integer_codes(self):
        """outstock_order_query status_list 使用整数状态码"""
        from services.kuaimai.registry import TRADE_REGISTRY
        doc = TRADE_REGISTRY["outstock_order_query"].param_docs["status_list"]
        assert "10=" in doc
        assert "70=" in doc
        # 不应包含旧的 0-5 值
        assert "0(待拣货)" not in doc

    def test_stock_status_uses_official_values(self):
        """stock_statuses 使用官方 1-6 值"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        doc = PRODUCT_REGISTRY["stock_status"].param_docs["stock_statuses"]
        assert "1=正常" in doc
        assert "4=超卖" in doc
        assert "6=有货" in doc

    def test_product_list_status_is_active_status(self):
        """product_list status 是启用/停用"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        doc = PRODUCT_REGISTRY["product_list"].param_docs["status"]
        assert "0=停用" in doc
        assert "1=启用" in doc

    def test_aftersale_type_3_is_reissue_not_exchange(self):
        """aftersale_list type=3 是补发，4 是换货（不能搞反）"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        doc = AFTERSALES_REGISTRY["aftersale_list"].param_docs["type"]
        assert "3=补发" in doc
        assert "4=换货" in doc

    def test_refund_warehouse_status_starts_from_1(self):
        """refund_warehouse status 从 1 开始"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        doc = AFTERSALES_REGISTRY["refund_warehouse"].param_docs["status"]
        assert "1=等待收货" in doc
        assert "0(待入库)" not in doc

    def test_repair_list_status_includes_negative(self):
        """repair_list status 包含 -1=已作废"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        doc = AFTERSALES_REGISTRY["repair_list"].param_docs["status"]
        assert "-1=已作废" in doc


# ============================================================
# TestTwoStepToolSchema — 两步调用模式 Schema
# ============================================================


class TestTwoStepToolSchema:
    """验证两步调用模式的工具 Schema"""

    def test_all_query_tools_have_4_params(self):
        """API 两步查询工具只有 action/params/page/page_size 4 个属性"""
        from config.erp_local_tools import ERP_LOCAL_TOOLS
        from config.erp_tools import build_erp_tools
        skip = {"erp_execute"} | ERP_LOCAL_TOOLS
        tools = build_erp_tools()
        expected_keys = {"action", "params", "page", "page_size"}
        query_tools = [t for t in tools
                       if t["function"]["name"] not in skip]
        for tool in query_tools:
            props = tool["function"]["parameters"]["properties"]
            assert set(props.keys()) == expected_keys, (
                f"{tool['function']['name']} 属性不符: {set(props.keys())}"
            )

    def test_params_description_mentions_two_step(self):
        """params 描述提到两步调用"""
        from config.erp_tools import build_erp_tools
        tools = build_erp_tools()
        trade = [t for t in tools
                 if t["function"]["name"] == "erp_trade_query"][0]
        params_desc = trade["function"]["parameters"][
            "properties"]["params"]["description"]
        assert "参数文档" in params_desc
        assert "action" in params_desc

    def test_routing_prompt_has_two_step_instructions(self):
        """ERP_ROUTING_PROMPT 包含两步查询指引"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "两步查询" in ERP_ROUTING_PROMPT
        assert "参数文档" in ERP_ROUTING_PROMPT


# ============================================================
# Phase 5B — format_item_with_labels 核心工具函数测试
# ============================================================


class TestFormatItemWithLabels:
    """common.py: format_item_with_labels 核心工具函数"""

    def test_basic_label_mapping(self):
        """已知字段按标签映射输出"""
        from services.kuaimai.formatters.common import format_item_with_labels
        item = {"name": "测试商品", "code": "A001"}
        labels = {"name": "名称", "code": "编码"}
        result = format_item_with_labels(item, labels)
        assert "名称: 测试商品" in result
        assert "编码: A001" in result
        assert " | " in result

    def test_skip_none_and_empty(self):
        """None 和空字符串字段不输出"""
        from services.kuaimai.formatters.common import format_item_with_labels
        item = {"name": "X", "code": None, "remark": ""}
        labels = {"name": "名称", "code": "编码", "remark": "备注"}
        result = format_item_with_labels(item, labels)
        assert "名称: X" in result
        assert "编码" not in result
        assert "备注" not in result

    def test_transforms_applied(self):
        """transforms 转换函数生效"""
        from services.kuaimai.formatters.common import format_item_with_labels
        item = {"status": 1, "price": 99.5}
        labels = {"status": "状态", "price": "价格"}
        transforms = {
            "status": lambda v: "正常" if v == 1 else "停用",
            "price": lambda v: f"¥{v}",
        }
        result = format_item_with_labels(item, labels, transforms=transforms)
        assert "状态: 正常" in result
        assert "价格: ¥99.5" in result

    def test_unknown_fields_fallback(self):
        """未在 labels 中的非空标量字段兜底输出"""
        from services.kuaimai.formatters.common import format_item_with_labels
        item = {"name": "X", "newField": "hello"}
        labels = {"name": "名称"}
        result = format_item_with_labels(item, labels)
        assert "名称: X" in result
        assert "newField: hello" in result

    def test_unknown_fields_skip_zero_and_nested(self):
        """未知字段值为0/list/dict时不输出"""
        from services.kuaimai.formatters.common import format_item_with_labels
        item = {"name": "X", "count": 0, "items": [1, 2], "meta": {"k": "v"}}
        labels = {"name": "名称"}
        result = format_item_with_labels(item, labels)
        assert "count" not in result
        assert "items" not in result
        assert "meta" not in result

    def test_global_skip_fields(self):
        """_GLOBAL_SKIP 中的字段不输出"""
        from services.kuaimai.formatters.common import format_item_with_labels
        item = {"name": "X", "picPath": "/img/1.jpg", "companyId": 999}
        labels = {"name": "名称"}
        result = format_item_with_labels(item, labels)
        assert "picPath" not in result
        assert "companyId" not in result

    def test_custom_skip_set(self):
        """自定义 skip 集合合并生效"""
        from services.kuaimai.formatters.common import format_item_with_labels
        item = {"name": "X", "shortTitle": "短标题"}
        labels = {"name": "名称"}
        result = format_item_with_labels(item, labels, skip={"shortTitle"})
        assert "shortTitle" not in result

    def test_labels_order_preserved(self):
        """输出按 labels 字典的键顺序"""
        from services.kuaimai.formatters.common import format_item_with_labels
        item = {"b": "BBB", "a": "AAA"}
        labels = {"a": "甲", "b": "乙"}
        result = format_item_with_labels(item, labels)
        assert result.index("甲") < result.index("乙")

    def test_empty_item_returns_empty(self):
        """空 item 返回空字符串"""
        from services.kuaimai.formatters.common import format_item_with_labels
        result = format_item_with_labels({}, {"name": "名称"})
        assert result == ""


# ============================================================
# Phase 5B — trade.py formatter 测试
# ============================================================


class TestTradeFormatters:
    """trade.py: 6个 formatter 功能验证"""

    def test_order_list_empty(self):
        from services.kuaimai.formatters.trade import format_order_list
        result = format_order_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_order_list_with_data(self):
        from services.kuaimai.formatters.trade import format_order_list
        data = {
            "list": [{
                "tid": "T20260317001", "sid": "S001",
                "sysStatus": "已发货", "buyerNick": "张三",
                "payment": 199.0, "shopName": "天猫店",
                "receiverState": "广东",
            }],
            "total": 1,
        }
        result = format_order_list(data, None)
        assert "T20260317001" in result
        assert "张三" in result
        assert "¥199" in result
        assert "省: 广东" in result

    def test_order_list_with_sub_orders(self):
        from services.kuaimai.formatters.trade import format_order_list
        data = {
            "list": [{
                "tid": "T001", "sysStatus": "待发货",
                "orders": [
                    {"sysTitle": "蓝色T恤", "num": 2, "price": 59.0},
                ],
            }],
            "total": 1,
        }
        result = format_order_list(data, None)
        assert "蓝色T恤" in result
        assert "数量: 2" in result

    def test_shipment_list_empty(self):
        from services.kuaimai.formatters.trade import format_shipment_list
        result = format_shipment_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_shipment_list_with_data(self):
        from services.kuaimai.formatters.trade import format_shipment_list
        data = {
            "list": [{
                "tid": "T001", "outSid": "SF1234",
                "expressCompanyName": "顺丰",
            }],
            "total": 1,
        }
        result = format_shipment_list(data, None)
        assert "T001" in result
        assert "SF1234" in result

    def test_outstock_order_list_empty(self):
        from services.kuaimai.formatters.trade import format_outstock_order_list
        result = format_outstock_order_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_order_log_empty(self):
        from services.kuaimai.formatters.trade import format_order_log
        result = format_order_log({"list": []}, None)
        assert "未找到" in result

    def test_order_log_with_data(self):
        from services.kuaimai.formatters.trade import format_order_log
        data = {
            "list": [{
                "sid": "S001",
                "operateTime": 1710648000000,
                "action": "发货",
                "operator": "李四",
                "content": "已发货至顺丰",
            }],
        }
        result = format_order_log(data, None)
        assert "S001" in result
        assert "李四" in result
        assert "已发货至顺丰" in result

    def test_express_list_flat_structure(self):
        """快递单号 — 扁平结构（API实际格式）"""
        from services.kuaimai.formatters.trade import format_express_list
        data = {
            "cpCode": "SF",
            "outSids": ["SF001", "SF002"],
            "expressName": "顺丰速运",
        }
        result = format_express_list(data, None)
        assert "顺丰速运" in result
        assert "SF001" in result
        assert "SF002" in result

    def test_express_list_empty(self):
        from services.kuaimai.formatters.trade import format_express_list
        data = {"cpCode": "", "outSids": [], "expressName": ""}
        result = format_express_list(data, None)
        assert "未找到" in result

    def test_logistics_company_empty(self):
        from services.kuaimai.formatters.trade import format_logistics_company
        result = format_logistics_company({"list": []}, None)
        assert "暂无" in result

    def test_logistics_company_with_data(self):
        from services.kuaimai.formatters.trade import format_logistics_company
        data = {
            "list": [{
                "name": "顺丰速运", "cpCode": "SF",
                "cpType": 1, "liveStatus": 1,
            }],
        }
        result = format_logistics_company(data, None)
        assert "顺丰速运" in result
        assert "SF" in result
        assert "直营" in result
        assert "启用" in result


# ============================================================
# Phase 5B — aftersales.py formatter 测试
# ============================================================


class TestAftersalesFormatters:
    """aftersales.py: 6个 formatter 功能验证"""

    def test_aftersale_list_empty(self):
        from services.kuaimai.formatters.aftersales import format_aftersale_list
        result = format_aftersale_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_aftersale_list_uses_correct_field_names(self):
        """售后工单使用修正后的字段名 id/refundMoney"""
        from services.kuaimai.formatters.aftersales import format_aftersale_list
        data = {
            "list": [{
                "id": "WO20260317001",
                "tid": "T001", "sid": "S001",
                "afterSaleType": 2,
                "status": 9,
                "refundMoney": 88.0,
                "buyerNick": "王五",
                "textReason": "尺码不合适",
                "goodStatus": 3,
            }],
            "total": 1,
        }
        result = format_aftersale_list(data, None)
        assert "WO20260317001" in result
        assert "退货" in result
        assert "处理完成" in result
        assert "¥88" in result
        assert "卖家已收" in result

    def test_aftersale_list_with_nested_items(self):
        """售后工单嵌套商品明细"""
        from services.kuaimai.formatters.aftersales import format_aftersale_list
        data = {
            "list": [{
                "id": "WO001",
                "afterSaleType": 1,
                "status": 4,
                "items": [
                    {"title": "红色T恤", "outerId": "SKU001",
                     "receivableCount": 1, "type": 1},
                ],
            }],
            "total": 1,
        }
        result = format_aftersale_list(data, None)
        assert "红色T恤" in result
        assert "SKU001" in result

    def test_refund_warehouse_empty(self):
        from services.kuaimai.formatters.aftersales import format_refund_warehouse
        result = format_refund_warehouse({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_refund_warehouse_uses_correct_field_names(self):
        """销退入库单使用修正后的字段名 id/wareHouseName"""
        from services.kuaimai.formatters.aftersales import format_refund_warehouse
        data = {
            "list": [{
                "id": "RW001",
                "workOrderId": "WO001",
                "sid": "S001", "tid": "T001",
                "wareHouseName": "主仓",
                "status": 3,
                "receiveUser": "仓管员A",
            }],
            "total": 1,
        }
        result = format_refund_warehouse(data, None)
        assert "RW001" in result
        assert "主仓" in result
        assert "已完成" in result
        assert "仓管员A" in result

    def test_replenish_list_empty(self):
        from services.kuaimai.formatters.aftersales import format_replenish_list
        result = format_replenish_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_replenish_list_uses_correct_field_name(self):
        """补款使用修正后的字段名 refundMoney"""
        from services.kuaimai.formatters.aftersales import format_replenish_list
        data = {
            "list": [{
                "tid": "T001", "sid": "S001",
                "refundMoney": 25.5,
                "status": "已完成",
                "sysMaker": "客服小王",
            }],
            "total": 1,
        }
        result = format_replenish_list(data, None)
        assert "T001" in result
        assert "¥25.5" in result
        assert "客服小王" in result

    def test_repair_list_empty(self):
        from services.kuaimai.formatters.aftersales import format_repair_list
        result = format_repair_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_repair_list_uses_correct_field_names(self):
        """维修单使用修正后的字段名 repairOrderNum/repairStatus/userNick"""
        from services.kuaimai.formatters.aftersales import format_repair_list
        data = {
            "list": [{
                "repairOrderNum": "REP001",
                "repairStatus": 1,
                "userNick": "李先生",
                "repairMoney": 150.0,
                "repairWarehouseName": "维修仓",
            }],
            "total": 1,
        }
        result = format_repair_list(data, None)
        assert "REP001" in result
        assert "维修中" in result
        assert "李先生" in result
        assert "¥150" in result

    def test_repair_detail_nested_structure(self):
        """维修单详情 — API返回 {order, itemList, feeList}"""
        from services.kuaimai.formatters.aftersales import format_repair_detail
        data = {
            "order": {
                "repairOrderNum": "REP001",
                "repairStatus": 3,
                "userNick": "赵六",
            },
            "itemList": [
                {"repairItemName": "主板", "repairItemCode": "MB001",
                 "repairQuantity": 1},
            ],
            "feeList": [
                {"currentPrice": 200.0, "operatorName": "技术员A"},
            ],
        }
        result = format_repair_detail(data, None)
        assert "REP001" in result
        assert "已完成" in result
        assert "主板" in result
        assert "MB001" in result
        assert "¥200" in result
        assert "技术员A" in result

    def test_aftersale_log_empty(self):
        from services.kuaimai.formatters.aftersales import format_aftersale_log
        result = format_aftersale_log({"list": []}, None)
        assert "未找到" in result

    def test_aftersale_log_uses_correct_field_names(self):
        """售后日志使用修正后的字段名 operateTime/content/staffName/operateName"""
        from services.kuaimai.formatters.aftersales import format_aftersale_log
        data = {
            "list": [{
                "key": "WO001",
                "operateTime": 1710648000000,
                "operateType": "退款",
                "content": "已退款到原支付方式",
                "staffName": "admin",
                "operateName": "客服小李",
            }],
        }
        result = format_aftersale_log(data, None)
        assert "WO001" in result
        assert "已退款到原支付方式" in result
        assert "客服小李" in result


# ============================================================
# Phase 5B — warehouse.py formatter 测试
# ============================================================


class TestWarehouseFormatters:
    """warehouse.py: 10个 formatter 功能验证"""

    def test_allocate_list_empty(self):
        from services.kuaimai.formatters.warehouse import format_allocate_list
        result = format_allocate_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_allocate_list_uses_correct_field_names(self):
        """调拨单使用修正后的字段名 code/outWarehouseName/inWarehouseName"""
        from services.kuaimai.formatters.warehouse import format_allocate_list
        data = {
            "list": [{
                "code": "DB20260317001",
                "outWarehouseName": "北京仓",
                "inWarehouseName": "上海仓",
                "status": "已完成",
                "outNum": 100, "actualOutNum": 98,
                "outTotalAmount": 5000.0,
            }],
            "total": 1,
        }
        result = format_allocate_list(data, None)
        assert "DB20260317001" in result
        assert "北京仓" in result
        assert "上海仓" in result
        assert "¥5000" in result

    def test_allocate_detail_with_items(self):
        from services.kuaimai.formatters.warehouse import format_allocate_detail
        data = {
            "code": "DB001",
            "outWarehouseName": "A仓",
            "items": [
                {"itemOuterId": "SPU001", "outerId": "SKU001",
                 "outNum": 10, "price": 25.0},
            ],
        }
        result = format_allocate_detail(data, None)
        assert "DB001" in result
        assert "SPU001" in result
        assert "¥25" in result

    def test_other_in_out_list_empty(self):
        from services.kuaimai.formatters.warehouse import format_other_in_out_list
        result = format_other_in_out_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_other_in_out_list_uses_code(self):
        """入出库单使用修正后的字段名 code"""
        from services.kuaimai.formatters.warehouse import format_other_in_out_list
        data = {
            "list": [{
                "code": "IO001",
                "warehouseName": "主仓",
                "status": "已完成",
                "quantity": 50,
            }],
            "total": 1,
        }
        result = format_other_in_out_list(data, None)
        assert "IO001" in result
        assert "主仓" in result

    def test_inventory_sheet_list_empty(self):
        from services.kuaimai.formatters.warehouse import format_inventory_sheet_list
        result = format_inventory_sheet_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_inventory_sheet_list_with_transforms(self):
        """盘点单类型和状态转换"""
        from services.kuaimai.formatters.warehouse import format_inventory_sheet_list
        data = {
            "list": [{
                "code": "PD001",
                "warehouseName": "主仓",
                "type": 1, "status": 3,
            }],
            "total": 1,
        }
        result = format_inventory_sheet_list(data, None)
        assert "PD001" in result
        assert "正常盘点" in result
        assert "已审核" in result

    def test_inventory_sheet_detail_uses_correct_fields(self):
        """盘点单明细使用修正后的字段名 beforeNum/afterNum/differentNum"""
        from services.kuaimai.formatters.warehouse import format_inventory_sheet_detail
        data = {
            "code": "PD001",
            "items": [
                {"title": "蓝色T恤", "outerId": "SKU001",
                 "beforeNum": 100, "afterNum": 98,
                 "differentNum": -2, "qualityType": 1},
            ],
        }
        result = format_inventory_sheet_detail(data, None)
        assert "PD001" in result
        assert "系统数: 100" in result
        assert "实盘数: 98" in result
        assert "差异数: -2" in result
        assert "良品" in result

    def test_unshelve_list_empty(self):
        from services.kuaimai.formatters.warehouse import format_unshelve_list
        result = format_unshelve_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_goods_section_list_empty(self):
        from services.kuaimai.formatters.warehouse import format_goods_section_list
        result = format_goods_section_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_goods_section_list_with_data(self):
        from services.kuaimai.formatters.warehouse import format_goods_section_list
        data = {
            "list": [{"sectionName": "A-01-01", "title": "红色帽子",
                       "quantity": 30}],
            "total": 1,
        }
        result = format_goods_section_list(data, None)
        assert "A-01-01" in result
        assert "红色帽子" in result

    def test_process_order_list_empty(self):
        from services.kuaimai.formatters.warehouse import format_process_order_list
        result = format_process_order_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_batch_stock_list_empty(self):
        from services.kuaimai.formatters.warehouse import format_batch_stock_list
        result = format_batch_stock_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_batch_stock_list_with_data(self):
        from services.kuaimai.formatters.warehouse import format_batch_stock_list
        data = {
            "list": [{"title": "维生素C", "batchNo": "B2026001",
                       "quantity": 500, "expireDate": "2027-01-01"}],
            "total": 1,
        }
        result = format_batch_stock_list(data, None)
        assert "维生素C" in result
        assert "B2026001" in result

    def test_section_record_list_empty(self):
        from services.kuaimai.formatters.warehouse import format_section_record_list
        result = format_section_record_list({"list": [], "total": 0}, None)
        assert "未找到" in result


# ============================================================
# Phase 5B — purchase.py formatter 测试
# ============================================================


class TestPurchaseFormatters:
    """purchase.py: 7个 formatter 功能验证"""

    def test_supplier_list_empty(self):
        from services.kuaimai.formatters.purchase import format_supplier_list
        result = format_supplier_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_supplier_list_uses_correct_field_names(self):
        """供应商使用修正后的字段名 contactName"""
        from services.kuaimai.formatters.purchase import format_supplier_list
        data = {
            "list": [{
                "name": "优质供应商",
                "code": "SUP001",
                "contactName": "张经理",
                "mobile": "13800138000",
                "status": 1,
            }],
            "total": 1,
        }
        result = format_supplier_list(data, None)
        assert "优质供应商" in result
        assert "张经理" in result
        assert "正常" in result

    def test_purchase_order_list_empty(self):
        from services.kuaimai.formatters.purchase import format_purchase_order_list
        result = format_purchase_order_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_purchase_order_list_uses_code(self):
        """采购单使用修正后的字段名 code"""
        from services.kuaimai.formatters.purchase import format_purchase_order_list
        data = {
            "list": [{
                "code": "PO20260317001",
                "supplierName": "优质供应商",
                "status": "已到货",
                "totalAmount": 10000.0,
                "quantity": 200,
                "arrivedQuantity": 200,
            }],
            "total": 1,
        }
        result = format_purchase_order_list(data, None)
        assert "PO20260317001" in result
        assert "优质供应商" in result
        assert "¥10000" in result

    def test_purchase_order_detail_with_items(self):
        from services.kuaimai.formatters.purchase import format_purchase_order_detail
        data = {
            "code": "PO001",
            "supplierName": "A供应商",
            "items": [
                {"itemOuterId": "SPU001", "outerId": "SKU001",
                 "count": 50, "price": 10.0, "amount": 500.0},
            ],
        }
        result = format_purchase_order_detail(data, None)
        assert "PO001" in result
        assert "SPU001" in result
        assert "数量: 50" in result
        assert "¥10" in result

    def test_purchase_return_list_empty(self):
        from services.kuaimai.formatters.purchase import format_purchase_return_list
        result = format_purchase_return_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_purchase_return_list_uses_correct_fields(self):
        """采退单使用修正后的字段名 code/gmCreate"""
        from services.kuaimai.formatters.purchase import format_purchase_return_list
        data = {
            "list": [{
                "code": "PR001",
                "supplierName": "B供应商",
                "totalAmount": 3000.0,
                "totalCount": 30,
                "gmCreate": 1710648000000,
            }],
            "total": 1,
        }
        result = format_purchase_return_list(data, None)
        assert "PR001" in result
        assert "¥3000" in result

    def test_warehouse_entry_list_empty(self):
        from services.kuaimai.formatters.purchase import format_warehouse_entry_list
        result = format_warehouse_entry_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_shelf_list_empty(self):
        from services.kuaimai.formatters.purchase import format_shelf_list
        result = format_shelf_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_purchase_strategy_empty(self):
        from services.kuaimai.formatters.purchase import format_purchase_strategy
        result = format_purchase_strategy({"list": [], "total": 0}, None)
        assert "暂无" in result

    def test_purchase_strategy_uses_correct_fields(self):
        """采购建议使用修正后的字段名 purchaseStock/stockoutNum/itemOuterId"""
        from services.kuaimai.formatters.purchase import format_purchase_strategy
        data = {
            "purchaseStrategyList": [{
                "itemOuterId": "SPU001", "outerId": "SKU001",
                "purchaseStock": 100,
                "stockoutNum": 20,
                "itemCatName": "服装",
            }],
            "total": 1,
        }
        result = format_purchase_strategy(data, None)
        assert "SPU001" in result
        assert "建议采购数: 100" in result
        assert "缺货数: 20" in result


# ============================================================
# Phase 5B — basic.py 未覆盖的 4 个 formatter 测试
# ============================================================


class TestBasicFormattersExtended:
    """basic.py: 补充 warehouse/tag/customer/distributor 测试"""

    def test_warehouse_list_empty(self):
        from services.kuaimai.formatters.basic import format_warehouse_list
        result = format_warehouse_list({"list": []}, None)
        assert "暂无" in result

    def test_warehouse_list_with_type_transform(self):
        from services.kuaimai.formatters.basic import format_warehouse_list
        data = {
            "list": [{
                "name": "主仓库", "code": "WH001",
                "type": 0, "status": 1,
                "city": "上海",
            }],
        }
        result = format_warehouse_list(data, None)
        assert "主仓库" in result
        assert "自有" in result
        assert "正常" in result
        assert "上海" in result

    def test_tag_list_empty(self):
        from services.kuaimai.formatters.basic import format_tag_list
        result = format_tag_list({"list": []}, None)
        assert "暂无" in result

    def test_tag_list_with_type_and_html_cleanup(self):
        from services.kuaimai.formatters.basic import format_tag_list
        data = {
            "list": [{
                "tagName": "VIP客户", "type": 0,
                "remark": "重要客户<br/>需要特殊关注",
            }],
        }
        result = format_tag_list(data, None)
        assert "VIP客户" in result
        assert "普通" in result
        assert "<br/>" not in result

    def test_customer_list_empty(self):
        from services.kuaimai.formatters.basic import format_customer_list
        result = format_customer_list({"list": [], "total": 0}, None)
        assert "未找到" in result

    def test_customer_list_with_transforms(self):
        from services.kuaimai.formatters.basic import format_customer_list
        data = {
            "list": [{
                "name": "优质客户", "code": "C001",
                "type": 0, "status": 1,
                "discountRate": 0.85,
            }],
            "total": 1,
        }
        result = format_customer_list(data, None)
        assert "优质客户" in result
        assert "分销商" in result
        assert "正常" in result

    def test_distributor_list_empty(self):
        from services.kuaimai.formatters.basic import format_distributor_list
        result = format_distributor_list({"list": []}, None)
        assert "暂无" in result

    def test_distributor_list_with_transforms(self):
        from services.kuaimai.formatters.basic import format_distributor_list
        data = {
            "list": [{
                "distributorCompanyName": "XX贸易公司",
                "showState": 2,
                "autoSyncStock": True,
            }],
        }
        result = format_distributor_list(data, None)
        assert "XX贸易公司" in result
        assert "已生效" in result
        assert "是" in result


# ============================================================
# Phase 5B — product.py 未覆盖的 3 个 formatter 测试
# ============================================================


class TestProductFormattersExtended:
    """product.py: 补充 product_detail/sku_info/stock_in_out 测试"""

    def test_product_detail_with_skus(self):
        from services.kuaimai.formatters.product import format_product_detail
        data = {
            "title": "蓝色运动鞋",
            "outerId": "SPU001",
            "priceOutput": 299.0,
            "type": 0,
            "activeStatus": 1,
            "items": [
                {"skuOuterId": "SKU001", "propertiesName": "42码",
                 "priceOutput": 299.0, "activeStatus": 1},
                {"skuOuterId": "SKU002", "propertiesName": "43码",
                 "priceOutput": 299.0, "activeStatus": 1},
            ],
            "sellerCats": [{"name": "运动鞋"}],
        }
        result = format_product_detail(data, None)
        assert "蓝色运动鞋" in result
        assert "¥299" in result
        assert "SKU001" in result
        assert "42码" in result
        assert "运动鞋" in result
        assert "共2个" in result

    def test_product_detail_empty_skus(self):
        from services.kuaimai.formatters.product import format_product_detail
        data = {"title": "简单商品", "outerId": "SPU002"}
        result = format_product_detail(data, None)
        assert "简单商品" in result

    def test_sku_info_list(self):
        from services.kuaimai.formatters.product import format_sku_info
        data = {
            "items": [
                {"skuOuterId": "SKU001", "propertiesName": "红色",
                 "activeStatus": 1},
                {"skuOuterId": "SKU002", "propertiesName": "蓝色",
                 "activeStatus": 0},
            ],
        }
        result = format_sku_info(data, None)
        assert "SKU001" in result
        assert "红色" in result
        assert "启用" in result
        assert "停用" in result

    def test_sku_info_empty(self):
        """空items时走单SKU详情兜底"""
        from services.kuaimai.formatters.product import format_sku_info
        result = format_sku_info({"items": []}, None)
        assert "SKU详情" in result

    def test_sku_info_single_detail(self):
        """单个 SKU 详情（非列表）"""
        from services.kuaimai.formatters.product import format_sku_info
        data = {"skuOuterId": "SKU001", "propertiesName": "XL"}
        result = format_sku_info(data, None)
        assert "SKU001" in result
        assert "XL" in result

    def test_stock_in_out_empty(self):
        from types import SimpleNamespace
        from services.kuaimai.formatters.product import format_stock_in_out
        entry = SimpleNamespace(response_key="stockInOutRecordVos")
        result = format_stock_in_out({"stockInOutRecordVos": [], "total": 0}, entry)
        assert "未找到" in result

    def test_stock_in_out_with_data(self):
        from types import SimpleNamespace
        from services.kuaimai.formatters.product import format_stock_in_out
        entry = SimpleNamespace(response_key="stockInOutRecordVos")
        data = {
            "stockInOutRecordVos": [{
                "outerId": "SKU001", "title": "红色T恤",
                "orderType": "采购入库", "stockChange": 50,
                "warehouseName": "主仓",
                "operateTime": 1710648000000,
            }],
            "total": 1,
        }
        result = format_stock_in_out(data, entry)
        assert "SKU001" in result
        assert "采购入库" in result
        assert "50" in result

    def test_product_list_shows_dimensions(self):
        """商品列表展示长宽高"""
        from services.kuaimai.formatters.product import format_product_list
        data = {
            "items": [{
                "title": "纸巾", "outerId": "P01",
                "x": 29.2, "y": 21.6, "z": 3.8,
                "weight": 262, "activeStatus": 1,
            }],
            "total": 1,
        }
        result = format_product_list(data, None)
        assert "29.2" in result
        assert "21.6" in result
        assert "3.8" in result
        assert "长(cm)" in result

    def test_product_detail_shows_dimensions(self):
        """商品详情展示长宽高（含SKU尺寸）"""
        from services.kuaimai.formatters.product import format_product_detail
        data = {
            "title": "纸巾", "outerId": "P01",
            "x": 29.2, "y": 21.6, "z": 3.8,
            "items": [{
                "skuOuterId": "P01-01", "propertiesName": "整箱",
                "x": 10.0, "y": 8.0, "z": 2.0, "activeStatus": 1,
            }],
        }
        result = format_product_detail(data, None)
        assert "29.2" in result
        assert "10.0" in result

    def test_product_list_no_dimensions(self):
        """无尺寸商品不报错"""
        from services.kuaimai.formatters.product import format_product_list
        data = {
            "items": [{"title": "虚拟商品", "outerId": "V01", "activeStatus": 1}],
            "total": 1,
        }
        result = format_product_list(data, None)
        assert "虚拟商品" in result
        assert "长(cm)" not in result


# ═══════════════════════════════════════════════════════════
# 通用格式化器截断行为验证
# ═══════════════════════════════════════════════════════════


class TestGenericFormatterTruncation:
    """验证 format_generic_list/detail 按边界截断，不破坏数据"""

    def _make_entry(self, **overrides):
        from services.kuaimai.registry.base import ApiEntry
        defaults = {
            "method": "erp.test",
            "description": "测试接口",
            "param_map": {},
            "response_key": "list",
        }
        defaults.update(overrides)
        return ApiEntry(**defaults)

    def test_generic_list_item_boundary_truncation(self):
        """超预算时停在完整条目边界，不截断到JSON一半"""
        from services.kuaimai.formatters.common import format_generic_list
        entry = self._make_entry()
        # 每条约200字符，20条=4000字符，超预算
        items = [{"id": i, "data": "x" * 180} for i in range(25)]
        data = {"list": items, "total": 25}
        result = format_generic_list(data, entry)
        # 不应有残缺JSON（不以逗号或花括号中间结尾）
        for line in result.split("\n"):
            if line.startswith("- {"):
                assert line.endswith("}"), f"条目未完整: {line[-30:]}"
        assert "显示前" in result

    def test_generic_list_respects_max_items(self):
        """即使预算充裕，也不超过 _MAX_GENERIC_ITEMS 条"""
        from services.kuaimai.formatters.common import (
            format_generic_list, _MAX_GENERIC_ITEMS,
        )
        entry = self._make_entry()
        items = [{"id": i} for i in range(30)]
        data = {"list": items, "total": 30}
        result = format_generic_list(data, entry)
        item_lines = [l for l in result.split("\n") if l.startswith("- ")]
        assert len(item_lines) <= _MAX_GENERIC_ITEMS

    def test_generic_list_single_huge_item(self):
        """单条数据极大时，至少显示1条"""
        from services.kuaimai.formatters.common import format_generic_list
        entry = self._make_entry()
        huge = {"id": 1, "payload": "y" * 5000}
        data = {"list": [huge], "total": 1}
        result = format_generic_list(data, entry)
        assert '"id": 1' in result

    def test_generic_detail_key_value_boundary(self):
        """detail 超预算时停在完整 key-value 边界"""
        from services.kuaimai.formatters.common import format_generic_detail
        entry = self._make_entry(response_key=None)
        data = {f"field_{i}": "v" * 200 for i in range(30)}
        result = format_generic_detail(data, entry)
        assert "..." in result
        # 每行完整（不截断到值的一半）
        for line in result.split("\n"):
            if line.strip() == "..." or not line.strip():
                continue
            if line.startswith("  "):
                assert ": " in line

    def test_format_dict_safe_skips_global_skip_fields(self):
        """_format_dict_safe 跳过 _GLOBAL_SKIP 中的字段"""
        from services.kuaimai.formatters.common import _format_dict_safe
        data = {
            "name": "测试",
            "picPath": "http://img.com/1.jpg",
            "sysItemId": 123456,
            "status": "active",
        }
        result = _format_dict_safe(data, "测试", 4000)
        assert "name" in result
        assert "status" in result
        assert "picPath" not in result
        assert "sysItemId" not in result

    def test_format_dict_safe_skips_empty_values(self):
        """_format_dict_safe 跳过 None 和空字符串"""
        from services.kuaimai.formatters.common import _format_dict_safe
        data = {"name": "A", "empty": "", "none_val": None, "code": "B"}
        result = _format_dict_safe(data, "测试", 4000)
        assert "name" in result
        assert "code" in result
        assert "empty" not in result
        assert "none_val" not in result


class TestBasicFormatterLimits:
    """验证 basic.py 列表上限 [:50]"""

    def test_warehouse_list_truncates_at_50(self):
        from services.kuaimai.formatters.basic import format_warehouse_list
        items = [{"name": f"仓库{i}", "code": f"WH{i:03d}"} for i in range(80)]
        result = format_warehouse_list({"list": items}, None)
        assert "共 80 个仓库" in result
        assert "仓库0" in result
        assert "仓库49" in result
        assert "仓库50" not in result

    def test_shop_list_truncates_at_50(self):
        from services.kuaimai.formatters.basic import format_shop_list
        items = [{"title": f"店铺{i}"} for i in range(60)]
        result = format_shop_list({"list": items}, None)
        assert "共 60 个店铺" in result
        assert "店铺49" in result
        assert "店铺50" not in result

    def test_tag_list_truncates_at_50(self):
        from services.kuaimai.formatters.basic import format_tag_list
        items = [{"tagName": f"标签{i}"} for i in range(55)]
        result = format_tag_list({"list": items}, None)
        assert "共 55 个标签" in result
        assert "标签49" in result
        assert "标签50" not in result


class TestWarehouseStockNestedLimits:
    """验证 product.py format_warehouse_stock 嵌套防爆"""

    def test_nested_skus_limited_to_10(self):
        from services.kuaimai.formatters.product import format_warehouse_stock
        # 1个商品 × 20个SKU × 1个仓库 → 应只展示10个SKU
        skus = [
            {
                "skuOuterId": f"SKU{i:03d}",
                "mainWareHousesStock": [
                    {"warehouseName": "主仓", "sellableNum": 10},
                ],
            }
            for i in range(20)
        ]
        data = {"list": [{"outerId": "SPU001", "skus": skus}]}
        result = format_warehouse_stock(data, None)
        assert "SKU009" in result
        assert "SKU010" not in result

    def test_nested_warehouses_limited_to_10(self):
        from services.kuaimai.formatters.product import format_warehouse_stock
        # 1个商品 × 1个SKU × 15个仓库 → 应只展示10个仓库
        wh_stocks = [
            {"warehouseName": f"仓库{i}", "sellableNum": i * 10}
            for i in range(15)
        ]
        data = {
            "list": [{
                "outerId": "SPU001",
                "skus": [{"skuOuterId": "SKU001",
                          "mainWareHousesStock": wh_stocks}],
            }],
        }
        result = format_warehouse_stock(data, None)
        assert "仓库9" in result
        assert "仓库10" not in result


# ═══════════════════════════════════════════════════════════
# Phase 5C: API审计修复验证
# ═══════════════════════════════════════════════════════════


class TestApiAuditFixes:
    """验证 API 审计发现的问题已修复"""

    # ── H1: outstock_order_query statusList 枚举修正 ───

    def test_outstock_order_status_correct_labels(self):
        """statusList 使用API文档正确标签"""
        from services.kuaimai.registry import TRADE_REGISTRY
        doc = TRADE_REGISTRY["outstock_order_query"].param_docs["status_list"]
        assert "10=待处理" in doc
        assert "20=预处理完成" in doc
        assert "30=发货中" in doc
        assert "50=已发货" in doc
        assert "70=已关闭" in doc
        assert "90=已作废" in doc
        # 旧的错误标签不应存在
        assert "待打印" not in doc
        assert "待称重" not in doc
        assert "待出库" not in doc
        assert "部分发货" not in doc
        assert "已签收" not in doc

    # ── H2: outstock_order_query timeType 枚举修正 ───

    def test_outstock_order_time_type_correct(self):
        """timeType 使用API文档正确标签"""
        from services.kuaimai.registry import TRADE_REGISTRY
        doc = TRADE_REGISTRY["outstock_order_query"].param_docs["time_type"]
        assert "2=发货时间" in doc
        assert "3=付款时间" in doc
        assert "4=下单时间" in doc
        assert "5=承诺时间" in doc
        # 旧的错误标签
        assert "出库时间" not in doc
        assert "修改时间" not in doc
        assert "称重时间" not in doc

    # ── H4: aftersale_list sid 警告 ───

    def test_aftersale_sid_has_warning(self):
        """aftersale_list system_id 文档包含警告"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        entry = AFTERSALES_REGISTRY["aftersale_list"]
        # system_id 保留在 param_map（否则 LLM 看不到警告）
        assert "system_id" in entry.param_map
        # param_docs 包含警告文字
        doc = entry.param_docs["system_id"]
        assert "不支持" in doc or "⚠" in doc

    # ── H5: stock_in_out 销量引导 ───

    def test_stock_in_out_description_mentions_sales(self):
        """stock_in_out description 提到销量查询"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["stock_in_out"]
        assert "销量" in entry.description or "销售出库" in entry.description

    def test_stock_in_out_has_param_hints(self):
        """stock_in_out 有 param_hints 引导 order_type"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["stock_in_out"]
        assert entry.param_hints
        assert "order_type" in entry.param_hints

    def test_stock_in_out_has_fetch_all(self):
        """stock_in_out 启用 fetch_all 自动翻页"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["stock_in_out"]
        assert entry.fetch_all is True

    def test_stock_in_out_has_response_key(self):
        """stock_in_out 显式设置 response_key"""
        from services.kuaimai.registry import PRODUCT_REGISTRY
        entry = PRODUCT_REGISTRY["stock_in_out"]
        assert entry.response_key == "stockInOutRecordVos"

    # ── H6: only_contain 负向约束 ───

    def test_order_list_only_contain_warning(self):
        """order_list only_contain 包含负向约束"""
        from services.kuaimai.registry import TRADE_REGISTRY
        doc = TRADE_REGISTRY["order_list"].param_docs["only_contain"]
        assert "不是商品编码" in doc or "不是" in doc

    def test_outstock_query_only_contain_warning(self):
        """outstock_query only_contain 包含负向约束"""
        from services.kuaimai.registry import TRADE_REGISTRY
        doc = TRADE_REGISTRY["outstock_query"].param_docs["only_contain"]
        assert "不是商品编码" in doc or "不是" in doc

    # ── M3: order_types 全量枚举 ───

    def test_order_list_has_full_order_types(self):
        """order_list order_types 包含全部38个值"""
        from services.kuaimai.registry import TRADE_REGISTRY
        doc = TRADE_REGISTRY["order_list"].param_docs["order_types"]
        # 验证关键值存在
        for val in ["0=普通", "1=货到付款", "6=预售", "33=分销",
                     "51=抖音厂商代发", "99=出库单"]:
            assert val in doc, f"缺少 {val}"

    def test_outstock_query_has_full_order_types(self):
        """outstock_query order_types 也包含全部38个值"""
        from services.kuaimai.registry import TRADE_REGISTRY
        doc = TRADE_REGISTRY["outstock_query"].param_docs["order_types"]
        for val in ["0=普通", "1=货到付款", "6=预售", "33=分销",
                     "51=抖音厂商代发", "99=出库单"]:
            assert val in doc, f"缺少 {val}"

    # ── M4: suiteSingle 描述修正 ───

    def test_suite_single_correct_description(self):
        """suiteSingle 描述是'套件信息'而非'套装拆单'"""
        from services.kuaimai.registry import AFTERSALES_REGISTRY
        doc = AFTERSALES_REGISTRY["aftersale_list"].param_docs["suite_single"]
        assert "套件信息" in doc
        assert "套装拆单" not in doc

    # ── outstock_order_query description 同步更新 ───

    def test_outstock_order_description_correct_status_names(self):
        """outstock_order_query description 使用正确的状态名"""
        from services.kuaimai.registry import TRADE_REGISTRY
        desc = TRADE_REGISTRY["outstock_order_query"].description
        assert "销售出库单" in desc
        assert "待打印" not in desc


class TestRoutingPromptAudit:
    """验证路由提示词审计修复"""

    def test_routing_has_sales_query_section(self):
        """路由提示词包含销量计算说明"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "销量" in ERP_ROUTING_PROMPT
        assert "num" in ERP_ROUTING_PROMPT

    def test_routing_has_num_accumulation_hint(self):
        """路由提示词说明销量=sum(num)"""
        from config.erp_tools import ERP_ROUTING_PROMPT
        assert "num" in ERP_ROUTING_PROMPT
        assert "销量" in ERP_ROUTING_PROMPT


# ============================================================
# Phase 5B 补充: suitSingleList + diffStockNum
# ============================================================


class TestProductDetailSuitSingles:
    """format_product_detail 套件子单品渲染"""

    def test_product_detail_with_suit_singles(self):
        """suitSingleList 有数据时渲染子单品列表"""
        from services.kuaimai.formatters.product import format_product_detail
        data = {
            "title": "天竺棉套件",
            "outerId": "TJ-CCNNTXL01",
            "type": 1,
            "suitSingleList": [
                {"outerId": "DBXL01", "title": "单品A", "ratio": 1,
                 "skuOuterId": "DBXL01-01", "propertiesName": "白色M"},
                {"outerId": "DBTX02", "title": "单品B", "ratio": 2},
            ],
            "items": [],
        }
        result = format_product_detail(data, None)
        assert "套件子单品" in result
        assert "2个" in result
        assert "DBXL01" in result
        assert "单品A" in result
        assert "x1" in result
        assert "sku=DBXL01-01" in result
        assert "白色M" in result
        assert "DBTX02" in result
        assert "x2" in result

    def test_product_detail_empty_suit_singles(self):
        """suitSingleList 为空时不渲染"""
        from services.kuaimai.formatters.product import format_product_detail
        data = {
            "title": "普通商品",
            "outerId": "SPU999",
            "suitSingleList": [],
        }
        result = format_product_detail(data, None)
        assert "套件子单品" not in result


class TestSubOrderDiffStockNum:
    """trade.py 子订单 diffStockNum 缺货数量渲染"""

    def test_sub_order_with_diff_stock_num(self):
        """子订单包含缺货数量字段"""
        from services.kuaimai.formatters.trade import format_order_list
        data = {
            "list": [{
                "tid": "T001", "sysStatus": "待发货",
                "orders": [
                    {"sysTitle": "白色T恤", "num": 3,
                     "diffStockNum": 1, "price": 59.0},
                ],
            }],
            "total": 1,
        }
        result = format_order_list(data, None)
        assert "白色T恤" in result
        assert "缺货数量: 1" in result

    def test_sub_order_without_diff_stock_num(self):
        """缺货数量为0时不显示"""
        from services.kuaimai.formatters.trade import format_order_list
        data = {
            "list": [{
                "tid": "T002", "sysStatus": "已发货",
                "orders": [
                    {"sysTitle": "黑色裤子", "num": 2, "price": 89.0},
                ],
            }],
            "total": 1,
        }
        result = format_order_list(data, None)
        assert "黑色裤子" in result
        assert "缺货数量" not in result
