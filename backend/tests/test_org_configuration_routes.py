"""Organization configuration routes use only the formal control plane."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.routes.org import (
    SetConfigRequest,
    MCPConnectorCredentialRequest,
    MCPConnectorEnableRequest,
    delete_mcp_connector_credential,
    get_mcp_connector_status,
    get_mcp_connector_credential_status,
    list_org_configs,
    set_org_config,
    set_mcp_connector_credential,
    set_mcp_connector_enabled,
    test_erp_connection as run_erp_connection_test,
    test_mcp_connector_connection as run_mcp_connection_test,
    test_wecom_connection as run_wecom_connection_test,
)
from core.exceptions import PermissionDeniedError
from services.configuration.bundles import ResolvedConfigurationBundle
from services.configuration.resolver import ConfigurationResolutionError


ORG_ID = "00000000-0000-0000-0000-000000000010"
USER_ID = "00000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_status_response_contains_only_non_secret_fields() -> None:
    org_service = MagicMock()
    control = MagicMock()
    control.list_organization_status.return_value = [{
        "key": "erp.token_pair",
        "configured": True,
        "source": "organization",
        "version": 4,
        "updated_at": "2026-07-27T00:00:00Z",
        "unexpected_secret": "must-not-escape",
    }]

    result = await list_org_configs(
        ORG_ID, USER_ID, org_service, control,
    )

    assert result["data"] == [{
        "config_key": "erp.token_pair",
        "configured": True,
        "source": "organization",
        "version": 4,
        "updated_at": "2026-07-27T00:00:00Z",
    }]
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_set_route_forwards_atomic_payload_and_expected_version() -> None:
    org_service = MagicMock()
    control = MagicMock()
    control.set_organization.return_value = {
        "key": "erp.app_credentials",
        "version": 3,
    }
    body = SetConfigRequest(
        config_key="erp.app_credentials",
        value={"app_key": "app", "app_secret": "secret"},
        expected_version=2,
    )

    result = await set_org_config(
        ORG_ID, body, USER_ID, org_service, control,
    )

    assert result == {
        "success": True,
        "data": {
            "config_key": "erp.app_credentials",
            "configured": False,
            "version": 3,
            "source": None,
            "updated_at": None,
        },
    }
    control.set_organization.assert_called_once_with(
        org_id=ORG_ID,
        key="erp.app_credentials",
        value={"app_key": "app", "app_secret": "secret"},
        expected_version=2,
    )


@pytest.mark.asyncio
async def test_erp_test_hides_bundle_and_database_details() -> None:
    org_service = MagicMock()
    bundle_resolver = MagicMock()
    bundle_resolver.erp_runtime.side_effect = ConfigurationResolutionError(
        "database ciphertext secret detail"
    )

    result = await run_erp_connection_test(
        ORG_ID, USER_ID, org_service, bundle_resolver, MagicMock(),
    )

    assert result == {
        "success": False,
        "message": "ERP 配置不完整或不可用",
    }
    assert "database" not in str(result)


@pytest.mark.asyncio
async def test_erp_test_uses_runtime_bundle_and_owner_admin_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_service = MagicMock()
    bundle_resolver = MagicMock()
    bundle_resolver.erp_runtime.return_value = ResolvedConfigurationBundle(
        name="erp.runtime",
        values={
            "erp.app_credentials": {
                "app_key": "app-key",
                "app_secret": "app-secret",
            },
            "erp.token_pair": {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
            },
        },
        sources={},
        versions={"erp.token_pair": 2},
    )
    client = MagicMock()
    client.load_cached_token = AsyncMock()
    client.request_with_retry = AsyncMock(return_value={"success": True})
    client.close = AsyncMock()
    client_type = MagicMock(return_value=client)
    monkeypatch.setattr("services.kuaimai.client.KuaiMaiClient", client_type)

    result = await run_erp_connection_test(
        ORG_ID, USER_ID, org_service, bundle_resolver, MagicMock(),
    )

    assert result == {
        "success": True,
        "message": "ERP 连接测试成功",
    }
    org_service.require_role.assert_called_once_with(
        ORG_ID, USER_ID, ("owner", "admin"),
    )
    bundle_resolver.erp_runtime.assert_called_once_with()
    client.load_cached_token.assert_awaited_once_with()
    client.request_with_retry.assert_awaited_once_with(
        "erp.shop.list.query", {"pageNo": 1, "pageSize": 1},
    )
    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_wecom_test_uses_admin_bundle_and_owner_admin_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_service = MagicMock()
    bundle_resolver = MagicMock()
    bundle_resolver.wecom_bot_admin_test.return_value = (
        ResolvedConfigurationBundle(
            name="wecom.bot",
            values={
                "wecom.bot_credentials": {
                    "bot_id": "bot-id",
                    "bot_secret": "bot-secret",
                },
            },
            sources={},
            versions={},
        )
    )
    verify = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr(
        "services.wecom.ws_client.verify_bot_credentials",
        verify,
    )

    result = await run_wecom_connection_test(
        ORG_ID, USER_ID, org_service, bundle_resolver,
    )

    assert result == {
        "success": True,
        "message": "企微连接测试成功",
    }
    org_service.require_role.assert_called_once_with(
        ORG_ID, USER_ID, ("owner", "admin"),
    )
    bundle_resolver.wecom_bot_admin_test.assert_called_once_with()
    bundle_resolver.wecom_bot.assert_not_called()
    verify.assert_awaited_once_with("bot-id", "bot-secret")


@pytest.mark.asyncio
async def test_wecom_test_hides_bundle_and_database_details() -> None:
    org_service = MagicMock()
    bundle_resolver = MagicMock()
    bundle_resolver.wecom_bot_admin_test.side_effect = (
        ConfigurationResolutionError(
            "database wrapped_dek secret detail"
        )
    )

    result = await run_wecom_connection_test(
        ORG_ID, USER_ID, org_service, bundle_resolver,
    )

    assert result == {
        "success": False,
        "message": "企微配置不完整或不可用",
    }
    assert "database" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route",
    (run_erp_connection_test, run_wecom_connection_test),
)
async def test_connection_tests_reject_non_admin_before_bundle_resolution(
    route,
) -> None:
    org_service = MagicMock()
    org_service.require_role.side_effect = PermissionDeniedError(
        "无权执行此操作"
    )
    bundle_resolver = MagicMock()

    with pytest.raises(HTTPException) as captured:
        if route is run_erp_connection_test:
            await route(
                ORG_ID, USER_ID, org_service, bundle_resolver, MagicMock(),
            )
        else:
            await route(ORG_ID, USER_ID, org_service, bundle_resolver)

    assert getattr(captured.value, "status_code", None) == 403
    bundle_resolver.erp_runtime.assert_not_called()
    bundle_resolver.wecom_bot_admin_test.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_credential_route_encrypts_and_never_returns_token() -> None:
    org_service = MagicMock()
    db = MagicMock()
    control = MagicMock()
    scoped_control = MagicMock()
    scoped_control.mcp_credential_status.return_value = {"version": 2}
    scoped_control.set_mcp_organization_credential.return_value = {
        "configured": True, "version": 3,
    }
    control.for_mcp_actor.return_value = scoped_control
    secret = "synthetic-org-secret"

    result = await set_mcp_connector_credential(
        ORG_ID, MCPConnectorCredentialRequest(token=secret), USER_ID,
        db, org_service, control,
    )

    assert result == {"success": True, "data": {"configured": True, "version": 3}}
    assert secret not in str(result)
    control.for_mcp_actor.assert_called_once_with(
        org_id=ORG_ID, actor_user_id=USER_ID,
    )
    scoped_control.set_mcp_organization_credential.assert_called_once_with(
        org_id=ORG_ID,
        value={"token": secret}, expected_version=2,
    )
    org_service.require_role.assert_called_once_with(
        ORG_ID, USER_ID, ("owner", "admin"),
    )


@pytest.mark.asyncio
async def test_mcp_credential_status_and_revoke_never_expose_material() -> None:
    org_service = MagicMock()
    db = MagicMock()
    control = MagicMock()
    scoped_control = MagicMock()
    scoped_control.mcp_credential_status.return_value = {
        "configured": True, "version": 4, "token": "must-not-escape",
    }
    scoped_control.delete_mcp_organization_credential.return_value = {
        "configured": False, "version": 5, "token": "must-not-escape",
    }
    control.for_mcp_actor.return_value = scoped_control

    status = await get_mcp_connector_credential_status(
        ORG_ID, USER_ID, db, org_service, control,
    )
    revoked = await delete_mcp_connector_credential(
        ORG_ID, USER_ID, db, 4, org_service, control,
    )

    assert status == {"success": True, "data": {"configured": True, "version": 4}}
    assert revoked == {"success": True, "data": {"configured": False, "version": 5}}
    assert "must-not-escape" not in str(status) + str(revoked)
    scoped_control.delete_mcp_organization_credential.assert_called_once_with(
        org_id=ORG_ID, expected_version=4,
    )


@pytest.mark.asyncio
async def test_mcp_enable_and_status_use_fixed_connector_and_redacted_state(monkeypatch) -> None:
    org_service = MagicMock()
    db = MagicMock()
    db.org_id = ORG_ID
    monkeypatch.setattr("services.tools.mcp_org.scoped_mcp_database", lambda db, **_: db)
    state = {
        "org_id": ORG_ID, "connector_id": "test-readonly", "enabled": True,
        "state": "ready", "health_status": "ready", "last_error_code": None,
    }
    db.rpc.return_value.execute.return_value.data = state

    changed = await set_mcp_connector_enabled(
        ORG_ID, MCPConnectorEnableRequest(enabled=True), USER_ID, db, org_service,
    )
    viewed = await get_mcp_connector_status(ORG_ID, USER_ID, db, org_service)

    assert changed == {"success": True, "data": state}
    assert viewed == {"success": True, "data": state}
    assert db.rpc.call_args_list[0].args[0] == "api_set_org_mcp_connector_enabled"
    assert db.rpc.call_args_list[0].args[1]["p_connector_id"] == "test-readonly"
    assert db.rpc.call_args_list[0].args[1]["p_enabled"] is True


@pytest.mark.asyncio
async def test_mcp_connection_test_returns_health_without_credential(monkeypatch) -> None:
    org_service = MagicMock()
    db = MagicMock()
    db.org_id = ORG_ID
    monkeypatch.setattr("services.tools.mcp_org.scoped_mcp_database", lambda db, **_: db)
    state = {
        "org_id": ORG_ID, "connector_id": "test-readonly", "enabled": False,
        "state": "disabled", "health_status": "ready", "last_error_code": None,
    }
    db.rpc.return_value.execute.return_value.data = state
    secret = "synthetic-org-secret"
    bundle = MagicMock()
    bundle.values = {"mcp.test_readonly.bearer_token": {"token": secret}}
    resolver = MagicMock()
    resolver.mcp_test_readonly.return_value = bundle

    result = await run_mcp_connection_test(
        ORG_ID, USER_ID, db, org_service, resolver,
    )

    assert result == {"success": True, "data": state}
    assert secret not in str(result)
    resolver.mcp_test_readonly.assert_called_once_with(
        actor_user_id=USER_ID, org_id=ORG_ID,
    )


@pytest.mark.asyncio
async def test_mcp_connection_test_rejects_non_admin_before_secret_resolution() -> None:
    org_service = MagicMock()
    org_service.require_role.side_effect = PermissionDeniedError("forbidden")
    db = MagicMock()
    resolver = MagicMock()

    with pytest.raises(HTTPException) as captured:
        await run_mcp_connection_test(ORG_ID, USER_ID, db, org_service, resolver)

    assert captured.value.status_code == 403
    resolver.mcp_test_readonly.assert_not_called()
    db.rpc.assert_not_called()
