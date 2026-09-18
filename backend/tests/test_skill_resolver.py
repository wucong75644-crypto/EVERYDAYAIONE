"""Context isolation, deterministic selection and tool narrowing without IO."""

from itertools import combinations, product
from uuid import uuid4

import pytest

from services.skills.contracts import SkillCatalogMetadata
from services.skills.resolver import (
    SkillCandidate, SkillResolutionContext, SkillResolver, effective_allowed_tool_names,
)


ACTOR, ORG, OTHER_ORG = uuid4(), uuid4(), uuid4()


def context(**changes):
    return SkillResolutionContext(**(dict(
        actor_user_id=ACTOR, org_id=ORG, conversation_scope="user", agent_domain="general",
        execution_mode="interactive", enabled_feature_flags={"skill_catalog_enabled"},
    ) | changes))


def candidate(**changes):
    return SkillCandidate(**(dict(
        package_id=uuid4(), skill_key="report", package_org_id=None, assignment_org_id=ORG,
        priority=0, revision="v1", description="报表摘要", scope_kind="platform",
        catalog_metadata=SkillCatalogMetadata(),
    ) | changes))


def test_disabled_does_not_even_iterate_candidates():
    def unavailable():
        raise AssertionError("No catalog access while disabled")
        yield
    assert SkillResolver().resolve(context(enabled_feature_flags=set()), unavailable()) == []


def test_empty_catalog_and_personal_without_assignment():
    assert SkillResolver().resolve(context(), []) == []
    assert SkillResolver().resolve(context(org_id=None), [candidate()]) == []


@pytest.mark.parametrize("changes", [
    {"assignment_org_id": OTHER_ORG},
    {"scope_kind": "org", "package_org_id": OTHER_ORG},
    {"scope_kind": "org", "package_org_id": None},
    {"scope_kind": "platform", "package_org_id": ORG},
])
def test_isolation_defense_even_with_foreign_candidates(changes):
    assert SkillResolver().resolve(context(), [candidate(**changes)]) == []


@pytest.mark.parametrize("declarations", [
    {"conversation_scopes": ["channel"]}, {"conversation_scopes": []},
    {"agent_domains": ["erp"]}, {"execution_modes": ["scheduled", "preflight"]},
    {"actor_user_ids": [uuid4()]}, {"required_permissions": ["order.view"]},
    {"required_feature_flags": ["unknown_flag"]},
])
def test_each_context_dimension_can_hide_a_skill(declarations):
    assert SkillResolver().resolve(context(), [candidate(catalog_metadata=declarations)]) == []


def test_all_dimensions_and_permission_revocation():
    item = candidate(catalog_metadata=dict(
        actor_user_ids=[ACTOR], conversation_scopes=["channel"], agent_domains=["erp"],
        execution_modes=["scheduled"], required_permissions=["order.view"],
        required_feature_flags=["erp_enabled"],
    ))
    ctx = context(conversation_scope="channel", agent_domain="erp", execution_mode="scheduled",
                  permissions={"order.view"}, enabled_feature_flags={"skill_catalog_enabled", "erp_enabled"})
    assert len(SkillResolver().resolve(ctx, [item])) == 1
    assert SkillResolver().resolve(ctx.model_copy(update={"permissions": frozenset()}), [item]) == []
    assert SkillResolver().resolve(ctx.model_copy(update={"actor_user_id": uuid4()}), [item]) == []


@pytest.mark.parametrize("platform_priority,org_priority,source", [(0, 0, "org"), (9, 1, "platform"), (1, 9, "org")])
def test_explicit_priority_then_organization_tie_break(platform_priority, org_priority, source):
    platform = candidate(priority=platform_priority)
    private = candidate(scope_kind="org", package_org_id=ORG, priority=org_priority, revision="v2")
    for items in ([platform, private], [private, platform]):
        result = SkillResolver().resolve(context(), items)
        assert len(result) == 1
        assert result[0].source == source
        assert result[0].revision == ("v2" if source == "org" else "v1")


def test_ineligible_org_version_does_not_hide_eligible_platform():
    restricted = candidate(scope_kind="org", package_org_id=ORG, priority=100,
                           catalog_metadata={"required_permissions": ["order.view"]})
    assert SkillResolver().resolve(context(), [restricted, candidate()])[0].source == "platform"


def test_same_display_name_different_stable_keys_and_public_allowlist():
    metadata = {"name": "同名", "triggers": ["报表"], "model_selectable": True,
                "allowed_tool_names": ["internal_tool"], "actor_user_ids": [ACTOR]}
    items = [candidate(skill_key=key, catalog_metadata=metadata) for key in ("b", "a")]
    result = SkillResolver().resolve(context(), items)
    assert len(result) == 2
    assert result == SkillResolver().resolve(context(), reversed(items))
    for summary in result:
        assert summary.model_dump() == dict(name="同名", revision="v1", description="报表摘要",
                                            triggers=("报表",), source="platform", model_selectable=True)


def test_legacy_metadata_is_conservative():
    result = SkillResolver().resolve(context(), [candidate()])[0]
    assert result.name == "report" and result.triggers == () and result.model_selectable is False
    assert SkillCatalogMetadata().allowed_tool_names == ()


def test_tool_intersection_exhaustively_only_narrows_and_does_not_mutate():
    universe = ("a", "b", "c")
    subsets = [set(part) for n in range(4) for part in combinations(universe, n)]
    for platform, authorized, declared in product(subsets, repeat=3):
        before = [set(s) for s in (platform, authorized, declared)]
        result = effective_allowed_tool_names(platform, authorized, declared)
        assert result == platform & authorized & declared
        assert isinstance(result, frozenset)
        assert result <= platform and result <= authorized and result <= declared
        assert before == [platform, authorized, declared]


def test_tools_missing_authorization_or_declaration_is_empty_and_no_wildcard():
    assert effective_allowed_tool_names({"a"}, None, {"a"}) == frozenset()
    assert effective_allowed_tool_names({"a"}, {"a"}, None) == frozenset()
    assert effective_allowed_tool_names({"a"}, {"a"}, {"*", "unknown"}) == frozenset()


@pytest.mark.parametrize("bad", ["read", [None], [""], ["  "]])
def test_invalid_tool_sets_are_rejected(bad):
    with pytest.raises(ValueError):
        effective_allowed_tool_names({"a"}, {"a"}, bad)
