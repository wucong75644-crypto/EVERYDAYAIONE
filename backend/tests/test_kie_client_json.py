"""
KIE client .json() 保护测试

覆盖：create_task / query_task 收到非 JSON 响应时抛 KieAPIError
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.adapters.kie.client import (
    KieAPIError,
    KieAuthenticationError,
    KieClient,
    KieInsufficientBalanceError,
    KieRateLimitError,
)


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


class TestInsufficientBalanceAlert:
    def test_402_logs_sanitized_alert_and_preserves_exception(self, client):
        response = {"code": 402, "msg": "Credits insufficient", "token": "secret"}

        with patch("services.adapters.kie.client.logger.error") as mock_error:
            with pytest.raises(KieInsufficientBalanceError):
                client._handle_error_response(402, response, model="test-model")

        message = mock_error.call_args.args[0]
        assert message.endswith("provider=kie | model=test-model | code=402")
        assert "env=" in message
        assert "secret" not in message

    @pytest.mark.asyncio
    async def test_body_402_uses_request_model(self, client):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"code": 402, "msg": "Credits insufficient"}
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        request = MagicMock(model="image-model")
        request.model_dump.return_value = {"model": "image-model", "input": {}}

        with patch.object(client, "_get_client", return_value=mock_http):
            with patch("services.adapters.kie.client.logger.error") as mock_error:
                with pytest.raises(KieInsufficientBalanceError):
                    await client.create_task(request)

        assert "model=image-model" in mock_error.call_args.args[0]

    @pytest.mark.asyncio
    async def test_query_body_402_uses_unknown_model(self, client):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"code": 402, "msg": "Credits insufficient"}
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_client", return_value=mock_http):
            with patch("services.adapters.kie.client.logger.error") as mock_error:
                with pytest.raises(KieInsufficientBalanceError):
                    await client.query_task("task-123")

        assert "model=unknown" in mock_error.call_args.args[0]

    @pytest.mark.parametrize(
        ("status_code", "error_type"),
        [(401, KieAuthenticationError), (429, KieRateLimitError)],
    )
    def test_other_kie_errors_do_not_log_balance_alert(self, client, status_code, error_type):
        with patch("services.adapters.kie.client.logger.error") as mock_error:
            with pytest.raises(error_type):
                client._handle_error_response(
                    status_code, {"code": status_code, "msg": "provider error"}
                )

        mock_error.assert_not_called()
