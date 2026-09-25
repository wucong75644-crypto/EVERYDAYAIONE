"""Server-owned scheduled revision pins; never resolve latest during execution."""

import asyncio
from dataclasses import replace
from uuid import UUID

from pydantic import Field, TypeAdapter, ValidationError

from services.skills.contracts import Contract, Sha256, SkillError
from services.skills.renderer import MAX_ACTIVE_SKILLS
from services.skills.resolver import SkillCandidate, SkillResolver, skill_tool_ceiling
from services.skills.runtime_source import ActorSkillSource
from services.skills.selection import SkillSelection


class ScheduledSkillPin(Contract):
    candidate: SkillCandidate
    revision_id: UUID
    content_sha256: Sha256
    body_sha256: Sha256
    allowed_tool_names: frozenset[str]


class ScheduledSkillSnapshot(Contract):
    version: int = Field(default=1, ge=1, le=1)
    skills: tuple[ScheduledSkillPin, ...] = Field(default=(), max_length=MAX_ACTIVE_SKILLS)

    @classmethod
    def parse(cls, raw):
        try:
            value = cls.model_validate(raw)
            if len({p.candidate.skill_key for p in value.skills}) != len(value.skills):
                raise ValueError()
            return value
        except (ValidationError, ValueError, TypeError):
            raise SkillError("SKILL_SCHEDULED_SNAPSHOT_INVALID") from None


EMPTY_SNAPSHOT = {"version": 1, "skills": []}


class ScheduledSkillSource(ActorSkillSource):
    def __init__(self, handler, context, settings, snapshot, *, configuring=False):
        super().__init__(handler, context, settings)
        self.snapshot = ScheduledSkillSnapshot.parse(snapshot)
        self.configuring = configuring

    async def discover(self):
        raise SkillError("SKILL_SCHEDULED_DISCOVERY_FORBIDDEN")

    async def load(self, candidate, *, restoring=False):
        pin = next((p for p in self.snapshot.skills if p.candidate == candidate), None)
        if pin is None:
            raise SkillError("SKILL_SCHEDULED_NOT_BOUND")
        revision = await asyncio.to_thread(
            self.repository.assigned_revision, candidate.package_id, candidate.revision,
            restoring=not self.configuring,
        )
        if not revision.reviewed:
            raise SkillError("SKILL_SCHEDULED_REVIEW_REQUIRED")
        if (revision.id != pin.revision_id or revision.content_sha256 != pin.content_sha256
                or revision.body_sha256 != pin.body_sha256):
            raise SkillError("SKILL_SCHEDULED_HASH_MISMATCH")
        # Preflight validates the same scheduled method; tools still run under
        # the stricter preflight ToolPolicy of the host executor.
        validated = await super().load(candidate, restoring=not self.configuring)
        if (validated.content_sha256 != pin.content_sha256 or validated.body_sha256 != pin.body_sha256):
            raise SkillError("SKILL_SCHEDULED_HASH_MISMATCH")
        return validated


def source_for_executor(executor, settings, snapshot, *, configuring=False):
    context = replace(executor.tool_runtime.context(), execution_mode="scheduled")
    return ScheduledSkillSource(executor, context, settings, snapshot, configuring=configuring)


async def _executor(db, owner, org, task_id, allowed=None):
    from services.agent.tool_executor import ToolExecutor
    from services.tools import build_legacy_catalog
    from services.tools.runtime_context import refresh_context
    registry = build_legacy_catalog()
    names = frozenset(s.name for s in registry.specs()) if allowed is None else frozenset(allowed)
    executor = ToolExecutor(
        db=db, user_id=owner, org_id=org, conversation_id=None, task_id=task_id,
        allowed_tool_names=names, execution_mode="scheduled", permission_mode="auto",
        tool_policy_snapshot={"version": 1, "allowed_tools": sorted(names)}, tool_entrypoint="model",
    )
    trusted = await refresh_context(executor, executor.tool_runtime.context(), registry)
    if trusted.authorization_snapshot.get("access_denied_reason"):
        raise SkillError("SKILL_SCHEDULED_AUTHORIZATION_UNAVAILABLE")
    permitted = frozenset(n for n in names if registry.check_access(n, trusted, policy=executor.tool_runtime.policy).allowed)
    return executor, permitted


