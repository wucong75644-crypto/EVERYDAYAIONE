"""WeCom OAuth database capability client.

All identity facts are accessed through migration 155 RPCs with an immutable
request scope. This module never reads or writes protected tables directly.
"""

from __future__ import annotations

from typing import Any

from core.config import get_settings
from core.crypto import aes_decrypt
from core.db_scope import (
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
)
from core.exceptions import (
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from core.security import (
    create_refresh_token,
    create_token_material_from_refresh,
)


class WecomOAuthIdentityService:
    """Execute the narrow Web WeCom OAuth database capabilities."""

    def __init__(self, db: Any, scope: DatabaseScope):
        self.db = ScopedDatabaseClient(db, scope)
        self.scope = scope
        self.settings = get_settings()

    @classmethod
    def for_login(
        cls,
        db: Any,
        *,
        org_id: str,
        request_id: str = "",
    ) -> "WecomOAuthIdentityService":
        return cls(db, DatabaseScope(
            actor_user_id=None,
            org_id=org_id,
            access_kind=DatabaseAccessKind.RUNTIME,
            request_id=request_id,
        ))

    @classmethod
    def for_actor(
        cls,
        db: Any,
        *,
        user_id: str,
        org_id: str | None = None,
        request_id: str = "",
    ) -> "WecomOAuthIdentityService":
        return cls(db, DatabaseScope(
            actor_user_id=user_id,
            org_id=org_id,
            access_kind=DatabaseAccessKind.RUNTIME,
            request_id=request_id,
        ))

    def get_public_config(self) -> dict[str, str]:
        data = self._rpc("get_web_wecom_oauth_public_config", {
            "p_org_id": self.scope.org_id,
        })
        corp_id = (data or {}).get("corp_id")
        agent_id = self._decrypt_config(data, "agent_id_encrypted")
        if not corp_id or not agent_id:
            raise ValidationError("该企业未配置企微自建应用（Corp ID 或 Agent ID 缺失）")
        return {"corp_id": corp_id, "agent_id": agent_id}

    def get_exchange_config(self) -> dict[str, str]:
        data = self._rpc("get_web_wecom_oauth_exchange_config", {
            "p_org_id": self.scope.org_id,
        })
        corp_id = (data or {}).get("corp_id")
        agent_secret = self._decrypt_config(data, "agent_secret_encrypted")
        if not corp_id or not agent_secret:
            raise ValidationError("该企业未配置企微自建应用 Secret")
        return {"corp_id": corp_id, "agent_secret": agent_secret}

    def login_or_create(
        self,
        *,
        wecom_userid: str,
        corp_id: str,
        nickname: str | None = None,
    ) -> dict[str, Any]:
        refresh_token, refresh_hash, refresh_expires_at = create_refresh_token()
        display_name = nickname or f"企微用户_{wecom_userid[:8]}"
        user = self._rpc("commit_web_wecom_login", {
            "p_wecom_userid": wecom_userid,
            "p_corp_id": corp_id,
            "p_org_id": self.scope.org_id,
            "p_display_name": display_name,
            "p_refresh_hash": refresh_hash,
            "p_refresh_expires_at": refresh_expires_at.isoformat(),
        })
        token = create_token_material_from_refresh(
            user["id"], refresh_token, refresh_hash, refresh_expires_at,
        ).response()
        return {
            "token": token,
            "user": self._format_user(user),
            "org": {
                "org_id": user["org_id"],
                "name": user["org_name"],
                "role": user["org_role"],
            },
        }

    def bind_account(
        self,
        *,
        wecom_userid: str,
        corp_id: str,
        nickname: str | None = None,
    ) -> dict[str, Any]:
        refresh_token, refresh_hash, refresh_expires_at = create_refresh_token()
        user = self._rpc("bind_web_wecom_identity", {
            "p_wecom_userid": wecom_userid,
            "p_corp_id": corp_id,
            "p_org_id": self.scope.org_id,
            "p_display_name": nickname or f"企微用户_{wecom_userid[:8]}",
            "p_refresh_hash": refresh_hash,
            "p_refresh_expires_at": refresh_expires_at.isoformat(),
        })
        token = create_token_material_from_refresh(
            user["id"], refresh_token, refresh_hash, refresh_expires_at,
        ).response()
        return {
            "token": token,
            "user": self._format_user(user),
            "merged": False,
        }

    def unbind_account(self) -> dict[str, Any]:
        self._rpc("unbind_web_wecom_identity", {
            "p_org_id": self.scope.org_id,
        })
        return {"success": True, "message": "企微账号已解绑"}

    def get_binding_status(self) -> dict[str, Any]:
        return self._rpc("get_web_wecom_binding_status", {
            "p_org_id": self.scope.org_id,
        })

    def _decrypt_config(
        self,
        data: dict[str, Any] | None,
        field: str,
    ) -> str | None:
        if not data or not data.get(field):
            return None
        key = data.get("encrypt_key") or self.settings.org_config_encrypt_key
        if not key:
            raise ValidationError("企业配置加密密钥缺失")
        try:
            return aes_decrypt(data[field], key)
        except ValueError as exc:
            raise ValidationError("企业企微配置无法解密") from exc

    def _rpc(self, name: str, params: dict[str, Any]) -> Any:
        try:
            return self.db.rpc(name, params).execute().data
        except Exception as exc:
            self._raise_business_error(exc)
            raise

    @staticmethod
    def _raise_business_error(exc: Exception) -> None:
        error = str(exc)
        if "MERGE_REVIEW_REQUIRED" in error:
            raise ConflictError("该企微账号已属于其他用户，请联系管理员审核合并") from exc
        if "ACTOR_ALREADY_BOUND" in error:
            raise ConflictError("该账号已绑定其他企微用户，请先解绑") from exc
        if "PRINCIPAL_INACTIVE" in error:
            raise PermissionDeniedError("账号已被禁用") from exc
        if "MEMBER_INACTIVE" in error:
            raise PermissionDeniedError("企业成员账号已停用") from exc
        if "BINDING_MISSING" in error:
            raise ValidationError("当前账号未绑定企微") from exc
        if "LAST_LOGIN_METHOD" in error:
            raise ValidationError(
                "该账号仅通过企微创建，解绑后将无法登录，请先绑定手机号"
            ) from exc
        if "ORG_CORP_MISMATCH" in error or "IDENTITY_SCOPE_CONFLICT" in error:
            raise PermissionDeniedError("企微身份与企业不匹配") from exc
        if "ARGUMENT_INVALID" in error:
            raise ValidationError("企微登录参数无效") from exc

    @staticmethod
    def _format_user(user: dict[str, Any]) -> dict[str, Any]:
        phone = user.get("phone")
        masked_phone = (
            f"{phone[:3]}****{phone[-4:]}"
            if phone and len(phone) >= 7 else None
        )
        return {
            "id": user["id"],
            "nickname": user["nickname"],
            "avatar_url": user.get("avatar_url"),
            "phone": masked_phone,
            "role": user["role"],
            "credits": user["credits"],
            "created_at": user["created_at"],
            "wecom_bound": "wecom" in (user.get("login_methods") or []),
        }
