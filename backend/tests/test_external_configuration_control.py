"""Kuaimai external credential control-plane tests."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.configuration.external_control import (
    ExternalConfigurationControl,
)


def _caller(data):
    caller = MagicMock()
    caller.execute = AsyncMock(return_value=MagicMock(data=data))
    return caller


def _statuses():
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "key": f"kuaimai_external.thinktank.{suffix}",
            "configured": True,
            "version": version,
            "updated_at": now,
        }
        for suffix, version in (("cookie", 2), ("company_id", 3))
    ]


@pytest.mark.asyncio
async def test_set_uses_atomic_two_entry_rpc():
    db = MagicMock()
    db.rpc.side_effect = lambda name, params=None: _caller(
        _statuses()
        if name == "list_org_configuration_status"
        else {"source": "thinktank"}
    )
    secrets = MagicMock()
    secrets.encrypt_payload.return_value = MagicMock(
        payload_ciphertext="cipher",
        wrapped_dek="dek",
        kek_version="v1",
    )
    control = ExternalConfigurationControl(db, secrets)
    resolved = MagicMock(
        values={
            "kuaimai_external.thinktank.cookie": {
                "censeid_cookie": "cookie",
                "cookie_full": "full",
            },
            "kuaimai_external.thinktank.company_id": "123",
        }
    )
    with patch(
        "services.configuration.external_control.AsyncSecretBundleResolver"
    ) as resolver:
        resolver.return_value.kuaimai_thinktank = AsyncMock(
            return_value=resolved
        )
        await control.set(
            org_id="org-1",
            source="thinktank",
            company_id=123,
            censeid_cookie="cookie",
            cookie_full="full",
        )

    atomic_call = [
        call for call in db.rpc.call_args_list
        if call.args[0] == "runtime_set_external_configuration"
    ][0]
    params = atomic_call.args[1]
    assert params["p_expected_cookie_version"] == 2
    assert params["p_expected_company_version"] == 3
    assert params["p_company_id"] == "123"


@pytest.mark.asyncio
async def test_delete_refuses_unconfigured_bundle_without_rpc():
    db = MagicMock()
    db.rpc.return_value = _caller([])
    control = ExternalConfigurationControl(db, MagicMock())

    assert await control.delete(
        org_id="org-1",
        source="thinktank",
    ) is False
    assert all(
        call.args[0] != "runtime_delete_external_configuration"
        for call in db.rpc.call_args_list
    )
