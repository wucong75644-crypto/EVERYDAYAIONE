"""
企业管理服务

处理企业 CRUD、成员管理（邀请/移除/角色变更）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

if TYPE_CHECKING:
    from supabase import Client

from core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from services.org.org_invitation_mixin import OrgInvitationMixin


class OrgService(OrgInvitationMixin):
    """企业管理服务"""

    INVITE_TOKEN_BYTES = 32
    INVITE_EXPIRE_DAYS = 7

    def __init__(self, db: Client):
        self.db = db

    def _governance_rpc(
        self,
        name: str,
        params: Optional[dict] = None,
    ) -> Any:
        """Execute one governance capability and preserve API error semantics."""
        try:
            return self.db.rpc(name, params).execute()
        except Exception as error:
            marker = str(error)
            if "GOVERNANCE_ORG_NAME_CONFLICT" in marker:
                raise ConflictError("企业名称已存在") from error
            if "GOVERNANCE_MEMBER_EXISTS" in marker:
                raise ConflictError("您已是该企业成员") from error
            if "GOVERNANCE_INVITATION_CONFLICT" in marker:
                raise ConflictError("该手机号已有待处理邀请或已是成员") from error
            if "GOVERNANCE_ORG_INACTIVE" in marker:
                raise PermissionDeniedError("该企业已被停用") from error
            if "GOVERNANCE_INVITATION_RECIPIENT_MISMATCH" in marker:
                raise ValidationError("邀请手机号不匹配") from error
            if "GOVERNANCE_INVITATION_EXPIRED" in marker:
                raise ValidationError("邀请已过期") from error
            if "GOVERNANCE_MEMBER_LIMIT_REACHED" in marker:
                raise ValidationError("企业成员数已达上限") from error
            if "GOVERNANCE_SELF_MUTATION_DENIED" in marker:
                raise ValidationError("不能修改自己") from error
            if "GOVERNANCE_ARGUMENT_INVALID" in marker:
                raise ValidationError("企业治理参数或状态无效") from error
            if any(code in marker for code in (
                "GOVERNANCE_MEMBER_MISSING",
                "GOVERNANCE_INVITATION_MISSING",
            )):
                raise NotFoundError("企业治理对象", "unknown") from error
            if any(code in marker for code in (
                "GOVERNANCE_AUTHORITY_DENIED",
                "GOVERNANCE_PRINCIPAL_INACTIVE",
                "GOVERNANCE_ROLE_SCOPE_MISMATCH",
                "GOVERNANCE_SCOPE_MISMATCH",
                "GOVERNANCE_SELF_SCOPE_MISMATCH",
            )):
                raise PermissionDeniedError("无权执行此企业治理操作") from error
            raise

    # ----------------------------------------------------------------
    # 企业 CRUD
    # ----------------------------------------------------------------

    def create_organization(self, name: str, owner_id: str) -> dict:
        """
        创建企业（仅超管调用）。

        同时将 owner 加入 org_members(role=owner)。

        Raises:
            ConflictError: 企业名已存在
        """
        name = name.strip()
        if not name or len(name) > 100:
            raise ValidationError("企业名称不能为空且不超过100字符")

        result = self._governance_rpc(
            "create_governed_organization",
            {"p_name": name, "p_owner_id": owner_id},
        )
        if not result.data:
            raise ValidationError("创建企业失败")
        org = result.data
        logger.info(
            f"Organization created | org_id={org['id']} | "
            f"name={name} | owner={owner_id}"
        )
        return org

    def get_organization(self, org_id: str) -> dict:
        """
        获取企业信息。

        Raises:
            NotFoundError: 企业不存在
        """
        result = self._governance_rpc(
            "get_governed_organization", {"p_org_id": org_id},
        )
        if not result.data:
            raise NotFoundError("企业", org_id)
        return result.data

    def update_organization(
        self,
        org_id: str,
        user_id: str,
        *,
        name: Optional[str] = None,
        logo_url: Optional[str] = None,
        features: Optional[dict] = None,
        wecom_corp_id: Optional[str] = None,
    ) -> dict:
        """
        更新企业信息（owner/admin）。

        Raises:
            PermissionDeniedError: 无权操作
            ConflictError: 名称重复
        """
        updates: dict = {}
        if name is not None:
            name = name.strip()
            if not name or len(name) > 100:
                raise ValidationError("企业名称不能为空且不超过100字符")
            updates["name"] = name
        if logo_url is not None:
            updates["logo_url"] = logo_url
        if features is not None:
            updates["features"] = features
        if wecom_corp_id is not None:
            updates["wecom_corp_id"] = wecom_corp_id.strip() or None

        if not updates:
            raise ValidationError("没有需要更新的内容")

        result = self._governance_rpc(
            "update_governed_organization",
            {"p_org_id": org_id, "p_changes": updates},
        )
        if not result.data:
            raise NotFoundError("企业", org_id)

        logger.info(f"Organization updated | org_id={org_id} | fields={list(updates.keys())}")
        return result.data

    # ----------------------------------------------------------------
    # 成员管理
    # ----------------------------------------------------------------

    def list_members(self, org_id: str, user_id: str) -> list[dict]:
        """
        列出企业所有成员（含昵称/手机号）。

        Raises:
            PermissionDeniedError: 非企业成员
        """
        result = self._governance_rpc(
            "list_governed_members", {"p_org_id": org_id},
        )
        return result.data or []

    def add_member(
        self,
        org_id: str,
        operator_id: str,
        target_user_id: str,
        role: str = "member",
    ) -> dict:
        """
        直接添加成员（超管/owner/admin 调用）。

        Raises:
            ConflictError: 已是成员
            PermissionDeniedError: 无权操作
        """
        if role not in ("admin", "member"):
            raise ValidationError("角色只能是 admin 或 member")
        result = self._governance_rpc(
            "add_governed_member",
            {
                "p_org_id": org_id,
                "p_target_user_id": target_user_id,
                "p_role": role,
            },
        )

        logger.info(
            f"Member added | org_id={org_id} | user_id={target_user_id} | "
            f"role={role} | by={operator_id}"
        )
        return result.data or {}

    def remove_member(self, org_id: str, operator_id: str, target_user_id: str) -> None:
        """
        移除成员。

        owner 不能被移除。admin 只能移除 member。

        Raises:
            PermissionDeniedError: 无权操作
            ValidationError: 不能移除 owner
        """
        if operator_id == target_user_id:
            raise ValidationError("不能移除自己")

        self._governance_rpc(
            "remove_governed_member",
            {"p_org_id": org_id, "p_target_user_id": target_user_id},
        )

        logger.info(
            f"Member removed | org_id={org_id} | user_id={target_user_id} | by={operator_id}"
        )

    def change_member_role(
        self, org_id: str, operator_id: str, target_user_id: str, new_role: str,
    ) -> dict:
        """
        变更成员角色（仅 owner 可操作）。

        Raises:
            PermissionDeniedError: 非 owner
            ValidationError: 无效角色 / 不能改自己
        """
        if new_role not in ("admin", "member"):
            raise ValidationError("目标角色只能是 admin 或 member")
        if operator_id == target_user_id:
            raise ValidationError("不能更改自己的角色")

        result = self._governance_rpc(
            "change_governed_member_role",
            {
                "p_org_id": org_id,
                "p_target_user_id": target_user_id,
                "p_role": new_role,
            },
        )
        logger.info(
            f"Member role changed | org_id={org_id} | user_id={target_user_id} | "
            f"new_role={new_role} | by={operator_id}"
        )
        return result.data or {}

    # ----------------------------------------------------------------
    # 用户查询自己的企业
    # ----------------------------------------------------------------

    def list_user_organizations(self, user_id: str) -> list[dict]:
        """列出用户所属的所有企业"""
        result = self._governance_rpc("list_actor_organizations")
        return result.data or []

    def list_all_organizations(self) -> list[dict]:
        """列出平台企业；数据库能力负责验证全局管理员。"""
        result = self._governance_rpc("list_all_governed_organizations")
        return result.data or []

    def search_user_by_phone(self, phone: str) -> dict:
        """按手机号搜索用户；仅返回治理能力允许的脱敏字段。"""
        result = self._governance_rpc(
            "search_governed_user_by_phone", {"p_phone": phone},
        )
        return result.data or {"found": False, "user": None}

    def list_pending_invitations(self) -> list[dict]:
        """列出当前数据库 Actor 的有效待接受邀请。"""
        result = self._governance_rpc("list_actor_pending_invitations")
        return result.data or []

    # ----------------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------------

    def require_role(
        self, org_id: str, user_id: str, allowed_roles: tuple[str, ...],
    ) -> str:
        """校验用户在企业中的角色（含企业状态检查），返回角色名。"""
        result = self._governance_rpc(
            "get_governed_actor_authority", {"p_org_id": org_id},
        )
        role = str(result.data or "")
        if role not in allowed_roles:
            raise PermissionDeniedError("无权执行此操作")
        return role
