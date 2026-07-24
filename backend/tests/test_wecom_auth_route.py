"""WeCom OAuth HTTP route orchestration tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.routes.wecom_auth import (
    _classify_error,
    get_binding_status,
    get_qr_url,
    oauth_callback,
    consume_oauth_handoff,
    OAuthHandoffRequest,
    unbind_wecom,
)
from core.exceptions import ValidationError


def _request() -> MagicMock:
    request = MagicMock()
    request.headers = {"X-Request-Id": "request-1"}
    return request


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.frontend_url = "https://example.com"
    settings.wecom_oauth_redirect_uri = "https://example.com/callback"
    settings.wecom_corp_id = "ww-system"
    settings.wecom_agent_id = 1000006
    return settings


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("state 无效或已过期", "state_invalid"),
        ("仅限企业成员使用", "not_member"),
        ("账号已被禁用", "user_disabled"),
        ("该账号已绑定其他企微用户", "already_bound"),
        ("请联系管理员审核合并", "already_bound"),
        ("unknown", "api_error"),
    ],
)
def test_classify_error(message: str, code: str) -> None:
    assert _classify_error(message) == code


@pytest.mark.asyncio
async def test_qr_url_uses_scoped_public_config() -> None:
    service = MagicMock()
    service.generate_state = AsyncMock(return_value="state-1")
    service.build_qr_url.return_value = {"state": "state-1"}
    identity = MagicMock()
    identity.get_public_config.return_value = {
        "corp_id": "ww-org", "agent_id": "1000009",
    }
    with (
        patch("api.routes.wecom_auth.get_settings", return_value=_settings()),
        patch(
            "api.routes.wecom_auth.WecomOAuthIdentityService.for_login",
            return_value=identity,
        ) as factory,
    ):
        result = await get_qr_url(
            request=_request(),
            user_id=None,
            db=MagicMock(),
            org_id="00000000-0000-0000-0000-000000000001",
            svc=service,
        )
    assert result["state"] == "state-1"
    factory.assert_called_once()
    service.build_qr_url.assert_called_once_with(
        "state-1", corp_id="ww-org", agent_id="1000009",
    )


@pytest.mark.asyncio
async def test_qr_url_without_org_requires_authenticated_bind() -> None:
    with patch("api.routes.wecom_auth.get_settings", return_value=_settings()):
        with pytest.raises(HTTPException) as exc:
            await get_qr_url(
                request=_request(),
                user_id=None,
                db=MagicMock(),
                org_id=None,
                svc=MagicMock(),
            )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_qr_url_builds_authenticated_bind_state() -> None:
    service = MagicMock()
    service.generate_state = AsyncMock(return_value="bind-state")
    service.build_qr_url.return_value = {"state": "bind-state"}
    with patch("api.routes.wecom_auth.get_settings", return_value=_settings()):
        await get_qr_url(
            request=_request(),
            user_id="user-1",
            db=MagicMock(),
            org_id=None,
            svc=service,
        )
    service.generate_state.assert_awaited_once_with(
        "bind", user_id="user-1", org_id=None,
    )


@pytest.mark.asyncio
async def test_callback_login_uses_exchange_and_login_scopes() -> None:
    transport = AsyncMock()
    transport.db = MagicMock()
    transport.validate_state.return_value = {
        "type": "login",
        "user_id": None,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }
    transport.exchange_code.return_value = {"userid": "zhangsan"}
    transport.create_handoff.return_value = "h" * 43
    identity = MagicMock()
    identity.get_exchange_config.return_value = {
        "corp_id": "ww-org", "agent_secret": "secret",
    }
    identity.login_or_create.return_value = {
        "token": {"access_token": "jwt", "refresh_token": "refresh"},
        "user": {"id": "user-1", "nickname": "张三"},
        "org": {"org_id": "org-1", "name": "企业", "role": "member"},
    }
    with (
        patch("api.routes.wecom_auth.get_settings", return_value=_settings()),
        patch(
            "api.routes.wecom_auth.WecomOAuthIdentityService.for_login",
            return_value=identity,
        ),
    ):
        response = await oauth_callback(
            request=_request(), code="code", state="state", svc=transport,
        )
    assert response.status_code == 302
    assert response.headers["location"].endswith(f"handoff={'h' * 43}")
    assert "token=" not in response.headers["location"]
    transport.create_handoff.assert_awaited_once_with(
        identity.login_or_create.return_value,
    )
    identity.login_or_create.assert_called_once_with(
        wecom_userid="zhangsan", corp_id="ww-org",
    )


@pytest.mark.asyncio
async def test_callback_bind_uses_actor_scope() -> None:
    transport = AsyncMock()
    transport.db = MagicMock()
    transport.validate_state.return_value = {
        "type": "bind", "user_id": "user-1", "org_id": None,
    }
    transport.exchange_code.return_value = {"userid": "zhangsan"}
    transport.create_handoff.return_value = "h" * 43
    identity = MagicMock()
    identity.bind_account.return_value = {
        "token": {"access_token": "jwt"},
        "user": {"id": "user-1"},
    }
    with (
        patch("api.routes.wecom_auth.get_settings", return_value=_settings()),
        patch(
            "api.routes.wecom_auth.WecomOAuthIdentityService.for_actor",
            return_value=identity,
        ) as factory,
    ):
        response = await oauth_callback(
            request=_request(), code="code", state="state", svc=transport,
        )
    assert response.status_code == 302
    factory.assert_called_once_with(
        transport.db, user_id="user-1", request_id="request-1",
    )
    identity.bind_account.assert_called_once_with(
        wecom_userid="zhangsan", corp_id="ww-system",
    )


@pytest.mark.asyncio
async def test_handoff_endpoint_consumes_code_once() -> None:
    service = AsyncMock()
    service.consume_handoff.return_value = {
        "token": {"access_token": "jwt"},
        "user": {"id": "user-1"},
    }
    payload = OAuthHandoffRequest(code="h" * 43)
    result = await consume_oauth_handoff(payload=payload, svc=service)
    assert result["user"]["id"] == "user-1"
    service.consume_handoff.assert_awaited_once_with("h" * 43)


@pytest.mark.asyncio
async def test_callback_business_error_redirects_without_exception() -> None:
    transport = AsyncMock()
    transport.validate_state.side_effect = ValidationError("登录链接已失效")
    with patch("api.routes.wecom_auth.get_settings", return_value=_settings()):
        response = await oauth_callback(
            request=_request(), code="code", state="state", svc=transport,
        )
    assert "error=state_invalid" in response.headers["location"]


@pytest.mark.asyncio
async def test_unbind_and_status_use_actor_facade() -> None:
    service = MagicMock()
    service.db = MagicMock()
    identity = MagicMock()
    identity.unbind_account.return_value = {
        "success": True, "message": "企微账号已解绑",
    }
    identity.get_binding_status.return_value = {
        "bound": False, "wecom_nickname": None, "bound_at": None,
    }
    with patch(
        "api.routes.wecom_auth.WecomOAuthIdentityService.for_actor",
        return_value=identity,
    ) as factory:
        unbound = await unbind_wecom(
            request=_request(), user_id="user-1", svc=service,
        )
        status = await get_binding_status(
            request=_request(), user_id="user-1", svc=service,
        )
    assert unbound["success"] is True
    assert status["bound"] is False
    assert factory.call_count == 2


@pytest.mark.asyncio
async def test_unbind_missing_binding_maps_to_404() -> None:
    service = MagicMock()
    service.db = MagicMock()
    identity = MagicMock()
    identity.unbind_account.side_effect = ValidationError("当前账号未绑定企微")
    with patch(
        "api.routes.wecom_auth.WecomOAuthIdentityService.for_actor",
        return_value=identity,
    ):
        with pytest.raises(HTTPException) as exc:
            await unbind_wecom(
                request=_request(), user_id="user-1", svc=service,
            )
    assert exc.value.status_code == 404
