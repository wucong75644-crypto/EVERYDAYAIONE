"""AuthService password、refresh 与 logout RPC 测试。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from core.exceptions import AuthenticationError, NotFoundError, ValidationError
from services.auth_service import AuthService
from testing.auth_test_support import auth_user


def _service(db, mock_settings) -> AuthService:
    with patch("services.auth_service.get_settings", return_value=mock_settings):
        return AuthService(db)


def _rpc_db(results: dict[str, object]) -> MagicMock:
    db = MagicMock()

    def rpc(name, params):
        caller = MagicMock()
        caller.execute.return_value = MagicMock(data=results.get(name))
        return caller

    db.rpc.side_effect = rpc
    return db


@pytest.mark.asyncio
async def test_reset_password_uses_lookup_then_atomic_reset(mock_settings):
    db = _rpc_db({
        "lookup_web_auth_candidate": auth_user(),
        "reset_web_password": True,
    })
    service = _service(db, mock_settings)

    with (
        patch.object(service, "_verify_code", new=AsyncMock(return_value=True)),
        patch("services.auth_service.hash_password", return_value="new-hash"),
    ):
        result = await service.reset_password(
            "13800138000", "123456", "new-password",
        )

    assert result == {"message": "密码重置成功"}
    assert db.rpc.call_args_list[1].args == (
        "reset_web_password",
        {"p_phone": "13800138000", "p_password_hash": "new-hash"},
    )


@pytest.mark.asyncio
async def test_reset_password_missing_user_does_not_consume_code(mock_settings):
    service = _service(
        _rpc_db({"lookup_web_auth_candidate": None}), mock_settings,
    )
    service._verify_code = AsyncMock()

    with pytest.raises(NotFoundError):
        await service.reset_password("13800138000", "123456", "new")

    service._verify_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_password_invalid_code_does_not_write(mock_settings):
    db = _rpc_db({"lookup_web_auth_candidate": auth_user()})
    service = _service(db, mock_settings)

    with patch.object(
        service, "_verify_code", new=AsyncMock(return_value=False),
    ):
        with pytest.raises(ValidationError, match="验证码"):
            await service.reset_password("13800138000", "bad", "new")

    assert [call.args[0] for call in db.rpc.call_args_list] == [
        "lookup_web_auth_candidate",
    ]


@pytest.mark.asyncio
async def test_refresh_rotated_returns_token_for_database_user(mock_settings):
    user_id = str(uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db = _rpc_db({
        "rotate_web_refresh_token": {
            "outcome": "rotated", "user_id": user_id,
        },
    })
    service = _service(db, mock_settings)

    with (
        patch(
            "services.auth_service.create_refresh_token",
            return_value=("new-refresh", "b" * 64, expires_at),
        ),
        patch(
            "services.auth_service.create_token_material_from_refresh",
        ) as complete,
    ):
        complete.return_value.response.return_value = {
            "access_token": "new-access",
        }
        result = await service.refresh_access_token("old-refresh")

    assert result["token"]["access_token"] == "new-access"
    assert complete.call_args.args[0] == user_id
    params = db.rpc.call_args.args[1]
    assert params["p_new_hash"] == "b" * 64
    assert params["p_old_hash"] != "old-refresh"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        ("invalid", "无效"),
        ("reuse", "已失效"),
        ("expired", "已过期"),
        ("inactive", "禁用"),
        ("unexpected", "无效"),
    ],
)
async def test_refresh_maps_database_outcomes(
    mock_settings, outcome, message,
):
    service = _service(
        _rpc_db({"rotate_web_refresh_token": {"outcome": outcome}}),
        mock_settings,
    )

    with pytest.raises(AuthenticationError, match=message):
        await service.refresh_access_token("old-refresh")


def test_revoke_refresh_token_hashes_before_rpc(mock_settings):
    db = _rpc_db({"revoke_web_refresh_token": True})
    service = _service(db, mock_settings)

    service.revoke_refresh_token("raw-refresh")

    name, params = db.rpc.call_args.args
    assert name == "revoke_web_refresh_token"
    assert params["p_token_hash"] != "raw-refresh"
    assert len(params["p_token_hash"]) == 64
