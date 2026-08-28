from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from api.routes import admin_user_assets


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def select(self, _fields):
        return self

    def eq(self, _field, _value):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return _Result({"id": "target-user"})


class _Db:
    def __init__(self):
        self.rpc_args = None

    def table(self, _name):
        return _Query()

    def rpc(self, name, args):
        self.rpc_args = (name, args)
        return _QueryWithPayload({
            "items": [{
                "id": "00000000-0000-4000-8000-000000000001",
                "created_at": "2026-08-21T00:00:00+00:00",
            }],
            "total": 1,
        })


class _QueryWithPayload:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return _Result(self.payload)


@pytest.mark.asyncio
async def test_list_user_assets_uses_legacy_admin_rpc(monkeypatch):
    monkeypatch.setattr(admin_user_assets, "_require_super_admin", lambda *_: None)
    db = _Db()

    result = await admin_user_assets.list_user_assets(
        uid="target-user",
        user_id="admin-user",
        db=db,
        source_type="upload",
        media_type=None,
        limit=24,
        cursor=None,
    )

    assert result["total"] == 1
    assert result["has_more"] is False
    assert db.rpc_args == (
        "list_platform_admin_user_assets",
        {
            "p_actor_user_id": "target-user",
            "p_source_type": "upload",
            "p_media_type": None,
            "p_limit": 25,
            "p_cursor_created_at": None,
            "p_cursor_id": None,
        },
    )


def test_asset_cursor_round_trip():
    created_at = datetime(2026, 8, 21, tzinfo=timezone.utc).isoformat()
    cursor = admin_user_assets._encode_cursor(
        created_at,
        "00000000-0000-4000-8000-000000000001",
    )
    assert admin_user_assets._decode_cursor(cursor) == (
        created_at,
        "00000000-0000-4000-8000-000000000001",
    )


def test_invalid_asset_cursor_returns_422():
    with pytest.raises(HTTPException) as error:
        admin_user_assets._decode_cursor("not-a-cursor")
    assert error.value.status_code == 422
