"""
快麦ERP API 集成测试

覆盖：签名算法、客户端请求、Token刷新、业务服务、工具注册。
"""

import hashlib
import hmac as hmac_mod
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

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
from services.kuaimai.service import KuaiMaiService


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
# 业务服务测试
# ============================================================


class TestKuaiMaiService:
    """业务查询服务测试"""

    def setup_method(self):
        self.mock_client = AsyncMock(spec=KuaiMaiClient)
        self.service = KuaiMaiService(client=self.mock_client)

    @pytest.mark.asyncio
    async def test_query_orders_empty(self):
        """订单查询无结果"""
        self.mock_client.request_with_retry.return_value = {
            "success": True, "list": [], "total": 0
        }
        result = await self.service.query_orders(query_type="by_time_range")
        assert "未找到" in result

    @pytest.mark.asyncio
    async def test_query_orders_by_id(self):
        """按订单号查询"""
        self.mock_client.request_with_retry.return_value = {
            "success": True,
            "total": 1,
            "list": [{
                "tid": "ORDER001",
                "sid": "SYS001",
                "sysStatus": "FINISHED",
                "buyerNick": "买家A",
                "payment": "99.00",
                "shopName": "测试店铺",
                "created": 1704067200000,
                "payTime": 1704070800000,
            }],
        }
        result = await self.service.query_orders(
            query_type="by_order_id", order_id="ORDER001"
        )
        assert "ORDER001" in result
        assert "FINISHED" in result
        assert "99.00" in result

        call_args = self.mock_client.request_with_retry.call_args
        assert call_args[0][0] == "erp.trade.list.query"
        assert call_args[0][1]["tid"] == "ORDER001"

    @pytest.mark.asyncio
    async def test_query_orders_by_status(self):
        """按状态查询订单"""
        self.mock_client.request_with_retry.return_value = {
            "success": True, "total": 0, "list": []
        }
        await self.service.query_orders(query_type="by_status", status="WAIT_SEND")

        call_args = self.mock_client.request_with_retry.call_args
        assert call_args[0][1]["status"] == "WAIT_SEND"

    @pytest.mark.asyncio
    async def test_query_orders_pagination_hint(self):
        """订单结果超过一页时提示翻页"""
        self.mock_client.request_with_retry.return_value = {
            "success": True,
            "total": 50,
            "list": [{"tid": f"T{i}", "sid": f"S{i}", "sysStatus": "FINISHED",
                       "buyerNick": "", "payment": "10", "shopName": "店铺",
                       "created": 1704067200000, "payTime": None}
                      for i in range(20)],
        }
        result = await self.service.query_orders(query_type="by_time_range")
        assert "第2页" in result

    @pytest.mark.asyncio
    async def test_query_products_list_all(self):
        """列出商品列表"""
        self.mock_client.request_with_retry.return_value = {
            "success": True,
            "total": 2,
            "items": [
                {"title": "测试商品A", "outerId": "SKU001", "activeStatus": 1},
                {"title": "测试商品B", "outerId": "SKU002", "activeStatus": 1},
            ],
        }
        result = await self.service.query_products(query_type="list_all")
        assert "测试商品A" in result
        assert "SKU001" in result
        assert "共找到 2" in result

    @pytest.mark.asyncio
    async def test_query_single_product(self):
        """按编码查询单个商品（对齐 item.single.get 响应）"""
        self.mock_client.request_with_retry.return_value = {
            "success": True,
            "item": {
                "title": "精品T恤",
                "outerId": "TS-001",
                "barcode": "6901234567890",
                "weight": 200,
                "unit": "件",
                "activeStatus": 1,
                "isSkuItem": 1,
                "catId": "50012345",
                "items": [
                    {"skuOuterId": "TS-001-S", "propertiesName": "S码", "barcode": "690S", "activeStatus": 1},
                    {"skuOuterId": "TS-001-M", "propertiesName": "M码", "barcode": "690M", "activeStatus": 1},
                ],
            },
        }
        result = await self.service.query_products(
            query_type="by_code", product_code="TS-001"
        )
        assert "精品T恤" in result
        assert "TS-001" in result
        assert "6901234567890" in result
        assert "SKU列表" in result
        assert "TS-001-S" in result

    @pytest.mark.asyncio
    async def test_query_single_product_not_found(self):
        """按编码查询商品不存在"""
        self.mock_client.request_with_retry.return_value = {
            "success": True, "item": {}
        }
        result = await self.service.query_products(
            query_type="by_code", product_code="NOTEXIST"
        )
        assert "未找到" in result

    @pytest.mark.asyncio
    async def test_query_inventory(self):
        """库存查询（对齐 stock.api.status.query 响应）"""
        self.mock_client.request_with_retry.return_value = {
            "success": True,
            "total": 1,
            "stockStatusVoList": [{
                "title": "商品X",
                "mainOuterId": "SKU-X",
                "outerId": "SKU-X-01",
                "totalAvailableStockSum": 100,
                "sellableNum": 80,
                "totalLockStock": 20,
                "wareHouseId": 12345,
                "stockStatus": 1,
                "purchasePrice": 10.50,
            }],
        }
        result = await self.service.query_inventory(product_code="SKU-X")
        assert "商品X" in result
        assert "总库存: 100" in result
        assert "可售: 80" in result
        assert "正常" in result

    @pytest.mark.asyncio
    async def test_query_shipment_by_order(self):
        """按订单号查物流"""
        self.mock_client.request_with_retry.return_value = {
            "success": True,
            "total": 1,
            "list": [{
                "tid": "ORDER001",
                "sid": 12345,
                "sysStatus": "FINISHED",
                "outSid": "SF1234567890",
                "expressCompanyName": "顺丰速运",
                "shopName": "测试店铺",
                "consignTime": 1704154800000,
                "payment": "99.00",
                "orders": [
                    {"title": "商品A", "num": 2},
                ],
            }],
        }
        result = await self.service.query_shipment(
            query_type="by_order_id", order_id="ORDER001"
        )
        assert "ORDER001" in result
        assert "SF1234567890" in result
        assert "顺丰速运" in result
        assert "商品A" in result

    @pytest.mark.asyncio
    async def test_query_shipment_by_waybill(self):
        """按快递单号查物流"""
        self.mock_client.request_with_retry.return_value = {
            "success": True, "total": 0, "list": []
        }
        result = await self.service.query_shipment(
            query_type="by_waybill", waybill_no="YT9999"
        )
        assert "未找到" in result

        call_args = self.mock_client.request_with_retry.call_args
        assert call_args[0][1]["outSids"] == "YT9999"

    @pytest.mark.asyncio
    async def test_query_inventory_warning(self):
        """查询警戒库存"""
        self.mock_client.request_with_retry.return_value = {
            "success": True, "total": 0, "stockStatusVoList": []
        }
        result = await self.service.query_inventory(stock_status="warning")
        assert "未找到" in result

        call_args = self.mock_client.request_with_retry.call_args
        assert call_args[0][1]["stockStatuses"] == 2

    @pytest.mark.asyncio
    async def test_query_inventory_by_sku_code(self):
        """按SKU编码查库存（sku_code 优先于 product_code）"""
        self.mock_client.request_with_retry.return_value = {
            "success": True,
            "total": 1,
            "stockStatusVoList": [{
                "title": "拼豆熨斗",
                "mainOuterId": "PDYD01",
                "outerId": "PDYD01-01",
                "totalAvailableStockSum": 123,
                "sellableNum": 118,
                "totalLockStock": 5,
                "wareHouseId": 100,
                "stockStatus": 1,
                "purchasePrice": 10.80,
            }],
        }
        result = await self.service.query_inventory(
            product_code="PDYD01", sku_code="PDYD01-01"
        )
        assert "拼豆熨斗" in result
        assert "总库存: 123" in result
        assert "可售: 118" in result

        # sku_code 优先：传 skuOuterId 而非 mainOuterId
        call_args = self.mock_client.request_with_retry.call_args
        assert "skuOuterId" in call_args[0][1]
        assert "mainOuterId" not in call_args[0][1]

    @pytest.mark.asyncio
    async def test_query_inventory_pagesize_100(self):
        """库存查询 pageSize 为 100"""
        self.mock_client.request_with_retry.return_value = {
            "success": True, "total": 0, "stockStatusVoList": []
        }
        await self.service.query_inventory()
        call_args = self.mock_client.request_with_retry.call_args
        assert call_args[0][1]["pageSize"] == 100


