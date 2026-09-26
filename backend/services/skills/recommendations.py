"""Bounded deterministic suggestions from typed facts, never execution authority."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from services.skills.contracts import Contract, SkillFileType
from services.skills.resolver import SkillResolutionContext, SkillResolver, SkillSummary

ALGORITHM_VERSION = "trusted-facts-v1"
MAX_RECOMMENDATIONS = 3


class RecommendationFacts(Contract):
    context: SkillResolutionContext
    available_tool_names: frozenset[str] = frozenset()
    selected_file_types: frozenset[SkillFileType] = frozenset()
    session_bindings: dict[str, str] = Field(default_factory=dict)


class RecommendationReason(Contract):
    code: Literal["organization", "domain", "execution_mode", "tools", "file_type", "session_binding"]
    values: tuple[str, ...] = ()


class SkillRecommendation(SkillSummary):
    reasons: tuple[RecommendationReason, ...]


class RecommendationBatch(Contract):
    status: Literal["ready", "disabled", "unavailable"]
    recommendation_id: UUID | None = None
    candidates: tuple[SkillRecommendation, ...] = Field(default=(), max_length=MAX_RECOMMENDATIONS)


def recommend(facts: RecommendationFacts, candidates, *, audience: Literal["user", "model"]):
    context = facts.context
    if "skill_recommendations_enabled" not in context.enabled_feature_flags:
        return []
    ranked = []
    # Resolve again: even an incorrect caller/ranking result cannot introduce a
    # foreign organization, a hidden model Skill, or a conflicting session pin.
    for c in SkillResolver().select(context, candidates):
        m = c.catalog_metadata
        if audience == "model" and not m.model_selectable:
            continue
        pin = facts.session_bindings.get(c.skill_key)
        if pin is not None and pin != c.revision:
            continue
        reasons = [RecommendationReason(code="organization"),
                   RecommendationReason(code="domain", values=(context.agent_domain,)),
                   RecommendationReason(code="execution_mode", values=(context.execution_mode,))]
        score = 0
        if pin == c.revision:
            reasons.append(RecommendationReason(code="session_binding"))
            score += 100
        types = sorted(facts.selected_file_types & set(m.recommended_file_types))
        if types:
            reasons.append(RecommendationReason(code="file_type", values=tuple(types)))
            score += 20
        tools = sorted(facts.available_tool_names & set(m.allowed_tool_names))
        if tools:
            reasons.append(RecommendationReason(code="tools", values=tuple(tools[:5])))
            score += 5
        summary = SkillRecommendation(skill_id=c.skill_key, revision=c.revision,
            name=m.name or c.skill_key, description=c.description, triggers=m.triggers,
            source=c.scope_kind, model_selectable=m.model_selectable, reasons=tuple(reasons))
        ranked.append((score, c, summary))
    ranked.sort(key=lambda row: (-row[0], -row[1].priority, row[1].scope_kind != "org",
                                 row[1].skill_key, str(row[1].package_id)))
    return [row[2] for row in ranked[:MAX_RECOMMENDATIONS]]
