"""企微联系人查询使用受控配置 Bundle。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.wecom.wecom_contact_api import fetch_wecom_real_name


@pytest.mark.asyncio
async def test_contact_lookup_uses_fixed_bundle_without_table_reads() -> None:
    db = MagicMock()
    bundle = MagicMock(values={
        "wecom.corp_id": "corp-1",
        "wecom.oauth_agent_secret": {"agent_secret": "secret-1"},
    })
    response = MagicMock()
    response.json.return_value = {"errcode": 0, "name": "王五"}

    with (
        patch(
            "services.wecom.wecom_contact_api.SecretBundleResolver"
        ) as resolver,
        patch(
            "services.wecom.wecom_contact_api.LocalKEKProvider.from_environment",
            return_value=MagicMock(),
        ),
        patch(
            "services.wecom.wecom_contact_api.get_access_token",
            new=AsyncMock(return_value="token"),
        ),
        patch("httpx.AsyncClient.get", new=AsyncMock(return_value=response)),
    ):
        resolver.return_value.wecom_contact.return_value = bundle
        result = await fetch_wecom_real_name(db, "org-1", "user-1")

    assert result == "王五"
    resolver.return_value.wecom_contact.assert_called_once_with()
    db.table.assert_not_called()


@pytest.mark.asyncio
async def test_contact_lookup_fails_closed_when_bundle_is_incomplete() -> None:
    db = MagicMock()
    bundle = MagicMock(values={
        "wecom.corp_id": "corp-1",
        "wecom.oauth_agent_secret": None,
    })

    with patch(
        "services.wecom.wecom_contact_api.SecretBundleResolver"
    ) as resolver, patch(
        "services.wecom.wecom_contact_api.LocalKEKProvider.from_environment",
        return_value=MagicMock(),
    ):
        resolver.return_value.wecom_contact.return_value = bundle
        result = await fetch_wecom_real_name(db, "org-1", "user-1")

    assert result is None
    db.table.assert_not_called()
