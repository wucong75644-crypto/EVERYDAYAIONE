"""Organization configuration routes use only the formal control plane."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from api.routes.org import (
    SetConfigRequest,
    list_org_configs,
    set_org_config,
    test_erp_connection as run_erp_connection_test,
)
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
