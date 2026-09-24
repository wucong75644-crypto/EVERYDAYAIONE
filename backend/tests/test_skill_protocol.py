"""One Skill protocol across tasks, with legacy and host authorization bounds."""
import copy
from dataclasses import replace

import pytest
from pydantic import ValidationError

from services.skills.authoring_contracts import DraftContent, new_draft_content
from services.skills.contracts import SkillCatalogMetadata
from services.skills.resolver import skill_tool_ceiling
from services.skills.selection import SkillSelection
from services.tools import ToolPolicy, build_legacy_catalog
from tests.test_skill_assets import attachment, document, publish
from tests.test_skill_assets_runtime import source_for
from tests.test_skill_runtime import Source, activate, item, state
from tests.test_skill_storage import storage  # noqa: F401
from tests.test_tool_policy import context


def test_creation_default_is_distinct_from_reading_existing_content():
    raw = {'description': '用途', 'body': '按正文完成任务。', 'catalog_metadata': {'name': '测试'}}
    old = DraftContent.model_validate(raw)
    assert old.catalog_metadata.tool_policy == 'restricted'
    assert 'tool_policy' not in old.model_dump()['catalog_metadata']
    created = new_draft_content(old)
    assert created.catalog_metadata.tool_policy == 'platform'
    assert created.model_dump()['catalog_metadata']['tool_policy'] == 'platform'
    # The input (also used for reads/saves) is not silently upgraded.
    assert old.catalog_metadata.tool_policy == 'restricted'
    for metadata in ({'allowed_tool_names': []}, {'allowed_tool_names': ['file_search']},
                     {'tool_policy': 'restricted'}):
        explicit = DraftContent(catalog_metadata=metadata)
        assert new_draft_content(explicit) is explicit


def test_legacy_metadata_keeps_exact_serialized_shape_and_rejects_ambiguous_policy():
    metadata = SkillCatalogMetadata(name='旧版本', allowed_tool_names=['file_search'])
    assert metadata.model_dump(mode='json', exclude_unset=True) == {
        'name': '旧版本', 'allowed_tool_names': ['file_search'],
    }
    assert SkillCatalogMetadata.model_validate(metadata.model_dump()) == metadata
    with pytest.raises(ValidationError, match='SKILL_TOOL_POLICY_CONFLICT'):
        SkillCatalogMetadata(tool_policy='platform', allowed_tool_names=['file_delete'])


@pytest.mark.parametrize('mode', ['auto', 'ask', 'plan'])
@pytest.mark.parametrize('changes', [{}, {'org_id': None}, {'agent_domain': 'erp'},
    {'feature_flags': {}}, {'authorized_tool_names': set()},
    {'authorized_tool_names': {'file_search'}},
    {'execution_mode': 'scheduled'}, {'confirmation_available': False}])
def test_platform_policy_never_grants_access_or_skips_host_approval(mode, changes):
    registry = build_legacy_catalog()
    policy = ToolPolicy(registry)
    names = {spec.name for spec in registry.specs()}
    before = context(permission_mode=mode, **changes)
    initial = names if before.authorized_tool_names is None else names & before.authorized_tool_names
    ceiling = skill_tool_ceiling(SkillCatalogMetadata(tool_policy='platform'), names,
                                 initial)
    after = replace(before, authorized_tool_names=ceiling)
    for name in names:
        previous = policy.decide(name, before, {})
        current = policy.decide(name, after, {})
        assert previous.outcome == current.outcome, (name, mode, changes)


async def test_platform_activation_preserves_host_cap_and_cannot_undo_other_skill_limits():
    source = Source([item('platform', tools=(), tool_policy='platform'), item('limited'),
                     item('second', tools=(), tool_policy='platform')], body='Use available tools.')
    runtime = state(source, authorized_tool_names={'file_search', 'web_search'})
    await runtime.initialize()
    assert (await runtime.activate(activate('platform')))['ok']
    assert runtime.effective_allowed_tool_names == {'file_search', 'web_search'}
    assert (await runtime.activate(activate('limited')))['ok']
    assert (await runtime.activate(activate('second')))['ok']
    assert runtime.effective_allowed_tool_names == {'file_search'}


async def test_replay_does_not_acquire_new_tools_and_respects_revocations():
    source = Source([item(tools=(), tool_policy='platform')], body='Use available tools.')
    runtime = state(source, platform_tool_names={'file_search', 'web_search'})
    await runtime.initialize()
    await runtime.activate(activate())
    saved = runtime.checkpoint()
    for host, authorization, expected in [
        ({'file_search', 'web_search', 'file_delete'}, None, {'file_search', 'web_search'}),
        ({'file_search'}, None, {'file_search'}),
        ({'file_search', 'web_search'}, {'web_search'}, {'web_search'}),
    ]:
        resumed = state(source, platform_tool_names=host, authorized_tool_names=authorization)
        await resumed.initialize(saved)
        assert resumed.effective_allowed_tool_names == expected
        assert resumed.messages() == runtime.messages()


