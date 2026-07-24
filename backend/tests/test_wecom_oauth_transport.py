"""WeCom OAuth state, remote exchange and QR transport tests."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.exceptions import ExternalServiceError, PermissionDeniedError, ValidationError
from services.wecom_oauth_service import (
    OAUTH_HANDOFF_PREFIX,
    OAUTH_HANDOFF_TTL,
    OAUTH_STATE_PREFIX,
    OAUTH_STATE_TTL,
    WecomOAuthService,
)


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.wecom_corp_id = "ww_test_corp"
    settings.wecom_agent_id = 1000006
    settings.wecom_agent_secret = "test_secret"
    settings.wecom_oauth_redirect_uri = "https://example.com/api/auth/wecom/callback"
    return settings


@pytest.fixture
def service() -> WecomOAuthService:
    with patch("services.wecom_oauth_service.get_settings", return_value=_settings()):
        return WecomOAuthService(MagicMock())


@pytest.mark.asyncio
async def test_generate_state_stores_login_scope(service: WecomOAuthService) -> None:
    redis = AsyncMock()
    with patch("services.wecom_oauth_service.get_redis", return_value=redis):
        state = await service.generate_state(
            "login", user_id=None, org_id="00000000-0000-0000-0000-000000000001",
        )
    key, value = redis.set.call_args.args
    assert key == f"{OAUTH_STATE_PREFIX}{state}"
    assert redis.set.call_args.kwargs["ex"] == OAUTH_STATE_TTL
    assert json.loads(value)["org_id"] == "00000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_generate_bind_state_stores_actor(service: WecomOAuthService) -> None:
    redis = AsyncMock()
    with patch("services.wecom_oauth_service.get_redis", return_value=redis):
        await service.generate_state("bind", user_id="actor-1")
    assert json.loads(redis.set.call_args.args[1])["user_id"] == "actor-1"


@pytest.mark.asyncio
async def test_generate_state_fails_when_redis_is_missing(
    service: WecomOAuthService,
) -> None:
    with patch("services.wecom_oauth_service.get_redis", return_value=None):
        with pytest.raises(RuntimeError, match="Redis 不可用"):
            await service.generate_state()


@pytest.mark.asyncio
async def test_validate_state_atomically_consumes_value(
    service: WecomOAuthService,
) -> None:
    redis = AsyncMock()
    redis.getdel.return_value = json.dumps({"type": "login", "org_id": "org-1"})
    with patch("services.wecom_oauth_service.get_redis", return_value=redis):
        result = await service.validate_state("state-1")
    assert result["org_id"] == "org-1"
    redis.getdel.assert_awaited_once_with(f"{OAUTH_STATE_PREFIX}state-1")


@pytest.mark.asyncio
async def test_validate_state_rejects_replay(service: WecomOAuthService) -> None:
    redis = AsyncMock()
    redis.getdel.return_value = None
    with patch("services.wecom_oauth_service.get_redis", return_value=redis):
        with pytest.raises(ValidationError, match="失效"):
            await service.validate_state("used")


@pytest.mark.asyncio
async def test_validate_state_fails_closed_without_redis(
    service: WecomOAuthService,
) -> None:
    with patch("services.wecom_oauth_service.get_redis", return_value=None):
        with pytest.raises(ExternalServiceError):
            await service.validate_state("state")


@pytest.mark.asyncio
async def test_handoff_is_short_lived_and_atomically_consumed(
    service: WecomOAuthService,
) -> None:
    redis = AsyncMock()
    payload = {"token": {"access_token": "jwt"}, "user": {"id": "user-1"}}
    with patch("services.wecom_oauth_service.get_redis", return_value=redis):
        code = await service.create_handoff(payload)
        stored = redis.set.call_args
        assert stored.args[0] == f"{OAUTH_HANDOFF_PREFIX}{code}"
        assert stored.kwargs["ex"] == OAUTH_HANDOFF_TTL
        redis.getdel.return_value = stored.args[1]
        assert await service.consume_handoff(code) == payload
    redis.getdel.assert_awaited_once_with(f"{OAUTH_HANDOFF_PREFIX}{code}")


@pytest.mark.asyncio
async def test_handoff_replay_or_invalid_payload_fails_closed(
    service: WecomOAuthService,
) -> None:
    redis = AsyncMock()
    with patch("services.wecom_oauth_service.get_redis", return_value=redis):
        redis.getdel.return_value = None
        with pytest.raises(ValidationError, match="交接码已失效"):
            await service.consume_handoff("used")
        redis.getdel.return_value = json.dumps({"user": {"id": "user-1"}})
        with pytest.raises(ValidationError, match="交接数据无效"):
            await service.consume_handoff("invalid")


@pytest.mark.asyncio
async def test_exchange_code_uses_per_org_credentials(
    service: WecomOAuthService,
) -> None:
    response = MagicMock()
    response.json.return_value = {"errcode": 0, "userid": "zhangsan"}
    client = AsyncMock()
    client.get.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    with (
        patch(
            "services.wecom_oauth_service.get_access_token",
            return_value="access-token",
        ) as get_token,
        patch("services.wecom_oauth_service.httpx.AsyncClient", return_value=context),
    ):
        result = await service.exchange_code(
            "code", org_id="org-1", corp_id="ww-org", agent_secret="secret",
        )
    assert result["userid"] == "zhangsan"
    get_token.assert_awaited_once_with("org-1", "ww-org", "secret")


@pytest.mark.asyncio
async def test_exchange_code_rejects_non_member(
    service: WecomOAuthService,
) -> None:
    response = MagicMock()
    response.json.return_value = {"errcode": 0, "openid": "external"}
    client = AsyncMock()
    client.get.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    with (
        patch(
            "services.wecom_oauth_service.get_access_token",
            return_value="access-token",
        ),
        patch("services.wecom_oauth_service.httpx.AsyncClient", return_value=context),
    ):
        with pytest.raises(PermissionDeniedError):
            await service.exchange_code("code")


def test_build_qr_url_uses_explicit_enterprise_config(
    service: WecomOAuthService,
) -> None:
    result = service.build_qr_url(
        "state-1", corp_id="ww-org", agent_id="1000009",
    )
    assert "appid=ww-org" in result["qr_url"]
    assert "agentid=1000009" in result["qr_url"]
    assert "state=state-1" in result["qr_url"]
