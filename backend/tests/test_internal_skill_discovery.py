"""Internal workflow methods stay executable without leaking into chat discovery."""
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

import pytest

from services.skills.discovery_policy import SOCIAL_WORKFLOW_SKILLS, TEST_ONLY_SKILLS
from services.skills.resolver import SkillResolver
from services.social_ops.skill_bundle import bundle_specs
from services.social_ops.production import STAGES
from tests.test_skill_resolver import candidate, context, ACTOR


@pytest.mark.parametrize('key', sorted(SOCIAL_WORKFLOW_SKILLS))
def test_existing_published_internal_methods_only_discoverable_for_workflow(key):
    # These are the existing production declarations; no republish is required.
    item = candidate(skill_key=key, catalog_metadata={
        'model_selectable': True, 'execution_modes': ['interactive', 'scheduled']})
    resolver = SkillResolver()
    assert resolver.resolve(context(), [item]) == []
    assert resolver.select(context(execution_mode='scheduled'), [item]) == [item]
    assert resolver.select(context(), [item], restoring=True) == [item]
    assert resolver.select(context(permissions=set()), [item.model_copy(update={
        'catalog_metadata': item.catalog_metadata.model_copy(update={'required_permissions': ('order.view',)})
    })], restoring=True) == []


@pytest.mark.parametrize('key', sorted(TEST_ONLY_SKILLS))
def test_test_methods_never_offered_but_pinned_history_can_restore(key):
    item = candidate(skill_key=key, catalog_metadata={'execution_modes': ['interactive', 'scheduled']})
    for mode in ('interactive', 'scheduled'):
        ctx = context(execution_mode=mode)
        assert SkillResolver().select(ctx, [item]) == []
        assert SkillResolver().select(ctx, [item], restoring=True) == [item]


@pytest.mark.parametrize('key', ['ecommerce-main-images', 'ecommerce-image-prompts',
                               'reference-image-prompts', 'taobao-product-white-background'])
def test_user_entry_points_remain_available(key):
    item = candidate(skill_key=key)
    assert SkillResolver().select(context(), [item]) == [item]


def test_personal_methods_are_not_reclassified_by_name():
    item = candidate(skill_key='test-daily-report', scope_kind='personal',
                     package_user_id=ACTOR, package_org_id=None, assignment_org_id=None)
    assert SkillResolver().select(context(), [item]) == [item]


def test_new_bundles_cover_exact_workflow_stages_without_chat_activation():
    specs = bundle_specs()
    assert {s.skill_key for s in specs} == SOCIAL_WORKFLOW_SKILLS
    assert {key for keys in STAGES.values() for key in keys} == SOCIAL_WORKFLOW_SKILLS
    for spec in specs:
        metadata = spec.content.catalog_metadata
        assert metadata.execution_modes == ('scheduled',)
        assert metadata.model_selectable is False


async def test_old_session_bindings_do_not_block_an_ordinary_chat(monkeypatch):
    from services.skills.runtime_source import ActorSkillSource
    from tests.test_skill_runtime_source import source_context
    from core.config import get_settings
    internal = candidate(skill_key='easel-text-polisher')
    user = candidate(skill_key='reference-image-prompts')
    repo = SimpleNamespace(bindings=Mock(return_value=[internal, user]), candidate=lambda row: row)
    monkeypatch.setattr('services.skills.runtime_source.SkillBindingRepository', lambda *args: repo)
    source = ActorSkillSource(SimpleNamespace(db=SimpleNamespace(pool=object())), source_context(), get_settings())
    source._resolution_context = AsyncMock(return_value=context())
    assert await source.session_bindings() == [user]


async def test_internal_method_rejected_before_reading_body_in_new_chat():
    from services.skills.runtime_source import ActorSkillSource
    from tests.test_skill_runtime_source import source_context
    from core.config import get_settings
    from services.skills.contracts import SkillError
    source = ActorSkillSource(SimpleNamespace(db=SimpleNamespace(pool=object())), source_context(), get_settings())
    source._resolution_context = AsyncMock(return_value=context())
    source.repository = SimpleNamespace(assigned_revision=Mock())
    with pytest.raises(SkillError, match='SKILL_ACCESS_DENIED'):
        await source.load(candidate(skill_key='easel-text-polisher'))
    source.repository.assigned_revision.assert_not_called()
