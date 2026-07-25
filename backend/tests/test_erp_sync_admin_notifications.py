"""ERP sync healthcheck administrator notification tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.kuaimai.erp_sync_healthcheck import _push_to_org_admins


class TestPushToOrgAdmins:
    @pytest.mark.asyncio
    async def test_uses_narrow_org_alert_capability(self) -> None:
        send = AsyncMock(return_value=True)
        db = object()
        with patch(
            "services.sync_alert_service.send_org_alert",
            send,
        ):
            await _push_to_org_admins(db, "org-test", "test msg")

        send.assert_awaited_once_with(db, "org-test", "test msg")

    @pytest.mark.asyncio
    async def test_no_recipient_returns_silently(self) -> None:
        with patch(
            "services.sync_alert_service.send_org_alert",
            new=AsyncMock(return_value=False),
        ):
            await _push_to_org_admins(
                object(), "org-test", "test msg",
            )
