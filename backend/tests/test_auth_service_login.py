"""AuthService Web 注册与登录 RPC 测试。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from core.security import TokenMaterial
from services.auth_service import AuthService
from testing.auth_test_support import auth_user


def _material() -> TokenMaterial:
    return TokenMaterial(
        access_token="access",
        refresh_token="refresh",
        refresh_token_hash="a" * 64,
        refresh_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        access_expires_in=1800,
        refresh_expires_in=604800,
    )


def _rpc_db(results: dict[str, object]) -> MagicMock:
    db = MagicMock()

    def rpc(name, params):
        caller = MagicMock()
        caller.execute.return_value = MagicMock(data=results.get(name))
        return caller

    db.rpc.side_effect = rpc
    return db


def _service(db, mock_settings) -> AuthService:
    with patch("services.auth_service.get_settings", return_value=mock_settings):
        return AuthService(db)


@pytest.mark.asyncio
async def test_register_uses_atomic_rpc_and_binds_token_to_returned_user(
    mock_settings,
):
    material = _material()
    user = auth_user(id="generated-user-id", password_hash=None)
    db = _rpc_db({"register_web_identity": user})
    service = _service(db, mock_settings)

    with (
        patch.object(service, "_verify_code", new=AsyncMock(return_value=True)),
        patch(
            "services.auth_service.uuid4",
            return_value="generated-user-id",
        ),
        patch(
            "services.auth_service.create_token_material",
            return_value=material,
        ),
    ):
        result = await service.register_by_phone(
            "13800138000", "123456", "测试用户",
        )

    assert result["token"]["refresh_token"] == "refresh"
    assert db.rpc.call_args.args[0] == "register_web_identity"
    assert db.rpc.call_args.args[1]["p_user_id"] == user["id"]


@pytest.mark.asyncio
async def test_register_invalid_code_does_not_call_database(mock_settings):
    db = MagicMock()
    service = _service(db, mock_settings)

    with patch.object(
        service, "_verify_code", new=AsyncMock(return_value=False),
    ):
        with pytest.raises(ValidationError, match="验证码"):
            await service.register_by_phone("13800138000", "bad")

    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_register_maps_phone_conflict(mock_settings):
    db = MagicMock()
    db.rpc.side_effect = RuntimeError("WEB_AUTH_PHONE_CONFLICT")
    service = _service(db, mock_settings)

    with patch.object(
        service, "_verify_code", new=AsyncMock(return_value=True),
    ):
        with pytest.raises(ConflictError, match="已注册"):
            await service.register_by_phone("13800138000", "123456")


@pytest.mark.asyncio
async def test_phone_login_uses_lookup_and_commit(mock_settings):
    candidate = auth_user()
    committed = auth_user(id=candidate["id"])
    db = _rpc_db({
        "lookup_web_auth_candidate": candidate,
        "commit_web_login": committed,
    })
    service = _service(db, mock_settings)

    with (
        patch.object(service, "_verify_code", new=AsyncMock(return_value=True)),
        patch(
            "services.auth_service.create_token_material",
            return_value=_material(),
        ),
    ):
        result = await service.login_by_phone("13800138000", "123456")

    assert result["user"]["id"] == candidate["id"]
    assert [call.args[0] for call in db.rpc.call_args_list] == [
        "lookup_web_auth_candidate", "commit_web_login",
    ]


@pytest.mark.asyncio
async def test_phone_login_missing_user_preserves_not_found(mock_settings):
    service = _service(
        _rpc_db({"lookup_web_auth_candidate": None}), mock_settings,
    )

    with patch.object(
        service, "_verify_code", new=AsyncMock(return_value=True),
    ):
        with pytest.raises(NotFoundError):
            await service.login_by_phone("13800138000", "123456")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate", "password_ok", "message"),
    [
        (None, True, "密码错误"),
        (auth_user(password_hash=None), True, "未设置密码"),
        (auth_user(), False, "密码错误"),
        (auth_user(status="disabled"), True, "禁用"),
    ],
)
async def test_password_login_rejects_invalid_candidate(
    mock_settings, candidate, password_ok, message,
):
    service = _service(
        _rpc_db({"lookup_web_auth_candidate": candidate}), mock_settings,
    )

    with patch(
        "services.auth_service.verify_password", return_value=password_ok,
    ):
        with pytest.raises(AuthenticationError, match=message):
            await service.login_by_password("13800138000", "password")


@pytest.mark.asyncio
async def test_password_login_success_commits_atomically(mock_settings):
    candidate = auth_user()
    db = _rpc_db({
        "lookup_web_auth_candidate": candidate,
        "commit_web_login": candidate,
    })
    service = _service(db, mock_settings)

    with (
        patch("services.auth_service.verify_password", return_value=True),
        patch(
            "services.auth_service.create_token_material",
            return_value=_material(),
        ),
    ):
        result = await service.login_by_password(
            "13800138000", "password",
        )

    assert result["token"]["access_token"] == "access"


@pytest.mark.asyncio
async def test_login_maps_principal_race_to_authentication_error(mock_settings):
    candidate = auth_user()
    db = _rpc_db({"lookup_web_auth_candidate": candidate})
    db.rpc.side_effect = [
        db.rpc.side_effect("lookup_web_auth_candidate", {}),
        RuntimeError("WEB_AUTH_PRINCIPAL_INACTIVE"),
    ]
    service = _service(db, mock_settings)

    with (
        patch("services.auth_service.verify_password", return_value=True),
        patch(
            "services.auth_service.create_token_material",
            return_value=_material(),
        ),
    ):
        with pytest.raises(AuthenticationError, match="状态已变更"):
            await service.login_by_password("13800138000", "password")


@pytest.mark.asyncio
async def test_org_login_returns_committed_org_context(mock_settings):
    candidate = auth_user(
        org_id="11111111-1111-1111-1111-111111111111",
        org_name="测试企业",
        org_status="active",
        org_role="member",
        member_status="active",
    )
    db = _rpc_db({
        "lookup_web_auth_candidate": candidate,
        "commit_web_login": candidate,
    })
    service = _service(db, mock_settings)

    with (
        patch("services.auth_service.verify_password", return_value=True),
        patch(
            "services.auth_service.create_token_material",
            return_value=_material(),
        ),
    ):
        result = await service.login_by_org_password(
            "测试企业", "13800138000", "password",
        )

    assert result["org"]["org_name"] == "测试企业"
    assert db.rpc.call_args_list[1].args[1]["p_org_id"] == candidate["org_id"]


@pytest.mark.asyncio
async def test_org_login_uses_uniform_failure(mock_settings):
    candidate = auth_user(
        org_id=None,
        org_name=None,
        org_status=None,
        org_role=None,
        member_status=None,
    )
    service = _service(
        _rpc_db({"lookup_web_auth_candidate": candidate}), mock_settings,
    )

    with pytest.raises(AuthenticationError, match="企业名称、手机号或密码错误"):
        await service.login_by_org_password(
            "不存在", "13800138000", "password",
        )
