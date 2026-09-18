"""Authenticated Web adapter for summary discovery; no chat execution integration."""

import asyncio
from uuid import UUID

from fastapi import HTTPException

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.permissions.checker import PermissionChecker
from services.permissions.permission_points import PERMISSIONS
from services.skills.repository import SkillRepository
from services.skills.resolver import SkillResolutionContext, SkillResolver, SkillSummary


def _conversation_org(db, actor_user_id: str, conversation_id: str) -> str | None:
    def row(table, fields, **filters):
        query = db.table(table).select(fields)
        for key, value in filters.items():
            query = query.eq(key, value)
        result = query.maybe_single().execute()
        return result.data if result and isinstance(result.data, dict) else {}

    if row("users", "status", id=actor_user_id).get("status") != "active":
        raise HTTPException(403, "无权访问 Skill 目录")
    conversation = row("conversations", "org_id,scope_type,scope_id", id=conversation_id,
                       user_id=actor_user_id, scope_type="user")
    # HTTP does not possess a trusted WeCom channel principal. Reject channel
    # discovery here even for organization administrators.
    if not conversation or str(conversation.get("scope_id") or "") != actor_user_id:
        raise HTTPException(404, "对话不存在或无权访问")
    org_id = conversation.get("org_id")
    if org_id is None:
        return None
    org_id = str(UUID(str(org_id)))
    if (row("organizations", "status", id=org_id).get("status") != "active"
            or row("org_members", "status", org_id=org_id,
                   user_id=actor_user_id).get("status") != "active"):
        raise HTTPException(403, "无权访问 Skill 目录")
    return org_id


async def available_skills(db, settings, *, actor_user_id: str, conversation_id: UUID) -> list[SkillSummary]:
    if settings.skill_catalog_enabled is not True:
        return []
    actor_user_id = str(UUID(actor_user_id))
    org_id = await asyncio.to_thread(_conversation_org, db, actor_user_id, str(conversation_id))
    # P1 assignments require an organization; no implied global/personal grant.
    if org_id is None:
        return []
    repository = SkillRepository(db.pool, DatabaseScope(
        actor_user_id=actor_user_id, org_id=org_id, access_kind=DatabaseAccessKind.PROJECTION,
    ))
    candidates = await asyncio.to_thread(repository.catalog_candidates)
    checker = PermissionChecker(db)
    required = {code for candidate in candidates for code in candidate.catalog_metadata.required_permissions}
    permissions = set()
    for code in sorted(required & PERMISSIONS.keys()):
        if await checker.check(actor_user_id, org_id, code):
            permissions.add(code)
    context = SkillResolutionContext(
        actor_user_id=actor_user_id, org_id=org_id, conversation_scope="user",
        agent_domain="general", execution_mode="interactive", permissions=frozenset(permissions),
        enabled_feature_flags=frozenset(
            name for name in type(settings).model_fields if getattr(settings, name) is True
        ),
    )
    return SkillResolver().resolve(context, candidates)
