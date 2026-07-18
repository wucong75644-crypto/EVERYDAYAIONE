"""管理员用户活跃排序契约测试。"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api.routes.admin_users import list_users


class _RecordingUsersQuery:
    def __init__(self) -> None:
        self.order_calls: list[tuple[str, dict]] = []

    def select(self, *args, **kwargs):
        return self

    def order(self, column: str, **kwargs):
        self.order_calls.append((column, kwargs))
        return self

    def range(self, *args):
        return self

    def execute(self):
        return SimpleNamespace(data=[], count=0)


class _UsersDB:
    def __init__(self) -> None:
        self.query = _RecordingUsersQuery()

    def table(self, name: str):
        assert name == "users"
        return self.query


@pytest.mark.asyncio
async def test_list_users_orders_by_latest_activity_with_nulls_last():
    db = _UsersDB()

    with patch("api.routes.admin_users._require_super_admin"):
        result = await list_users(
            user_id="admin-1",
            db=db,
            search=None,
            org_id=None,
            page=1,
            page_size=20,
        )

    assert result["items"] == []
    assert db.query.order_calls == [
        ("last_active_at", {"desc": True, "nulls_first": False}),
        ("created_at", {"desc": True}),
    ]
