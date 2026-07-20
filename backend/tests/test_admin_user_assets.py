"""管理员统一用户资产游标 API 测试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from api.routes.admin_user_assets import (
    _decode_cursor,
    _encode_cursor,
    list_user_assets,
)
from api.routes.admin_users_zip import _is_allowed_asset_url


ADMIN_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
ASSET_1 = "00000000-0000-0000-0000-000000000011"
ASSET_2 = "00000000-0000-0000-0000-000000000012"
ASSET_3 = "00000000-0000-0000-0000-000000000013"
CREATED_AT = "2026-07-20T10:00:00+00:00"


class _Query:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count
        self.calls: list[tuple[str, tuple, dict]] = []
        self.single = False

    def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return self

    def select(self, *args, **kwargs):
        return self._call("select", *args, **kwargs)

    def eq(self, *args, **kwargs):
        return self._call("eq", *args, **kwargs)

    def or_(self, *args, **kwargs):
        return self._call("or_", *args, **kwargs)

    def order(self, *args, **kwargs):
        return self._call("order", *args, **kwargs)

    def limit(self, *args, **kwargs):
        return self._call("limit", *args, **kwargs)

    def maybe_single(self):
        self.single = True
        return self

    def execute(self):
        data = self.data
        if self.single and isinstance(data, list):
            data = data[0] if data else None
        return SimpleNamespace(data=data, count=self.count)


class _DB:
    def __init__(self, *queries: _Query, rpc_query: _Query | None = None):
        self.queries = list(queries)
        self.rpc_query = rpc_query
        self.tables: list[str] = []
        self.rpc_calls: list[tuple[str, dict]] = []

    def table(self, name: str):
        self.tables.append(name)
        return self.queries.pop(0)

    def rpc(self, name: str, params: dict):
        self.rpc_calls.append((name, params))
        assert self.rpc_query is not None
        return self.rpc_query


def _authorized_db(rows, *, total=0) -> _DB:
    db = _DB(
        _Query({"role": "super_admin"}),
        _Query({"id": USER_ID}),
        rpc_query=_Query({"items": rows, "total": total}),
    )
    return db


@pytest.mark.asyncio
async def test_list_assets_returns_stable_cursor_without_message_scan() -> None:
    rows = [
        {"id": ASSET_3, "created_at": CREATED_AT, "media_type": "image"},
        {"id": ASSET_2, "created_at": CREATED_AT, "media_type": "image"},
        {"id": ASSET_1, "created_at": CREATED_AT, "media_type": "image"},
    ]
    db = _authorized_db(rows, total=3)

    response = await list_user_assets(
        USER_ID,
        ADMIN_ID,
        db,
        source_type="generated",
        media_type="image",
        limit=2,
        cursor=None,
    )

    assert [item["id"] for item in response["items"]] == [
        ASSET_3, ASSET_2,
    ]
    assert response["has_more"] is True
    assert response["total"] == 3
    assert _decode_cursor(response["next_cursor"]) == (
        CREATED_AT, ASSET_2,
    )
    assert db.tables == ["users", "users"]
    assert db.rpc_calls == [(
        "list_admin_user_assets",
        {
            "p_actor_user_id": USER_ID,
            "p_source_type": "generated",
            "p_media_type": "image",
            "p_limit": 3,
            "p_cursor_created_at": None,
            "p_cursor_id": None,
        },
    )]


@pytest.mark.asyncio
async def test_list_assets_applies_composite_cursor_filter() -> None:
    cursor = _encode_cursor(CREATED_AT, ASSET_2)
    db = _authorized_db([], total=3)

    response = await list_user_assets(
        USER_ID,
        ADMIN_ID,
        db,
        source_type="generated",
        media_type=None,
        limit=24,
        cursor=cursor,
    )

    assert db.rpc_calls[0][1]["p_cursor_created_at"] == CREATED_AT
    assert db.rpc_calls[0][1]["p_cursor_id"] == ASSET_2
    assert response["items"] == []
    assert response["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_assets_rejects_malformed_rpc_result() -> None:
    db = _DB(
        _Query({"role": "super_admin"}),
        _Query({"id": USER_ID}),
        rpc_query=_Query({"items": None, "total": "3"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await list_user_assets(
            USER_ID,
            ADMIN_ID,
            db,
            source_type="upload",
            media_type=None,
            limit=24,
            cursor=None,
        )

    assert exc_info.value.status_code == 500


@pytest.mark.parametrize("cursor", [
    "not-base64",
    _encode_cursor("2026-07-20T10:00:00", ASSET_1),
])
def test_decode_cursor_rejects_malformed_or_naive_timestamp(cursor) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _decode_cursor(cursor)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_list_assets_requires_super_admin() -> None:
    db = _DB(_Query({"role": "user"}))

    with pytest.raises(HTTPException) as exc_info:
        await list_user_assets(
            USER_ID,
            ADMIN_ID,
            db,
            source_type="upload",
            media_type=None,
            limit=24,
            cursor=None,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_list_assets_rejects_missing_target_user() -> None:
    db = _DB(
        _Query({"role": "super_admin"}),
        _Query(None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await list_user_assets(
            USER_ID,
            ADMIN_ID,
            db,
            source_type="upload",
            media_type=None,
            limit=24,
            cursor=None,
        )

    assert exc_info.value.status_code == 404


def test_zip_url_guard_rejects_http_and_untrusted_hosts(monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "oss_cdn_domain", "cdn.example.com")

    assert _is_allowed_asset_url(
        "https://cdn.example.com/path/image.png",
    ) is True
    assert _is_allowed_asset_url(
        "http://cdn.example.com/path/image.png",
    ) is False
    assert _is_allowed_asset_url(
        "https://cdn.example.com.attacker.test/image.png",
    ) is False
    assert _is_allowed_asset_url(
        "https://127.0.0.1/internal",
    ) is False
