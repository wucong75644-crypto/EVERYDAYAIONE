"""WeCom OAuth migration 155 Python capability client tests."""

import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.crypto import aes_encrypt
from core.exceptions import ConflictError, PermissionDeniedError, ValidationError
from services.wecom.oauth_identity_service import WecomOAuthIdentityService


ORG_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
KEY = base64.b64encode(b"k" * 32).decode()


def _service(*, actor: bool = False) -> tuple[WecomOAuthIdentityService, MagicMock]:
    raw_db = MagicMock()
    scoped_db = MagicMock()
    settings = MagicMock()
    settings.org_config_encrypt_key = KEY
    with (
        patch(
            "services.wecom.oauth_identity_service.ScopedDatabaseClient",
            return_value=scoped_db,
        ),
        patch(
            "services.wecom.oauth_identity_service.get_settings",
            return_value=settings,
        ),
    ):
        if actor:
            service = WecomOAuthIdentityService.for_actor(
                raw_db, user_id=USER_ID, org_id=ORG_ID, request_id="req-1",
            )
        else:
            service = WecomOAuthIdentityService.for_login(
                raw_db, org_id=ORG_ID, request_id="req-1",
            )
    return service, scoped_db


def _rpc_result(db: MagicMock, data: dict) -> None:
    db.rpc.return_value.execute.return_value.data = data


def _user() -> dict:
    return {
        "id": USER_ID,
        "nickname": "张三",
        "avatar_url": None,
        "phone": "13800138000",
        "login_methods": ["phone", "wecom"],
        "role": "user",
        "credits": 100,
        "created_at": "2026-07-24T00:00:00+00:00",
        "org_id": ORG_ID,
        "org_name": "示例企业",
        "org_role": "member",
    }


def test_login_and_actor_factories_create_distinct_scopes() -> None:
    login, _ = _service()
    actor, _ = _service(actor=True)
    assert login.scope.actor_user_id is None
    assert login.scope.org_id == ORG_ID
    assert actor.scope.actor_user_id == USER_ID
    assert actor.scope.org_id == ORG_ID
    assert actor.scope.request_id == "req-1"


def test_public_and_exchange_config_decrypt_exact_rpc_values() -> None:
    service, db = _service()
    _rpc_result(db, {
        "corp_id": "ww-corp",
        "agent_id_encrypted": aes_encrypt("1000006", KEY),
        "encrypt_key": KEY,
    })
    assert service.get_public_config() == {
        "corp_id": "ww-corp", "agent_id": "1000006",
    }
    _rpc_result(db, {
        "corp_id": "ww-corp",
        "agent_secret_encrypted": aes_encrypt("secret", KEY),
        "encrypt_key": KEY,
    })
    assert service.get_exchange_config()["agent_secret"] == "secret"


def test_config_missing_or_invalid_is_rejected() -> None:
    service, db = _service()
    _rpc_result(db, {"corp_id": "ww-corp"})
    with pytest.raises(ValidationError, match="Agent ID"):
        service.get_public_config()
    _rpc_result(db, {
        "corp_id": "ww-corp",
        "agent_secret_encrypted": "invalid",
        "encrypt_key": KEY,
    })
    with pytest.raises(ValidationError, match="无法解密"):
        service.get_exchange_config()


def test_login_commits_refresh_hash_and_builds_public_token() -> None:
    service, db = _service()
    user = _user()
    _rpc_result(db, user)
    expires = datetime.now(timezone.utc) + timedelta(days=1)
    with (
        patch(
            "services.wecom.oauth_identity_service.create_refresh_token",
            return_value=("raw-refresh", "a" * 64, expires),
        ),
        patch(
            "services.wecom.oauth_identity_service.create_token_material_from_refresh",
        ) as material,
    ):
        material.return_value.response.return_value = {
            "access_token": "access", "refresh_token": "raw-refresh",
        }
        result = service.login_or_create(
            wecom_userid="zhangsan", corp_id="ww-corp",
        )
    params = db.rpc.call_args.args[1]
    assert db.rpc.call_args.args[0] == "commit_web_wecom_login"
    assert params["p_refresh_hash"] == "a" * 64
    assert params["p_org_id"] == ORG_ID
    assert result["org"]["org_id"] == ORG_ID
    assert result["user"]["phone"] == "138****8000"


def test_bind_uses_actor_rpc_and_never_reports_merge() -> None:
    service, db = _service(actor=True)
    _rpc_result(db, _user())
    expires = datetime.now(timezone.utc) + timedelta(days=1)
    with (
        patch(
            "services.wecom.oauth_identity_service.create_refresh_token",
            return_value=("raw", "b" * 64, expires),
        ),
        patch(
            "services.wecom.oauth_identity_service.create_token_material_from_refresh",
        ) as material,
    ):
        material.return_value.response.return_value = {"access_token": "access"}
        result = service.bind_account(
            wecom_userid="zhangsan", corp_id="ww-corp",
        )
    assert db.rpc.call_args.args[0] == "bind_web_wecom_identity"
    assert result["merged"] is False


def test_unbind_and_status_use_only_facade_rpcs() -> None:
    service, db = _service(actor=True)
    _rpc_result(db, {"success": True})
    assert service.unbind_account()["success"] is True
    assert db.rpc.call_args.args[0] == "unbind_web_wecom_identity"
    _rpc_result(db, {"bound": False, "wecom_nickname": None, "bound_at": None})
    assert service.get_binding_status()["bound"] is False
    assert db.rpc.call_args.args[0] == "get_web_wecom_binding_status"
    db.table.assert_not_called()


@pytest.mark.parametrize(
    ("database_error", "exception"),
    [
        ("WEB_WECOM_OAUTH_MERGE_REVIEW_REQUIRED", ConflictError),
        ("WEB_WECOM_OAUTH_PRINCIPAL_INACTIVE", PermissionDeniedError),
        ("WEB_WECOM_OAUTH_BINDING_MISSING", ValidationError),
    ],
)
def test_database_errors_map_to_business_errors(
    database_error: str,
    exception: type[Exception],
) -> None:
    service, db = _service(actor=True)
    db.rpc.return_value.execute.side_effect = RuntimeError(database_error)
    with pytest.raises(exception):
        service.get_binding_status()
