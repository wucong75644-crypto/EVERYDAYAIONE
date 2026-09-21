"""Trusted Actor scope adapter for metadata discovery and explicit pinned reads."""

import asyncio
from types import SimpleNamespace

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.permissions.checker import PermissionChecker
from services.permissions.permission_points import PERMISSIONS
from services.skills.contracts import PublishRevision, SkillError
from services.skills.repository import SkillRepository
from services.skills.resolver import SkillResolutionContext, SkillResolver
from services.skills.storage import SkillStorage
from services.tools.runtime_context import _check_identity


class ActorSkillSource:
    def __init__(self, handler, context, settings):
        self.handler, self.context, self.settings = handler, context, settings
        self.repository = SkillRepository(handler.db.pool, DatabaseScope(
            actor_user_id=context.actor_user_id, org_id=context.org_id,
            access_kind=DatabaseAccessKind.PROJECTION,
        ))

    async def _resolution_context(self, candidates):
        context = self.context
        scope = getattr(self.handler, "execution_scope", None)
        identity = SimpleNamespace(
            db=self.handler.db, execution_scope=scope,
            channel_scope_id=getattr(scope, "channel_scope_id", None),
        )
        try:
            await asyncio.to_thread(_check_identity, identity, context)
            response = await asyncio.to_thread(
                lambda: self.handler.db.table("users").select("status")
                .eq("id", context.actor_user_id).maybe_single().execute(),
            )
            if not response or not isinstance(response.data, dict) or response.data.get("status") != "active":
                raise SkillError("SKILL_ACTOR_UNAVAILABLE")
        except Exception:
            raise SkillError("SKILL_IDENTITY_UNAVAILABLE") from None
        checker = PermissionChecker(self.handler.db)
        required = {code for c in candidates for code in c.catalog_metadata.required_permissions}
        permissions = set()
        for code in sorted(required & PERMISSIONS.keys()):
            if await checker.check(context.actor_user_id, context.org_id, code):
                permissions.add(code)
        return SkillResolutionContext(
            actor_user_id=context.actor_user_id, org_id=context.org_id,
            conversation_scope=context.context_scope, agent_domain=context.agent_domain,
            execution_mode=context.execution_mode, permissions=frozenset(permissions),
            enabled_feature_flags=frozenset(
                name for name in type(self.settings).model_fields
                if getattr(self.settings, name) is True
            ),
        )

    async def discover(self):
        if self.context.org_id is None:
            return []
        candidates = await asyncio.to_thread(self.repository.catalog_candidates)
        context = await self._resolution_context(candidates)
        return SkillResolver().select(context, candidates)

    async def load(self, candidate, *, restoring: bool = False):
        # Recheck current identity and business permissions, even on replay.
        context = await self._resolution_context([candidate])
        if not SkillResolver().select(context, [candidate]):
            raise SkillError("SKILL_ACCESS_DENIED")

        def read():
            revision = self.repository.assigned_revision(candidate.package_id, candidate.revision, restoring=restoring)
            package = self.repository.get_package(candidate.package_id)
            if (package.skill_key != candidate.skill_key
                    or revision.catalog_metadata != candidate.catalog_metadata):
                raise SkillError("SKILL_PINNED_METADATA_MISMATCH")
            storage = SkillStorage(self.settings.skill_storage_root,
                                   workspace_root=self.settings.file_workspace_root)
            return storage.validate(package, PublishRevision(
                revision=revision.revision, content_sha256=revision.content_sha256,
                body_sha256=revision.body_sha256,
            ), nas_path=revision.nas_path)

        return await asyncio.to_thread(read)
