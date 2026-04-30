"""
KIE client .json() 保护测试

覆盖：create_task / query_task 收到非 JSON 响应时抛 KieAPIError
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.adapters.kie.client import KieClient, KieAPIError


@pytest.fixture
def client():
    return KieClient(api_key="test-key")


class TestCreateTaskJsonProtection:
    """create_task: 非 JSON 响应 → KieAPIError"""

    @pytest.mark.asyncio
    async def test_non_json_response_raises(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("No JSON")

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch.object(client, "_get_client", return_value=mock_http):
            request = MagicMock()
            request.model_dump.return_value = {"model": "test", "input": {}}

            with pytest.raises(KieAPIError, match="非 JSON 响应"):
                await client.create_task(request)

    @pytest.mark.asyncio
    async def test_html_error_page_raises(self, client):
        """模拟 502 网关返回 HTML"""
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.json.side_effect = ValueError("Expecting value")

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch.object(client, "_get_client", return_value=mock_http):
            request = MagicMock()
            request.model_dump.return_value = {"model": "test", "input": {}}

            with pytest.raises(KieAPIError, match="502"):
                await client.create_task(request)


class TestQueryTaskJsonProtection:
    """query_task: 非 JSON 响应 → KieAPIError"""

    @pytest.mark.asyncio
    async def test_non_json_response_raises(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("No JSON")

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch.object(client, "_get_client", return_value=mock_http):
            with pytest.raises(KieAPIError, match="非 JSON 响应"):
                await client.query_task("task-123")
