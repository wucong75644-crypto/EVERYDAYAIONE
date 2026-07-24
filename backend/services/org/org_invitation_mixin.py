"""Organization invitation lifecycle."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)


class OrgInvitationMixin:
    """Create and accept organization invitations."""

    INVITE_TOKEN_BYTES: int
    INVITE_EXPIRE_DAYS: int
    db: Any

    def create_invitation(
        self, org_id: str, operator_id: str, phone: str,
        role: str = "member",
    ) -> dict:
        self.require_role(org_id, operator_id, ("owner", "admin"))
        if role not in ("admin", "member"):
            raise ValidationError("邀请角色只能是 admin 或 member")

        user_result = (
            self.db.table("users").select("id").eq("phone", phone).execute()
        )
        if user_result.data:
            existing_member = (
                self.db.table("org_members")
                .select("user_id")
                .eq("org_id", org_id)
                .eq("user_id", user_result.data[0]["id"])
                .execute()
            )
            if existing_member.data:
                raise ConflictError("该用户已是企业成员")

        pending = (
            self.db.table("org_invitations")
            .select("id")
            .eq("org_id", org_id)
            .eq("phone", phone)
            .eq("status", "pending")
            .execute()
        )
        if pending.data:
            raise ConflictError("该手机号已有待处理的邀请")

        token = secrets.token_urlsafe(self.INVITE_TOKEN_BYTES)
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(days=self.INVITE_EXPIRE_DAYS)
        ).isoformat()
        result = self.db.table("org_invitations").insert({
            "org_id": org_id,
            "phone": phone,
            "role": role,
            "invite_token": token,
            "invited_by": operator_id,
            "expires_at": expires_at,
        }).execute()
        logger.info(
            f"Invitation created | org_id={org_id} | "
            f"phone={phone} | by={operator_id}"
        )
        return result.data[0] if result.data else {}

    def accept_invitation(self, invite_token: str, user_id: str) -> dict:
        result = (
            self.db.table("org_invitations")
            .select("*")
            .eq("invite_token", invite_token)
            .single()
            .execute()
        )
        if not result.data:
            raise NotFoundError("邀请", invite_token)

        invitation = result.data
        if invitation["status"] != "pending":
            raise ValidationError("邀请已使用或已过期")
        expires_at = datetime.fromisoformat(
            invitation["expires_at"].replace("Z", "+00:00")
        )
        if datetime.now(timezone.utc) > expires_at:
            self.db.table("org_invitations").update(
                {"status": "expired"}
            ).eq("id", invitation["id"]).execute()
            raise ValidationError("邀请已过期")

        user_result = (
            self.db.table("users")
            .select("phone")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if not user_result.data:
            raise NotFoundError("用户", user_id)
        if user_result.data.get("phone") != invitation["phone"]:
            raise ValidationError("该邀请不是发给您的（手机号不匹配）")

        org_id = invitation["org_id"]
        organization = self.get_organization(org_id)
        if organization["status"] != "active":
            raise PermissionDeniedError("该企业已被停用")

        existing = (
            self.db.table("org_members")
            .select("user_id")
            .eq("org_id", org_id)
            .eq("user_id", user_id)
            .execute()
        )
        if existing.data:
            self.db.table("org_invitations").update(
                {"status": "accepted"}
            ).eq("id", invitation["id"]).execute()
            raise ConflictError("您已是该企业成员")

        max_members = organization.get("max_members", 50)
        if self._member_count(org_id) >= max_members:
            raise ValidationError(f"企业成员数已达上限({max_members}人)")

        self.db.table("org_members").insert({
            "org_id": org_id,
            "user_id": user_id,
            "role": invitation["role"],
            "invited_by": invitation["invited_by"],
        }).execute()
        self.db.table("org_invitations").update(
            {"status": "accepted"}
        ).eq("id", invitation["id"]).execute()
        logger.info(
            f"Invitation accepted | org_id={org_id} | user_id={user_id}"
        )
        return {
            "org_id": org_id,
            "role": invitation["role"],
            "org_name": organization["name"],
        }