async def bind_scheduled_skills(db, *, owner, org, task_id, selections):
    """Only identity/revision is accepted from the caller. Build all authority here."""
    try:
        selected = TypeAdapter(list[SkillSelection]).validate_python(selections)
        if len(selected) > MAX_ACTIVE_SKILLS or len({s.skill_id for s in selected}) != len(selected):
            raise ValueError()
    except (ValueError, TypeError):
        raise SkillError("SKILL_SCHEDULED_SELECTION_INVALID") from None
    if not selected:
        return dict(EMPTY_SNAPSHOT)
    from core.config import get_settings
    settings = get_settings()
    if not settings.skill_runtime_enabled or not settings.skill_catalog_enabled:
        raise SkillError("SKILL_SCHEDULED_RUNTIME_DISABLED")
    executor, permitted = await _executor(db, owner, org, task_id)
    source = ActorSkillSource(executor, executor.tool_runtime.context(), settings)
    pins = []
    for selection in selected:
        candidates = await asyncio.to_thread(source.repository.scheduled_candidates, selection.skill_id, selection.revision)
        candidates = SkillResolver().select(await source._resolution_context(candidates), candidates)
        candidate = next((c for c in candidates if c.skill_key == selection.skill_id
                          and c.revision == selection.revision), None)
        if candidate is None:
            raise SkillError("SKILL_SCHEDULED_SELECTION_UNAVAILABLE")
        revision = await asyncio.to_thread(source.repository.assigned_revision, candidate.package_id, candidate.revision)
        if not revision.reviewed:
            raise SkillError("SKILL_SCHEDULED_REVIEW_REQUIRED")
        metadata = candidate.catalog_metadata
        if metadata.tool_policy == 'restricted' and not set(metadata.allowed_tool_names) <= permitted:
            raise SkillError("SKILL_SCHEDULED_TOOL_DENIED")
        validated = await source.load(candidate)
        pins.append(ScheduledSkillPin(
            candidate=candidate, revision_id=revision.id, content_sha256=validated.content_sha256,
            body_sha256=validated.body_sha256,
            allowed_tool_names=skill_tool_ceiling(metadata, permitted, permitted),
        ))
    return ScheduledSkillSnapshot(skills=tuple(pins)).model_dump(mode="json")


def snapshot_ceiling(snapshot, tools):
    ceiling = frozenset(tools)
    for pin in ScheduledSkillSnapshot.parse(snapshot).skills:
        ceiling &= pin.allowed_tool_names
    return ceiling


async def validate_scheduled_skills(db, *, owner, org, task_id, snapshot, policy=None):
    parsed = ScheduledSkillSnapshot.parse(snapshot)
    if not parsed.skills:
        return
    from core.config import get_settings
    settings = get_settings()
    if not settings.skill_runtime_enabled or not settings.skill_catalog_enabled:
        raise SkillError("SKILL_SCHEDULED_RUNTIME_DISABLED")
    allowed = (policy or {}).get("allowed_tools")
    executor, permitted = await _executor(db, owner, org, task_id, allowed)
    source = source_for_executor(executor, settings, snapshot)
    for pin in parsed.skills:
        await source.load(pin.candidate)
    if allowed is not None and (not set(allowed) <= permitted
                               or not set(allowed) <= snapshot_ceiling(snapshot, allowed)):
        raise SkillError("SKILL_SCHEDULED_TOOL_DENIED")


async def scheduled_skill_options(db, *, owner, org, task_id):
    """Summary-only choices; binding revalidates the exact chosen revision."""
    from core.config import get_settings
    settings = get_settings()
    if not settings.skill_runtime_enabled or not settings.skill_catalog_enabled:
        return []
    executor, permitted = await _executor(db, owner, org, task_id)
    source = ActorSkillSource(executor, executor.tool_runtime.context(), settings)
    options = []
    for candidate in await source.discover():
        revision = await asyncio.to_thread(source.repository.assigned_revision, candidate.package_id, candidate.revision)
        metadata = candidate.catalog_metadata
        if revision.reviewed and (metadata.tool_policy == 'platform' or set(metadata.allowed_tool_names) <= permitted):
            options.append({'skill_id': candidate.skill_key, 'revision': candidate.revision,
                'name': metadata.name or candidate.skill_key, 'description': candidate.description})
    return options
