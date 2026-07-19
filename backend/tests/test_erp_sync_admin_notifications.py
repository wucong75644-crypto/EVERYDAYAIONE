"""ERP sync healthcheck administrator notification tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.kuaimai.erp_sync_healthcheck import _push_to_org_admins


class _AsyncQueryStub:
    def __init__(self, data: list[dict]) -> None:
        self._data = data

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def execute(self):
        return MagicMock(data=self._data)


class _DBStub:
    def __init__(self, table_data: dict[str, list[dict]]) -> None:
        self._tables = table_data

    def table(self, name: str) -> _AsyncQueryStub:
        return _AsyncQueryStub(self._tables.get(name, []))


class TestPushToOrgAdmins:
    @pytest.mark.asyncio
    async def test_queries_org_members_not_users_role(self) -> None:
        table_calls: list[str] = []

        class SpyDB:
            def table(self, name: str) -> _AsyncQueryStub:
                table_calls.append(name)
                return _AsyncQueryStub([])

        await _push_to_org_admins(SpyDB(), "org-test", "test msg")

        assert "org_members" in table_calls
        assert "users" not in table_calls

    @pytest.mark.asyncio
    async def test_no_admins_returns_silently(self) -> None:
        await _push_to_org_admins(
            _DBStub({"org_members": []}), "org-test", "test msg",
        )

    @pytest.mark.asyncio
    async def test_no_wecom_mapping_returns_silently(self) -> None:
        db = _DBStub({
            "org_members": [{"user_id": "u1", "role": "owner"}],
            "wecom_user_mappings": [],
        })

        await _push_to_org_admins(db, "org-test", "test msg")

    @pytest.mark.asyncio
    async def test_mapped_admin_uses_gateway_without_external_io(self) -> None:
        db = _DBStub({
            "org_members": [{"user_id": "u1", "role": "owner"}],
            "wecom_user_mappings": [{"wecom_userid": "wu1", "user_id": "u1"}],
        })
        gateway = MagicMock()
        gateway.save_system_message = AsyncMock(return_value=True)

        with patch("core.database.get_db", return_value=MagicMock()), patch(
            "services.message_gateway.MessageGateway",
            return_value=gateway,
        ):
            await _push_to_org_admins(db, "org-test", "test msg")

        gateway.save_system_message.assert_awaited_once_with(
            user_id="u1",
            org_id="org-test",
            text="test msg",
            source="error_alert",
        )
