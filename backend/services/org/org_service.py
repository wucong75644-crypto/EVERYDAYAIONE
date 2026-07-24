"""
企业管理服务

处理企业 CRUD、成员管理（邀请/移除/角色变更）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

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

        existing = (
            self.db.table("organizations")
            .select("id")
            .eq("name", name)
            .execute()
        )
        if existing.data:
            raise ConflictError(f"企业名称「{name}」已存在")

        result = (
            self.db.table("organizations")
            .insert({"name": name, "owner_id": owner_id})
            .execute()
        )
        if not result.data:
            raise ValidationError("创建企业失败")

        org = result.data[0]
        org_id = org["id"]

        self.db.table("org_members").insert({
            "org_id": org_id,
            "user_id": owner_id,
            "role": "owner",
            "status": "active",
        }).execute()

        logger.info(f"Organization created | org_id={org_id} | name={name} | owner={owner_id}")
        return org

    def get_organization(self, org_id: str) -> dict:
        """
        获取企业信息。

        Raises:
            NotFoundError: 企业不存在
        """
        result = (
            self.db.table("organizations")
            .select("*")
            .eq("id", org_id)
            .single()
            .execute()
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
        self.require_role(org_id, user_id, ("owner", "admin"))

        updates: dict = {}
        if name is not None:
            name = name.strip()
            if not name or len(name) > 100:
                raise ValidationError("企业名称不能为空且不超过100字符")
            dup = (
                self.db.table("organizations")
                .select("id")
                .eq("name", name)
                .neq("id", org_id)
                .execute()
            )
            if dup.data:
                raise ConflictError(f"企业名称「{name}」已存在")
            updates["name"] = name
        if logo_url is not None:
            updates["logo_url"] = logo_url
        if features is not None:
            updates["features"] = features
        if wecom_corp_id is not None:
            updates["wecom_corp_id"] = wecom_corp_id.strip() or None

        if not updates:
            raise ValidationError("没有需要更新的内容")

        result = (
            self.db.table("organizations")
            .update(updates)
            .eq("id", org_id)
            .execute()
        )
        if not result.data:
            raise NotFoundError("企业", org_id)

        logger.info(f"Organization updated | org_id={org_id} | fields={list(updates.keys())}")
        return result.data[0]

    # ----------------------------------------------------------------
    # 成员管理
    # ----------------------------------------------------------------

    def list_members(self, org_id: str, user_id: str) -> list[dict]:
        """
        列出企业所有成员（含昵称/手机号）。

        Raises:
            PermissionDeniedError: 非企业成员
        """
        self.require_role(org_id, user_id, ("owner", "admin", "member"))

        result = (
            self.db.table("org_members")
            .select("user_id, role, status, joined_at")
            .eq("org_id", org_id)
            .order("joined_at")
            .execute()
        )
        members = []
        for row in result.data or []:
            # 分步查用户信息（兼容 LocalDB，不依赖 PostgREST 嵌套语法）
            user_info = {}
            try:
                u = self.db.table("users").select("nickname, phone").eq("id", row["user_id"]).single().execute()
                if u.data:
                    user_info = u.data
            except Exception:
                pass
            phone = user_info.get("phone") or ""
            masked = f"{phone[:3]}****{phone[-4:]}" if len(phone) >= 7 else phone
            members.append({
                "user_id": row["user_id"],
                "role": row["role"],
                "status": row["status"],
                "joined_at": row["joined_at"],
                "nickname": user_info.get("nickname"),
                "phone": masked,
            })
        return members

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
        self.require_role(org_id, operator_id, ("owner", "admin"))
        if role not in ("admin", "member"):
            raise ValidationError("角色只能是 admin 或 member")

        org = self.get_organization(org_id)
        max_m = org.get("max_members", 50)
        current_count = self._member_count(org_id)
        if current_count >= max_m:
            raise ValidationError(f"企业成员数已达上限({max_m}人)")

        existing = (
            self.db.table("org_members")
            .select("user_id")
            .eq("org_id", org_id)
            .eq("user_id", target_user_id)
            .execute()
        )
        if existing.data:
            raise ConflictError("该用户已是企业成员")

        result = self.db.table("org_members").insert({
            "org_id": org_id,
            "user_id": target_user_id,
            "role": role,
            "invited_by": operator_id,
        }).execute()

        logger.info(
            f"Member added | org_id={org_id} | user_id={target_user_id} | "
            f"role={role} | by={operator_id}"
        )
        return result.data[0] if result.data else {}

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

        self._check_org_active(org_id)
        operator_role = self._get_member_role(org_id, operator_id)
        target_role = self._get_member_role(org_id, target_user_id)

        if target_role == "owner":
            raise ValidationError("不能移除企业创建者")
        if operator_role == "admin" and target_role == "admin":
            raise PermissionDeniedError("管理员不能移除其他管理员")
        if operator_role not in ("owner", "admin"):
            raise PermissionDeniedError("无权移除成员")

        self.db.table("org_members").delete().eq(
            "org_id", org_id
        ).eq("user_id", target_user_id).execute()

        self.db.table("users").update(
            {"current_org_id": None}
        ).eq("id", target_user_id).eq("current_org_id", org_id).execute()

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
        self.require_role(org_id, operator_id, ("owner",))
        if new_role not in ("admin", "member"):
            raise ValidationError("目标角色只能是 admin 或 member")
        if operator_id == target_user_id:
            raise ValidationError("不能更改自己的角色")

        self._get_member_role(org_id, target_user_id)

        result = (
            self.db.table("org_members")
            .update({"role": new_role})
            .eq("org_id", org_id)
            .eq("user_id", target_user_id)
            .execute()
        )
        logger.info(
            f"Member role changed | org_id={org_id} | user_id={target_user_id} | "
            f"new_role={new_role} | by={operator_id}"
        )
        return result.data[0] if result.data else {}

    # ----------------------------------------------------------------
    # 用户查询自己的企业
    # ----------------------------------------------------------------

    def list_user_organizations(self, user_id: str) -> list[dict]:
        """列出用户所属的所有企业"""
        result = (
            self.db.table("org_members")
            .select("org_id, role, status")
            .eq("user_id", user_id)
            .eq("status", "active")
            .execute()
        )
        orgs = []
        for row in result.data or []:
            # 分步查企业信息（兼容 LocalDB）
            try:
                org_result = (
                    self.db.table("organizations")
                    .select("id, name, logo_url, status, features")
                    .eq("id", row["org_id"])
                    .single()
                    .execute()
                )
                org_info = org_result.data or {}
            except Exception:
                continue
            if org_info.get("status") != "active":
                continue
            orgs.append({
                "org_id": str(org_info["id"]),
                "name": org_info["name"],
                "logo_url": org_info.get("logo_url"),
                "role": row["role"],
                "features": org_info.get("features", {}),
            })
        return orgs

    # ----------------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------------

    def _check_org_active(self, org_id: str) -> None:
        """校验企业存在且状态为 active。"""
        org_result = (
            self.db.table("organizations")
            .select("status")
            .eq("id", org_id)
            .single()
            .execute()
        )
        if not org_result.data:
            raise NotFoundError("企业", org_id)
        if org_result.data["status"] != "active":
            raise PermissionDeniedError("该企业已被停用")

    def require_role(
        self, org_id: str, user_id: str, allowed_roles: tuple[str, ...],
    ) -> str:
        """校验用户在企业中的角色（含企业状态检查），返回角色名。"""
        self._check_org_active(org_id)
        role = self._get_member_role(org_id, user_id)
        if role not in allowed_roles:
            raise PermissionDeniedError("无权执行此操作")
        return role

    def _get_member_role(self, org_id: str, user_id: str) -> str:
        """获取用户在企业中的角色。"""
        result = (
            self.db.table("org_members")
            .select("role, status")
            .eq("org_id", org_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        if not result.data:
            raise PermissionDeniedError("您不是该企业成员")
        if result.data["status"] != "active":
            raise PermissionDeniedError("您在该企业中已被禁用")
        return result.data["role"]

    def _member_count(self, org_id: str) -> int:
        """当前企业有效成员数"""
        result = (
            self.db.table("org_members")
            .select("user_id", count="exact")
            .eq("org_id", org_id)
            .eq("status", "active")
            .execute()
        )
        return result.count or 0