async def test_old_checkpoint_keeps_its_original_instructions_and_deny_all():
    source = Source([item(tools=())], body='Original instructions.')
    runtime = state(source)
    await runtime.initialize()
    await runtime.activate(activate())
    legacy = runtime.checkpoint()
    legacy.pop('context_version')
    restored = state(source)
    await restored.initialize(legacy)
    assert restored.effective_allowed_tool_names == set()
    assert restored.checkpoint() == legacy
    old_message = {'role': 'system', 'content':
        '[Turn Skill instructions: apply only within existing tool policy and authorization]\n'
        'skill_id=report revision=v1\nOriginal instructions.'}
    assert restored.messages()[-1] == old_message
    messages = [{'role': 'user', 'content': '继续'}, *restored.messages()]
    original = copy.deepcopy(messages)
    restored.ensure_messages(messages)
    assert messages == original
    assert restored.model_messages(messages, []) is messages


async def test_current_capabilities_follow_final_schemas_without_polluting_replay():
    runtime = state(Source(body='Use the tool when necessary.'))
    messages = [{'role': 'system', 'content': 'Host'}, {'role': 'user', 'content': '执行'}]
    assert runtime.model_messages(messages, []) is messages
    await runtime.initialize()
    await runtime.activate(activate())
    runtime.ensure_messages(messages)
    original = copy.deepcopy(messages)
    checkpoint = runtime.checkpoint()
    tools = [{'type': 'function', 'function': {'name': 'file_search'}}]
    with_tools = runtime.model_messages(messages, tools)
    assert '"available_tools":["file_search"]' in with_tools[-2]['content']
    without_tools = runtime.model_messages(messages, [])
    assert '当前没有可调用工具' in without_tools[-2]['content']
    assert '"available_tools":[]' in without_tools[-2]['content']
    assert len(with_tools) == len(original) + 1 and with_tools[-1] == original[-1]
    assert messages == original and runtime.checkpoint() == checkpoint


@pytest.mark.parametrize('manual', [False, True])
async def test_instructions_survive_compression_without_changing_user_or_tool_pairs(manual):
    source = Source(body='A different task from historical context.')
    runtime = state(source)
    selection = SkillSelection(skill_id='report', revision='v1') if manual else None
    await runtime.initialize(selection=selection)
    if manual:
        await runtime.activate_manual(selection)
    else:
        await runtime.activate(activate())
    tail = [{'role': 'user', 'content': '请分析。'},
            {'role': 'assistant', 'tool_calls': [{'id': 'call-1', 'type': 'function',
                'function': {'name': 'file_search', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'result'}]
    messages = [{'role': 'system', 'content': 'Host policy'}, *copy.deepcopy(tail)]
    for _ in range(2):
        runtime.ensure_messages(messages)
    assert messages[-3:] == tail
    assert messages[0]['content'] == 'Host policy'
    assert messages[2]['role'] == 'system'
    assert ('"selection":"user"' if manual else '"selection":"model"') in messages[2]['content']
    assert sum('Turn Skill instructions' in m.get('content', '') for m in messages) == 1
    # Simulate compression removing Skill context but retaining the task.
    messages = [messages[0], *copy.deepcopy(tail)]
    resumed = state(source)
    await resumed.initialize(runtime.checkpoint())
    resumed.ensure_messages(messages)
    assert messages[1:3] == runtime.messages()
    assert messages[-3:] == tail


@pytest.mark.parametrize('body,asset_text,kind', [
    ('Translate using glossary [[asset:guide]].', '账期 = payment term', 'reference'),
    ('Write a report with format [[asset:guide]].', '# Findings\n# Evidence', 'template'),
    ('Query the supplied data using method [[asset:guide]].', 'Ask for a date if missing, then query.', 'example_input'),
])
async def test_same_loader_delivers_different_task_methods_and_only_referenced_assets(storage, body, asset_text, kind):
    skill = publish(storage, document(attachment(content=asset_text, kind=kind),
        attachment('unused', content='UNREFERENCED_SECRET_CONTENT'), body=body))
    source = source_for(storage, skill)
    runtime = state(source)
    await runtime.initialize()
    assert (await runtime.activate(activate('skill0')))['ok']
    message = runtime.messages()[-1]['content']
    assert body in message and asset_text in message
    assert 'UNREFERENCED_SECRET_CONTENT' not in message
    assert '"kind":"' + kind + '"' in message
    assert runtime.active['skill0'].loaded_asset_ids == ('guide',)
    assert len(source.load_assets.await_args.args[2]) == 1
