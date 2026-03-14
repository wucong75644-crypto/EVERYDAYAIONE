"""
IP 地理位置服务单元测试

覆盖：extract_client_ip / _is_public_ip / get_location_by_ip / _fetch_from_amap
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ip_location_service import (
    extract_client_ip,
    _is_public_ip,
    get_location_by_ip,
    _fetch_from_amap,
)


# ============================================================
# extract_client_ip 测试
# ============================================================


class TestExtractClientIP:
    """测试从 Request 提取真实客户端 IP"""

    def _make_request(self, headers=None, client_host="127.0.0.1"):
        """构造 mock Request"""
        req = MagicMock()
        req.headers = headers or {}
        req.client = MagicMock()
        req.client.host = client_host
        return req

    def test_x_real_ip(self):
        req = self._make_request(headers={"x-real-ip": "1.2.3.4"})
        assert extract_client_ip(req) == "1.2.3.4"

    def test_x_forwarded_for_single(self):
        req = self._make_request(headers={"x-forwarded-for": "5.6.7.8"})
        assert extract_client_ip(req) == "5.6.7.8"

    def test_x_forwarded_for_multiple(self):
        req = self._make_request(
            headers={"x-forwarded-for": "5.6.7.8, 10.0.0.1, 192.168.1.1"},
        )
        assert extract_client_ip(req) == "5.6.7.8"

    def test_x_real_ip_priority_over_forwarded(self):
        req = self._make_request(
            headers={"x-real-ip": "1.1.1.1", "x-forwarded-for": "2.2.2.2"},
        )
        assert extract_client_ip(req) == "1.1.1.1"

    def test_fallback_to_client_host(self):
        req = self._make_request(client_host="9.8.7.6")
        assert extract_client_ip(req) == "9.8.7.6"

    def test_no_client(self):
        req = MagicMock()
        req.headers = {}
        req.client = None
        assert extract_client_ip(req) == ""


# ============================================================
# _is_public_ip 测试
# ============================================================


class TestIsPublicIP:
    """测试公网 IP 判断"""

    def test_public_ipv4(self):
        assert _is_public_ip("8.8.8.8") is True

    def test_private_ipv4_10(self):
        assert _is_public_ip("10.0.0.1") is False

    def test_private_ipv4_192(self):
        assert _is_public_ip("192.168.1.1") is False

    def test_private_ipv4_172(self):
        assert _is_public_ip("172.16.0.1") is False

    def test_loopback(self):
        assert _is_public_ip("127.0.0.1") is False

    def test_invalid_ip(self):
        assert _is_public_ip("not-an-ip") is False

    def test_empty_string(self):
        assert _is_public_ip("") is False

    def test_ipv6_public(self):
        assert _is_public_ip("2001:4860:4860::8888") is True

    def test_ipv6_loopback(self):
        assert _is_public_ip("::1") is False


# ============================================================
# _fetch_from_amap 测试
# ============================================================


class TestFetchFromAmap:
    """测试高德 API 调用"""

    @pytest.mark.asyncio
    @patch("services.ip_location_service.settings")
    async def test_success_normal_city(self, mock_settings):
        mock_settings.amap_api_key = "test_key"
        mock_settings.ip_location_timeout = 3.0

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "status": "1",
            "province": "广东省",
            "city": "深圳市",
        }

        with patch("services.ip_location_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _fetch_from_amap("1.2.3.4")
            assert result == "广东省深圳市"

    @pytest.mark.asyncio
    @patch("services.ip_location_service.settings")
    async def test_direct_municipality(self, mock_settings):
        """直辖市去重：北京市北京市 → 北京市"""
        mock_settings.amap_api_key = "test_key"
        mock_settings.ip_location_timeout = 3.0

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "status": "1",
            "province": "北京市",
            "city": "北京市",
        }

        with patch("services.ip_location_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _fetch_from_amap("1.2.3.4")
            assert result == "北京市"

    @pytest.mark.asyncio
    @patch("services.ip_location_service.settings")
    async def test_empty_list_response(self, mock_settings):
        """高德对无法识别的 IP 返回空数组"""
        mock_settings.amap_api_key = "test_key"
        mock_settings.ip_location_timeout = 3.0

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "status": "1",
            "province": [],
            "city": [],
        }

        with patch("services.ip_location_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _fetch_from_amap("1.2.3.4")
            assert result is None

    @pytest.mark.asyncio
    @patch("services.ip_location_service.settings")
    async def test_api_error_status(self, mock_settings):
        """高德返回 status != 1"""
        mock_settings.amap_api_key = "test_key"
        mock_settings.ip_location_timeout = 3.0

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "status": "0",
            "info": "INVALID_USER_KEY",
        }

        with patch("services.ip_location_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _fetch_from_amap("1.2.3.4")
            assert result is None

    @pytest.mark.asyncio
    @patch("services.ip_location_service.settings")
    async def test_network_error(self, mock_settings):
        """网络异常静默返回 None"""
        mock_settings.amap_api_key = "test_key"
        mock_settings.ip_location_timeout = 3.0

        with patch("services.ip_location_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = Exception("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await _fetch_from_amap("1.2.3.4")
            assert result is None


# ============================================================
# get_location_by_ip 测试
# ============================================================


class TestGetLocationByIP:
    """测试完整定位流程（缓存 + API）"""

    @pytest.mark.asyncio
    @patch("services.ip_location_service.settings")
    async def test_no_api_key_returns_none(self, mock_settings):
        mock_settings.amap_api_key = None
        result = await get_location_by_ip("8.8.8.8")
        assert result is None

    @pytest.mark.asyncio
    @patch("services.ip_location_service.settings")
    async def test_private_ip_returns_none(self, mock_settings):
        mock_settings.amap_api_key = "test_key"
        result = await get_location_by_ip("192.168.1.1")
        assert result is None

    @pytest.mark.asyncio
    @patch("services.ip_location_service.settings")
    async def test_empty_ip_returns_none(self, mock_settings):
        mock_settings.amap_api_key = "test_key"
        result = await get_location_by_ip("")
        assert result is None

    @pytest.mark.asyncio
    @patch("services.ip_location_service._set_cached", new_callable=AsyncMock)
    @patch("services.ip_location_service._get_cached", new_callable=AsyncMock)
    @patch("services.ip_location_service.settings")
    async def test_cache_hit(self, mock_settings, mock_get_cached, mock_set_cached):
        """缓存命中直接返回"""
        mock_settings.amap_api_key = "test_key"
        mock_get_cached.return_value = "广东省深圳市"

        result = await get_location_by_ip("8.8.8.8")
        assert result == "广东省深圳市"
        mock_set_cached.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.ip_location_service._set_cached", new_callable=AsyncMock)
    @patch("services.ip_location_service._get_cached", new_callable=AsyncMock)
    @patch("services.ip_location_service._fetch_from_amap", new_callable=AsyncMock)
    @patch("services.ip_location_service.settings")
    async def test_cache_miss_calls_api(
        self, mock_settings, mock_fetch, mock_get_cached, mock_set_cached,
    ):
        """缓存未命中 → 调 API → 写缓存"""
        mock_settings.amap_api_key = "test_key"
        mock_get_cached.return_value = None
        mock_fetch.return_value = "浙江省杭州市"

        result = await get_location_by_ip("8.8.8.8")
        assert result == "浙江省杭州市"
        mock_fetch.assert_called_once_with("8.8.8.8")
        mock_set_cached.assert_called_once_with("8.8.8.8", "浙江省杭州市")

    @pytest.mark.asyncio
    @patch("services.ip_location_service._set_cached", new_callable=AsyncMock)
    @patch("services.ip_location_service._get_cached", new_callable=AsyncMock)
    @patch("services.ip_location_service._fetch_from_amap", new_callable=AsyncMock)
    @patch("services.ip_location_service.settings")
    async def test_api_fail_caches_empty(
        self, mock_settings, mock_fetch, mock_get_cached, mock_set_cached,
    ):
        """API 失败 → 缓存空字符串（避免重复查询）"""
        mock_settings.amap_api_key = "test_key"
        mock_get_cached.return_value = None
        mock_fetch.return_value = None

        result = await get_location_by_ip("8.8.8.8")
        assert result is None
        mock_set_cached.assert_called_once_with("8.8.8.8", "")

    @pytest.mark.asyncio
    @patch("services.ip_location_service._get_cached", new_callable=AsyncMock)
    @patch("services.ip_location_service.settings")
    async def test_cached_empty_string_returns_none(
        self, mock_settings, mock_get_cached,
    ):
        """缓存值为空字符串 → 返回 None（之前查过无结果）"""
        mock_settings.amap_api_key = "test_key"
        mock_get_cached.return_value = ""

        result = await get_location_by_ip("8.8.8.8")
        assert result is None
