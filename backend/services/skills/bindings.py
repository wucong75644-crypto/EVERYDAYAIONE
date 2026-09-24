"""Authenticated session configuration, separate from Turn/model activation."""

import asyncio
from uuid import UUID

from fastapi import HTTPException

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.permissions.checker import PermissionChecker
from services.permissions.permission_points import PERMISSIONS
from services.skills.binding_repository import SkillBindingRepository
from services.skills.contracts import SkillError
from services.skills.resolver import SkillResolutionContext, SkillResolver, SkillSummary
from services.skills.selection import SkillSelection


class SkillBinding(SkillSummary):
    binding_id: UUID
    available: bool


def binding_authority(db, actor: str, conversation_id: UUID) -> tuple[str, str]:
    def row(table, **filters):
        query = db.table(table).select("*")
        for key, value in filters.items():
            query = query.eq(key, value)
        response = query.maybe_single().execute()
        return response.data if response and isinstance(response.data, dict) else {}

    if row("users", id=actor).get("status") != "active":
        raise HTTPException(403, "无权管理会话 Skill")
    conversation = row("conversations", id=str(conversation_id))
    owner = str(conversation.get("user_id") or "")
    if (not conversation or conversation.get("scope_type") != "user"
            or str(conversation.get("scope_id") or "") != owner or not conversation.get("org_id")):
        raise HTTPException(404, "对话不存在或不支持会话 Skill")
    org = str(conversation["org_id"])
    membership = row("org_members", org_id=org, user_id=actor)
    if (row("organizations", id=org).get("status") != "active" or membership.get("status") != "active"
            or (owner != actor and membership.get("role") not in ("owner", "admin"))):
        raise HTTPException(403, "仅会话所有者或同组织管理员可管理会话 Skill")
    return org, owner


class ConversationSkillBindings:
    def __init__(self, db, settings, actor, conversation_id, org, owner):
        self.db, self.settings = db, settings
        self.conversation_id, self.org, self.owner = conversation_id, org, owner
        self.repository = SkillBindingRepository(db.pool, DatabaseScope(
            actor_user_id=actor, org_id=org, access_kind=DatabaseAccessKind.RUNTIME_ADMIN,
        ))

    async def _select(self, candidates):
        # Admin configuration cannot grant the recipient its administrator's
        # business permissions. Runtime rechecks the recipient again on use.
        checker = PermissionChecker(self.db)
        required = {code for c in candidates for code in c.catalog_metadata.required_permissions}
        permissions = set()
        for code in sorted(required & PERMISSIONS.keys()):
            if await checker.check(self.owner, self.org, code):
                permissions.add(code)
        context = SkillResolutionContext(
            actor_user_id=self.owner, org_id=self.org, conversation_scope="user",
            agent_domain="general", execution_mode="interactive", permissions=frozenset(permissions),
            enabled_feature_flags=frozenset(name for name in type(self.settings).model_fields
                                           if getattr(self.settings, name) is True),
        )
        return SkillResolver().select(context, candidates)

    async def list(self) -> list[SkillBinding]:
        rows = await asyncio.to_thread(self.repository.bindings, self.conversation_id)
        candidates = [self.repository.candidate(row) for row in rows]
        eligible = {c.package_id for c in await self._select(candidates)}
        return [SkillBinding(
            binding_id=row["id"], skill_id=c.skill_key, name=c.catalog_metadata.name or c.skill_key,
            revision=c.revision, description=c.description, triggers=c.catalog_metadata.triggers,
            source=c.scope_kind, model_selectable=c.catalog_metadata.model_selectable,
            available=bool(row["available"] and c.package_id in eligible),
        ) for row, c in zip(rows, candidates)]

    async def add(self, selection: SkillSelection) -> UUID:
        candidates = await asyncio.to_thread(self.repository.catalog_candidates)
        selected = next((c for c in await self._select(candidates) if c.skill_key == selection.skill_id), None)
        if selected is None:
            raise SkillError("SKILL_NOT_AVAILABLE")
        if selected.revision != selection.revision:
            raise SkillError("SKILL_SELECTION_CHANGED")
        return await asyncio.to_thread(self.repository.add_binding, self.conversation_id, selected)

    async def remove(self, binding_id: UUID):
        await asyncio.to_thread(self.repository.remove_binding, self.conversation_id, binding_id)