# ============================================================
# 格式化工具方法测试
# ============================================================


class TestServiceHelpers:
    """Service 辅助方法测试"""

    def test_parse_date_with_value(self):
        """有日期值时返回格式化结果"""
        result = KuaiMaiService._parse_date("2024-01-15")
        assert result == "2024-01-15 00:00:00"

    def test_parse_date_full_format(self):
        """完整格式直接返回"""
        result = KuaiMaiService._parse_date("2024-01-15 10:30:00")
        assert result == "2024-01-15 10:30:00"

    def test_parse_date_default_days_ago(self):
        """无日期时使用相对日期"""
        result = KuaiMaiService._parse_date(None, days_ago=7)
        datetime.strptime(result, "%Y-%m-%d %H:%M:%S")

    def test_parse_date_default_now(self):
        """无参数返回当前时间"""
        result = KuaiMaiService._parse_date(None)
        parsed = datetime.strptime(result, "%Y-%m-%d %H:%M:%S")
        assert parsed.year == datetime.now().year

    def test_format_timestamp_millis(self):
        """毫秒时间戳转换"""
        ts = 1704067200000
        result = KuaiMaiService._format_timestamp(ts)
        assert "2024-01-01" in result

    def test_format_timestamp_seconds(self):
        """秒级时间戳转换"""
        ts = 1704067200
        result = KuaiMaiService._format_timestamp(ts)
        assert "2024" in result

    def test_format_timestamp_none(self):
        """空值返回 -"""
        assert KuaiMaiService._format_timestamp(None) == "-"
        assert KuaiMaiService._format_timestamp(0) == "-"

    def test_format_timestamp_invalid(self):
        """无效值返回原值"""
        assert KuaiMaiService._format_timestamp("not_a_number") == "not_a_number"


