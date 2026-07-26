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
        if role not in ("admin", "member"):
            raise ValidationError("邀请角色只能是 admin 或 member")

        token = secrets.token_urlsafe(self.INVITE_TOKEN_BYTES)
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(days=self.INVITE_EXPIRE_DAYS)
        ).isoformat()
        result = self._governance_rpc(
            "create_governed_invitation",
            {
                "p_org_id": org_id,
                "p_phone": phone,
                "p_role": role,
                "p_invite_token": token,
                "p_expires_at": expires_at,
            },
        )
        logger.info(
            f"Invitation created | org_id={org_id} | "
            f"phone={phone} | by={operator_id}"
        )
        return result.data or {}

    def accept_invitation(self, invite_token: str, user_id: str) -> dict:
        result = self._governance_rpc(
            "accept_governed_invitation",
            {"p_invite_token": invite_token},
        )
        if not result.data:
            raise NotFoundError("邀请", invite_token)
        logger.info(
            f"Invitation accepted | org_id={result.data.get('org_id')} | "
            f"user_id={user_id}"
        )
        return result.data
