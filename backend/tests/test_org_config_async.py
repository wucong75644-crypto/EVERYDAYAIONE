"""Compatibility tests for the governed asynchronous ERP resolver."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.configuration.sync_resolver import SyncErpCredentials
from services.org.config_resolver import AsyncOrgConfigResolver


def _credentials(version: int = 4) -> SyncErpCredentials:
    return SyncErpCredentials(
        org_id="org-1",
        app_key="app-key",
        app_secret="app-secret",
        access_token="access-token",
        refresh_token="refresh-token",
        warehouse_ids=("WH-1", "WH-2"),
        token_version=version,
    )


@pytest.fixture
def resolver():
    with patch(
        "services.configuration.sync_resolver.SyncConfigurationResolver"
    ) as resolver_type:
        governed = resolver_type.return_value
        governed.erp_credentials = AsyncMock(return_value=_credentials())
        governed.commit_erp_token_pair = AsyncMock(return_value=5)
        yield AsyncOrgConfigResolver(MagicMock()), governed


@pytest.mark.asyncio
async def test_get_legacy_erp_key_from_governed_bundle(resolver):
    adapter, governed = resolver

    assert await adapter.get("org-1", "kuaimai_app_key") == "app-key"
    assert await adapter.get("org-1", "erp_warehouse_ids") == "WH-1,WH-2"
    governed.erp_credentials.assert_awaited_once_with("org-1")


@pytest.mark.asyncio
async def test_erp_credentials_preserve_legacy_shape(resolver):
    adapter, _ = resolver

    assert await adapter.get_erp_credentials("org-1") == {
        "kuaimai_app_key": "app-key",
        "kuaimai_app_secret": "app-secret",
        "kuaimai_access_token": "access-token",
        "kuaimai_refresh_token": "refresh-token",
    }


@pytest.mark.asyncio
async def test_personal_context_never_receives_enterprise_secret(resolver):
    adapter, _ = resolver

    assert await adapter.get(None, "kuaimai_app_key") is None


@pytest.mark.asyncio
async def test_unknown_key_is_not_exposed(resolver):
    adapter, _ = resolver

    assert await adapter.get("org-1", "unknown") is None


@pytest.mark.asyncio
async def test_update_token_uses_atomic_sync_capability_and_invalidates_cache(
    resolver,
):
    adapter, governed = resolver
    await adapter.get_erp_credentials("org-1")

    await adapter.update_erp_token("org-1", "new-access", "new-refresh")

    governed.commit_erp_token_pair.assert_awaited_once_with(
        _credentials(),
        "new-access",
        "new-refresh",
    )
    assert "org-1" not in adapter._erp_cache
