"""Trusted adapters for optional suggestions; failures cannot stop ordinary chat."""

import asyncio
from dataclasses import replace
import logging
from types import SimpleNamespace
from uuid import UUID

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.permissions.checker import PermissionChecker
from services.permissions.permission_points import PERMISSIONS
from services.skills.available import _conversation_org
from services.skills.recommendation_repository import SkillRecommendationRepository
from services.skills.recommendations import RecommendationBatch, RecommendationFacts, recommend
from services.skills.resolver import skill_tool_ceiling
from services.tools import ToolPolicy, build_legacy_catalog
from services.tools.runtime_context import catalog_context
from services.tools.spec import thaw

logger = logging.getLogger(__name__)


def recommendations_enabled(settings):
    return settings.skill_catalog_enabled is True and getattr(settings, "skill_recommendations_enabled", False) is True


async def available_capabilities(db, context):
    registry = build_legacy_catalog()
    checker = PermissionChecker(db)
    codes = {code for spec in registry.specs() for code in spec.policy_rules.required_permissions}
    permissions = {code: bool(context.org_id) and code in PERMISSIONS and await checker.check(
        context.actor_user_id, context.org_id, code) for code in sorted(codes)}
    snapshot = thaw(context.authorization_snapshot)
    snapshot["permissions"] = permissions
    context = replace(context, authorization_snapshot=snapshot)
    policy = ToolPolicy(registry)
    return frozenset(spec.name for spec in registry.specs()
                     if registry.check_access(spec.name, context, policy=policy).allowed)


async def source_recommendations(source, candidates, bindings, *, audience, turn_id=None, selected_file_types=(), bound_candidates=()):
    if not recommendations_enabled(source.settings):
        return RecommendationBatch(status="disabled")
    try:
        context = await source._resolution_context(candidates)
        capabilities = await available_capabilities(source.handler.db, source.context)
        for bound in bound_candidates:
            capabilities = skill_tool_ceiling(bound.catalog_metadata, capabilities, capabilities)
        facts = RecommendationFacts(context=context,
            available_tool_names=capabilities,
            selected_file_types=frozenset(selected_file_types), session_bindings=bindings)
        suggestions = recommend(facts, candidates, audience=audience)
        repository = SkillRecommendationRepository(source.handler.db.pool, source.repository.scope)
        recommendation_id = await asyncio.to_thread(repository.record, source.context.conversation_id,
            turn_id, audience, facts, suggestions)
        return RecommendationBatch(status="ready", recommendation_id=recommendation_id, candidates=tuple(suggestions))
    except Exception:
        # Do not log exception values: DB validation errors may contain data.
        logger.warning("SKILL_RECOMMENDATION_UNAVAILABLE")
        return RecommendationBatch(status="unavailable")


async def web_recommendations(db, settings, *, actor_user_id, conversation_id, selected_file_types=(), permission_mode="auto"):
    if not recommendations_enabled(settings):
        return RecommendationBatch(status="disabled")
    actor = str(UUID(actor_user_id))
    org = await asyncio.to_thread(_conversation_org, db, actor, str(conversation_id))
    if org is None:
        return RecommendationBatch(status="ready")
    from services.skills.runtime_source import ActorSkillSource
    context = replace(catalog_context(org, permission_mode), actor_user_id=actor,
                      workspace_owner_id=actor, conversation_id=str(conversation_id),
                      feature_flags={key: getattr(settings, key, False) is True for key in (
                          "file_workspace_enabled", "sandbox_enabled", "crawler_enabled", "scheduled_task_direct_enabled")})
    source = ActorSkillSource(SimpleNamespace(db=db), context, settings)
    try:
        candidates = await source.discover()
        bound = await source.session_bindings()
        bindings = {c.skill_key: c.revision for c in bound}
    except Exception:
        logger.warning("SKILL_RECOMMENDATION_DISCOVERY_UNAVAILABLE")
        return RecommendationBatch(status="unavailable")
    return await source_recommendations(source, candidates, bindings, audience="user",
                                        selected_file_types=selected_file_types, bound_candidates=bound)


async def model_recommendations(state):
    source = state.source
    if not recommendations_enabled(source.settings) or not state.allows_dynamic_activation:
        return
    # A resumed Turn keeps its original catalog; never refresh optional hints on replay.
    state.recommendation_batch = await source_recommendations(source,
        [c for c in state.directory.values() if c.skill_key not in state.session_skill_ids],
        {key: state.directory[key].revision for key in state.session_skill_ids},
        audience="model", turn_id=state.turn_id,
        bound_candidates=[state.directory[key] for key in state.session_skill_ids])


async def model_feedback(state, candidate, success):
    batch = state.recommendation_batch
    if batch is None or batch.recommendation_id is None or not any(
        c.skill_id == candidate.skill_key and c.revision == candidate.revision for c in batch.candidates
    ):
        return
    try:
        repository = SkillRecommendationRepository(state.source.handler.db.pool, state.source.repository.scope)
        await asyncio.to_thread(repository.feedback, batch.recommendation_id, state.source.context.conversation_id,
            candidate.skill_key, candidate.revision, "activated" if success else "activation_failed", audience="model")
    except Exception:
        logger.warning("SKILL_RECOMMENDATION_FEEDBACK_UNAVAILABLE")


async def web_feedback(db, *, actor_user_id, conversation_id, recommendation_id, skill_id, revision, feedback):
    actor = str(UUID(actor_user_id))
    org = await asyncio.to_thread(_conversation_org, db, actor, str(conversation_id))
    if org is None:
        return False
    repository = SkillRecommendationRepository(db.pool, DatabaseScope(actor, org, DatabaseAccessKind.PROJECTION))
    return await asyncio.to_thread(repository.feedback, recommendation_id, conversation_id, skill_id, revision, feedback)
