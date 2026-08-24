"""WebSocket 认证拒绝必须返回浏览器可观察的业务关闭码。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes.ws import websocket_endpoint
from core.db_scope import DatabaseAccessKind, database_scope_from_client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "expected_code"),
    (("invalid", 4001), ("expired", 4002)),
)
async def test_auth_rejection_accepts_before_closing(
    error_type: str,
    expected_code: int,
) -> None:
    websocket = AsyncMock()

    with patch(
        "api.routes.ws.get_user_from_token",
        new=AsyncMock(return_value=(None, error_type)),
    ):
        await websocket_endpoint(websocket, token="secret")

    websocket.accept.assert_awaited_once_with()
    websocket.close.assert_awaited_once()
    assert websocket.close.await_args.kwargs["code"] == expected_code
    assert (
        websocket.accept.await_count == websocket.close.await_count == 1
    )


@pytest.mark.asyncio
async def test_org_rejection_returns_business_close_code() -> None:
    websocket = AsyncMock()
    db = MagicMock()
    organization_query = (
        db.table.return_value.select.return_value.eq.return_value
        .maybe_single.return_value
    )
    organization_query.execute.return_value.data = {"status": "active"}
    member_query = (
        db.table.return_value.select.return_value.eq.return_value.eq.return_value
        .maybe_single.return_value
    )
    member_query.execute.return_value.data = None

    with (
        patch(
            "api.routes.ws.get_user_from_token",
            new=AsyncMock(return_value=("user-1", "")),
        ),
        patch("api.routes.ws._build_connection_db", return_value=db),
    ):
        await websocket_endpoint(
            websocket,
            token="secret",
            org_id="org-1",
        )

    websocket.accept.assert_awaited_once_with()
    websocket.close.assert_awaited_once_with(
        code=4003,
        reason="Organization access denied",
    )


@pytest.mark.asyncio
async def test_suspended_org_rejection_happens_before_member_access() -> None:
    websocket = AsyncMock()
    db = MagicMock()
    organization_query = (
        db.table.return_value.select.return_value.eq.return_value
        .maybe_single.return_value
    )
    organization_query.execute.return_value.data = {"status": "suspended"}

    with (
        patch(
            "api.routes.ws.get_user_from_token",
            new=AsyncMock(return_value=("user-1", "")),
        ),
        patch("api.routes.ws._build_connection_db", return_value=db),
    ):
        await websocket_endpoint(
            websocket,
            token="secret",
            org_id="org-1",
        )

    websocket.accept.assert_awaited_once_with()
    websocket.close.assert_awaited_once_with(
        code=4003,
        reason="Organization access denied",
    )
    assert db.table.call_count == 1


def test_connection_database_carries_verified_runtime_scope() -> None:
    from api.routes.ws import _build_connection_db

    user_id = "469dbe1e-954f-423b-89d2-1d8ea6ecace9"
    org_id = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"
    with patch("api.routes.ws.get_db", return_value=MagicMock()):
        db = _build_connection_db(
            user_id, org_id, request_id="ws:connection-1",
        )

    scope = database_scope_from_client(db)
    assert scope is not None
    assert scope.actor_user_id == user_id
    assert scope.org_id == org_id
    assert scope.access_kind == DatabaseAccessKind.RUNTIME