# ============================================================
# 格式化方法字段对齐测试
# ============================================================


class TestFormatMethodsAlignment:
    """验证格式化方法使用正确的 API 响应字段"""

    def setup_method(self):
        self.mock_client = AsyncMock(spec=KuaiMaiClient)
        self.service = KuaiMaiService(client=self.mock_client)

    def test_format_order_pdd_null_fields(self):
        """pdd 隐私字段为 null 时不报错"""
        order = {
            "tid": None, "sid": 12345, "sysStatus": "FINISHED",
            "buyerNick": None, "payment": None, "shopName": "拼多多店",
            "source": "pdd", "created": 1704067200000, "payTime": None,
        }
        result = self.service._format_order(order)
        assert "（隐私保护）" in result
        assert "¥0" in result
        assert "拼多多店" in result
        assert "来源: pdd" in result

    def test_format_order_normal(self):
        """正常订单格式化"""
        order = {
            "tid": "ORD001", "sid": 12345, "sysStatus": "WAIT_SEND_GOODS",
            "buyerNick": "张三", "payment": "99.00", "shopName": "旗舰店",
            "created": 1704067200000, "payTime": 1704067200000,
        }
        result = self.service._format_order(order)
        assert "ORD001" in result
        assert "张三" in result
        assert "¥99.00" in result

    def test_format_product_new_fields(self):
        """商品列表格式化使用 item.list.query 字段"""
        item = {
            "title": "测试商品", "outerId": "SKU001",
            "barcode": "6901234567890", "activeStatus": 1,
            "isSkuItem": 1, "weight": 200,
        }
        result = self.service._format_product(item)
        assert "测试商品" in result
        assert "SKU001" in result
        assert "条码: 6901234567890" in result
        assert "多规格: 是" in result
        assert "状态: 启用" in result
        assert "200g" in result

    def test_format_product_disabled(self):
        """停用商品显示正确"""
        item = {"title": "已停商品", "activeStatus": 0, "isSkuItem": 0}
        result = self.service._format_product(item)
        assert "状态: 停用" in result
        assert "多规格: 否" in result

    def test_format_product_detail_with_items_sku(self):
        """商品详情 SKU 在 items 数组（对齐 item.single.get）"""
        item = {
            "title": "测试商品", "outerId": "TS-001",
            "barcode": "690", "weight": 200, "unit": "件",
            "catId": "50012345", "activeStatus": 1, "isSkuItem": 1,
            "sellerCats": [{"name": "服饰"}, {"name": "T恤"}],
            "items": [
                {"skuOuterId": "TS-001-S", "propertiesName": "S码",
                 "barcode": "690S", "activeStatus": 1},
                {"skuOuterId": "TS-001-M", "propertiesName": "M码",
                 "barcode": "690M", "activeStatus": 0},
            ],
        }
        result = self.service._format_product_detail(item)
        assert "TS-001" in result
        assert "SKU列表（共2个）" in result
        assert "TS-001-S" in result
        assert "S码" in result
        assert "条码: 690S" in result
        assert "停用" in result  # TS-001-M is disabled
        assert "分类: 服饰 > T恤" in result

    def test_format_inventory_new_fields(self):
        """库存格式化使用 stock.api.status.query 字段"""
        item = {
            "title": "拼豆熨斗", "mainOuterId": "PDYD01",
            "outerId": "PDYD01-01", "propertiesName": "白色",
            "totalAvailableStockSum": 123, "sellableNum": 118,
            "totalLockStock": 5, "wareHouseId": 100,
            "stockStatus": 1, "purchasePrice": 10.80,
        }
        result = self.service._format_inventory(item)
        assert "拼豆熨斗" in result
        assert "编码: PDYD01" in result
        assert "SKU: PDYD01-01" in result
        assert "规格: 白色" in result
        assert "总库存: 123" in result
        assert "可售: 118" in result
        assert "锁定: 5" in result
        assert "仓库ID: 100" in result
        assert "采购价: ¥10.8" in result
        assert "正常" in result

    def test_format_inventory_same_outer_id(self):
        """主编码与SKU编码相同时不重复显示"""
        item = {
            "title": "单品", "mainOuterId": "A001", "outerId": "A001",
            "totalAvailableStockSum": 50, "sellableNum": 50,
            "stockStatus": 6,
        }
        result = self.service._format_inventory(item)
        assert result.count("A001") == 1  # 只出现一次
        assert "有货" in result

    def test_format_shipment_pdd_null_fields(self):
        """出库格式化兼容 pdd 隐私字段 null"""
        item = {
            "tid": None, "sid": 12345, "sysStatus": "FINISHED",
            "outSid": "SF123", "expressCompanyName": "顺丰",
            "shopName": None, "consignTime": 1704067200000,
            "payment": None, "warehouseName": "默认仓库",
            "orders": [
                {"sysTitle": "商品A", "num": 2},
            ],
        }
        result = self.service._format_shipment(item)
        assert "SF123" in result
        assert "顺丰" in result
        assert "仓库: 默认仓库" in result
        assert "商品A x2" in result
        assert "¥0" in result  # payment is null


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
                "erp_trade_query", {"action": "order_list"}
            )
        assert "失败" in result


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

    def test_agent_tools_count(self):
        """工具总数验证（4 路由 + 3 信息 + 2 搜索 + 8 ERP + 1 爬虫 = 18）"""
        from config.agent_tools import AGENT_TOOLS
        assert len(AGENT_TOOLS) == 18

    def test_agent_tools_names(self):
        """所有ERP工具名在定义中"""
        from config.agent_tools import AGENT_TOOLS
        tool_names = {t["function"]["name"] for t in AGENT_TOOLS}
        assert "erp_info_query" in tool_names
        assert "erp_product_query" in tool_names
        assert "erp_trade_query" in tool_names
        assert "erp_aftersales_query" in tool_names
        assert "erp_warehouse_query" in tool_names
        assert "erp_purchase_query" in tool_names
        assert "erp_taobao_query" in tool_names
        assert "erp_execute" in tool_names

    def test_agent_tools_all_have_valid_structure(self):
        """每个工具有 type=function + function.name/description/parameters"""
        from config.agent_tools import AGENT_TOOLS
        for tool in AGENT_TOOLS:
            assert tool["type"] == "function"
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert len(func["description"]) > 10
            assert "parameters" in func
            assert func["parameters"]["type"] == "object"

    def test_agent_tools_names_match_all_tools(self):
        """工具名集合 = ALL_TOOLS"""
        from config.agent_tools import AGENT_TOOLS, ALL_TOOLS
        tool_names = {t["function"]["name"] for t in AGENT_TOOLS}
        assert tool_names == ALL_TOOLS

    def test_agent_tools_routing_tools_present(self):
        """4 个路由工具全部存在"""
        from config.agent_tools import AGENT_TOOLS
        tool_names = {t["function"]["name"] for t in AGENT_TOOLS}
        for name in ("route_to_chat", "route_to_image",
                      "route_to_video", "ask_user"):
            assert name in tool_names

    def test_agent_tools_info_tools_present(self):
        """信息工具全部存在"""
        from config.agent_tools import AGENT_TOOLS, INFO_TOOLS
        tool_names = {t["function"]["name"] for t in AGENT_TOOLS}
        for name in INFO_TOOLS:
            assert name in tool_names

    def test_route_to_chat_has_model_enum(self):
        """route_to_chat 的 model 参数有 enum 列表"""
        from config.agent_tools import AGENT_TOOLS
        tool = next(
            t for t in AGENT_TOOLS
            if t["function"]["name"] == "route_to_chat"
        )
        props = tool["function"]["parameters"]["properties"]
        assert "model" in props
        assert "enum" in props["model"]
        assert len(props["model"]["enum"]) > 0

    def test_route_to_image_has_prompts_array(self):
        """route_to_image 的 prompts 参数是数组类型"""
        from config.agent_tools import AGENT_TOOLS
        tool = next(
            t for t in AGENT_TOOLS
            if t["function"]["name"] == "route_to_image"
        )
        props = tool["function"]["parameters"]["properties"]
        assert props["prompts"]["type"] == "array"
        assert props["prompts"]["minItems"] == 1
        assert props["prompts"]["maxItems"] == 8

    def test_route_to_image_required_fields(self):
        """route_to_image 必填字段：prompts + model"""
        from config.agent_tools import AGENT_TOOLS
        tool = next(
            t for t in AGENT_TOOLS
            if t["function"]["name"] == "route_to_image"
        )
        required = tool["function"]["parameters"]["required"]
        assert "prompts" in required
        assert "model" in required

    def test_system_prompt_contains_erp_rules(self):
        """系统提示词包含 ERP 路由规则"""
        from config.agent_tools import AGENT_SYSTEM_PROMPT
        assert "erp_info_query" in AGENT_SYSTEM_PROMPT
        assert "erp_product_query" in AGENT_SYSTEM_PROMPT
        assert "erp_trade_query" in AGENT_SYSTEM_PROMPT
        assert "erp_execute" in AGENT_SYSTEM_PROMPT

    def test_system_prompt_contains_routing_keywords(self):
        """系统提示词包含路由核心关键词"""
        from config.agent_tools import AGENT_SYSTEM_PROMPT
        assert "意图路由器" in AGENT_SYSTEM_PROMPT
        assert "route_to_chat" in AGENT_SYSTEM_PROMPT
        assert "route_to_image" in AGENT_SYSTEM_PROMPT
        assert "route_to_video" in AGENT_SYSTEM_PROMPT
        assert "ask_user" in AGENT_SYSTEM_PROMPT

    def test_system_prompt_contains_core_identity(self):
        """系统提示词包含核心身份和职责边界"""
        from config.agent_tools import AGENT_SYSTEM_PROMPT
        assert "意图路由器" in AGENT_SYSTEM_PROMPT
        assert "不直接回答用户问题" in AGENT_SYSTEM_PROMPT
        assert "填好工具参数" in AGENT_SYSTEM_PROMPT

    def test_system_prompt_contains_prohibitions(self):
        """系统提示词包含禁止事项"""
        from config.agent_tools import AGENT_SYSTEM_PROMPT
        assert "禁止直接回复用户" in AGENT_SYSTEM_PROMPT
        assert "禁止调用不存在的工具" in AGENT_SYSTEM_PROMPT

    def test_system_prompt_contains_model_hints(self):
        """系统提示词包含模型选择提示"""
        from config.agent_tools import AGENT_SYSTEM_PROMPT
        assert "视频" in AGENT_SYSTEM_PROMPT
        assert "模型选择" in AGENT_SYSTEM_PROMPT

    def test_erp_tools_have_descriptions(self):
        """ERP 工具有清晰的描述"""
        from config.agent_tools import AGENT_TOOLS
        erp_names = {
            "erp_info_query", "erp_product_query", "erp_trade_query",
            "erp_aftersales_query", "erp_warehouse_query",
            "erp_purchase_query", "erp_execute",
        }
        for tool in AGENT_TOOLS:
            if tool["function"]["name"] in erp_names:
                desc = tool["function"]["description"]
                assert len(desc) > 10

    def test_erp_product_query_has_action_enum(self):
        """商品查询工具有 action 枚举"""
        from config.agent_tools import AGENT_TOOLS
        tool = next(
            t for t in AGENT_TOOLS
            if t["function"]["name"] == "erp_product_query"
        )
        props = tool["function"]["parameters"]["properties"]
        assert "action" in props
        assert "enum" in props["action"]
        assert "product_list" in props["action"]["enum"]
        assert "stock_status" in props["action"]["enum"]

    def test_erp_trade_query_has_action_enum(self):
        """交易查询工具有 action 枚举"""
        from config.agent_tools import AGENT_TOOLS
        tool = next(
            t for t in AGENT_TOOLS
            if t["function"]["name"] == "erp_trade_query"
        )
        props = tool["function"]["parameters"]["properties"]
        assert "action" in props
        assert "enum" in props["action"]
        assert "order_list" in props["action"]["enum"]
        assert "outstock_query" in props["action"]["enum"]

    def test_erp_product_query_has_keyword_param(self):
        """商品查询工具有 keyword 和 outer_id 参数"""
        from config.agent_tools import AGENT_TOOLS
        tool = next(
            t for t in AGENT_TOOLS
            if t["function"]["name"] == "erp_product_query"
        )
        props = tool["function"]["parameters"]["properties"]
        assert "keyword" in props
        assert "outer_id" in props


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
        entry = self._make_entry(response_key="list")
        d = self._make_dispatcher()
        data = {"list": [{"name": "A"}, {"name": "B"}], "total": 2}
        result = d._generic_format(data, entry, "test")
        assert "2" in result
        assert "A" in result

    def test_generic_format_empty_list(self):
        """通用格式化：空列表"""
        entry = self._make_entry(response_key="list")
        d = self._make_dispatcher()
        result = d._generic_format({"list": []}, entry, "test")
        assert "暂无数据" in result

    def test_generic_format_detail(self):
        """通用格式化：非列表响应（详情类）"""
        entry = self._make_entry(response_key=None)
        d = self._make_dispatcher()
        result = d._generic_format({"id": 1, "name": "A"}, entry, "test")
        assert "结果" in result

    def test_generic_format_non_dict(self):
        """通用格式化：非dict响应"""
        entry = self._make_entry()
        d = self._make_dispatcher()
        result = d._generic_format("plain text", entry, "test")
        assert "plain text" in result


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

    def test_build_default_date_range(self):
        """生成默认日期范围"""
        from services.kuaimai.param_mapper import build_default_date_range
        result = build_default_date_range(7)
        assert "start" in result
        assert "end" in result
        assert "00:00:00" in result["start"]


class TestTradeRegistryParamMap:

    def test_order_list_has_time_type_mapping(self):
        """order_list param_map 包含 time_type → timeType 映射"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_list"]
        assert "time_type" in entry.param_map
        assert entry.param_map["time_type"] == "timeType"

    def test_order_list_has_shop_name_mapping(self):
        """order_list param_map 包含 shop_name → shopName 映射"""
        from services.kuaimai.registry import TRADE_REGISTRY
        entry = TRADE_REGISTRY["order_list"]
        assert "shop_name" in entry.param_map
        assert entry.param_map["shop_name"] == "shopName"

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
