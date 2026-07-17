from types import SimpleNamespace

import pytest

from services.handlers.chat.execution_scope import resolve_execution_scope


class _Query:
    def __init__(self, row):
        self.row = row

    def select(self, _fields):
        return self

    def eq(self, _field, _value):
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return SimpleNamespace(data=self.row)


class _DB:
    def __init__(self, row):
        self.row = row

    def table(self, name):
        assert name == "conversations"
        return _Query(self.row)


@pytest.mark.asyncio
async def test_user_scope_keeps_actor_as_workspace_owner() -> None:
    scope = await resolve_execution_scope(
        _DB({
            "id": "conv-1",
            "org_id": "org-1",
            "user_id": "user-1",
            "source": "wecom",
            "scope_type": "user",
            "scope_id": "user-1",
        }),
        {"user_id": "user-1", "org_id": "org-1"},
        "conv-1",
    )

    assert scope.workspace_owner_id == "user-1"
    assert scope.personal_context_allowed is True


@pytest.mark.asyncio
async def test_channel_scope_requires_wecom_group_delivery() -> None:
    row = {
        "id": "conv-1",
        "org_id": "org-1",
        "user_id": None,
        "source": "wecom",
        "scope_type": "channel",
        "scope_id": "group-1",
    }
    task = {
        "user_id": "user-1",
        "org_id": "org-1",
        "delivery_context": {
            "channel": "wecom",
            "chattype": "single",
            "corp_id": "corp-1",
            "chatid": "group-1",
        },
    }

    with pytest.raises(RuntimeError, match="ACTOR_EXECUTION_SCOPE_MISMATCH"):
        await resolve_execution_scope(_DB(row), task, "conv-1")
