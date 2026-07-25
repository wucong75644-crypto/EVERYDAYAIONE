"""WebSocket 认证拒绝必须返回浏览器可观察的业务关闭码。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes.ws import websocket_endpoint


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
    query = (
        db.table.return_value.select.return_value.eq.return_value.eq.return_value
        .maybe_single.return_value
    )
    query.execute.return_value.data = None

    with (
        patch(
            "api.routes.ws.get_user_from_token",
            new=AsyncMock(return_value=("user-1", "")),
        ),
        patch("api.routes.ws.get_db", return_value=db),
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
