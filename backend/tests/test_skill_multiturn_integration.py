"""Skill resources remain inside the current multi-turn execution contract."""
import copy
import json
from types import SimpleNamespace

import pytest

from services.conversation_commands import SafePoint
from services.skills.runtime import READ_SKILL_ASSET
from tests.test_model_directed_loop import case, execute
from tests.test_skill_assets import attachment, document, publish
from tests.test_skill_assets_runtime import source_for
from tests.test_skill_runtime import activate, state
from tests.test_skill_storage import storage  # noqa: F401
from tests.test_task_execution_model_bridge import call

REFERENCE = 'A private reference.'


async def attach_lazy_method(runtime, storage, *, load=True):
    published = publish(storage, document(attachment(loading='on_demand')))
    source = source_for(storage, published)
    skills = state(source, turn_id=runtime.turn_id)
    await skills.initialize()
    assert (await skills.activate(activate('skill0')))['ok']
    runtime.skill_runtime = skills
    if load:
        result = await skills.read_asset(json.dumps({'skill_id': 'skill0', 'asset_id': 'guide'}))
        assert result['ok'], result
    return source


async def test_lazy_read_stays_a_barrier_in_the_model_directed_loop(storage):
    runtime, prepared, handler, request = case()
    source = await attach_lazy_method(runtime, storage, load=False)
    await execute(runtime, prepared, handler, request, [
        call(READ_SKILL_ASSET, {'skill_id': 'skill0', 'asset_id': 'guide'}, 'read'),
        call('file_search', {'query': 'not this batch'}, 'business'),
    ])
    handler._execute_tool_calls.assert_not_awaited()
    assert runtime.skill_runtime.active['skill0'].loaded_asset_ids == ('guide',)
    source.load_assets.assert_awaited_once()
    assert runtime.task_execution_state is None
    assert len([message for message in prepared.messages if message.get('tool_calls')]) == 1
    assert {message['tool_call_id'] for message in prepared.messages if message.get('role') == 'tool'} == {
        'read', 'business',
    }


async def test_failed_resource_checkpoint_preserves_the_current_argument_repair_record(storage):
    async def reject(point, payload):
        if point == SafePoint.AFTER_SKILL_ACTIVATION:
            raise RuntimeError('save failed')
        return {'outcome': 'saved'}
    runtime, prepared, handler, request = case(reject)
    await attach_lazy_method(runtime, storage, load=False)
    from services.handlers.chat import tool_argument_repairs as repair
    previous = repair.Checkpoint(input_id='input', input_version='1' * 64,
                                 counts={'target': 2}, stopped=False).model_dump(mode='json')
    runtime.checkpoint_base[repair.KEY] = copy.deepcopy(previous)
    before, messages = runtime.skill_runtime.checkpoint(), copy.deepcopy(prepared.messages)
    with pytest.raises(RuntimeError, match='save failed'):
        await execute(runtime, prepared, handler, request, [
            call(READ_SKILL_ASSET, {'skill_id': 'skill0', 'asset_id': 'guide'}, 'read'),
        ])
    assert runtime.skill_runtime.checkpoint() == before
    assert prepared.messages == messages
    assert runtime.checkpoint_base[repair.KEY] == previous
    handler._execute_tool_calls.assert_not_awaited()


def test_reviewed_size_rule_and_on_demand_state_coexist_without_rewriting_the_method():
    from services.skills.runtime import ActiveSkill
    from services.skills.renderer import digest, argument_summary
    from services.skills.context import instruction_message
    from services.handlers.image_size_requirements import SIZE_GUIDANCE
    from services.task_execution.methods import PROFILES, REFERENCE_PROMPTS_PROFILE
    (package, key, revision, sha), = [identity for identity, profile in PROFILES.items()
                                     if profile[0] == REFERENCE_PROMPTS_PROFILE]
    body = '原版步骤：不得改写。'
    active = ActiveSkill(skill_key=key, revision=revision, body_sha256=sha, rendered=body,
                         rendered_sha256=digest(body), args_summary=argument_summary({}),
                         effective_allowed_tool_names=frozenset(), deferred_asset_ids=('guide',))
    candidate = SimpleNamespace(package_id=package, skill_key=key, description='参考图提示词',
                                catalog_metadata=SimpleNamespace(task_stage=None, name='参考图提示词'))
    content = instruction_message(active, candidate, manual=True, context_version=2)['content']
    assert SIZE_GUIDANCE in content and '[Skill attachment load state]' in content
    assert 'guide' in content and content.count(body) == 1
    assert active.rendered == body
    candidate.package_id = 'custom-package'
    unrelated = instruction_message(active, candidate, manual=True, context_version=2)['content']
    assert SIZE_GUIDANCE not in unrelated and '[Skill attachment load state]' in unrelated


async def test_successive_edit_drafts_keep_the_selected_outputs_under_a_loaded_skill(storage):
    from dataclasses import replace
    from uuid import uuid4
    from schemas.message import TextPart
    from services.task_execution import content_objects as content
    from tests.test_content_objects import registered, ref
    runtime, prepared, handler, request, original = await registered()
    source = await attach_lazy_method(runtime, storage)
    original_input, _ = content.compile_input(runtime.task_execution_state, 'generate_image', {}, ref(original, 2))
    saved = {original['artifact_id']}
    for index, instruction in enumerate(('只把背景调浅，保留福字主体。', '在刚才的成图上移除桌面杂物，保留浅色背景。')):
        # Each user-selected result is a different image occurrence; neither is
        # the product image used by the original plan.
        selected = {'message_id': str(uuid4()), 'content_index': index + 1}
        request = replace(request, content=[TextPart(text=instruction)])
        await execute(runtime, prepared, handler, request, [call(content.SAVE, {
            'document': {'title': f'编辑{index}', 'items': [{
                'title': '修改已选成图', 'body': instruction,
                'materials': [{'reference': selected, 'role': '本轮选定成图', 'usage': 'input'}],
            }]}, 'new_content': True, 'continue_execution': True,
        }, f'edit-{index}')])
        edited, = [row for row in content.catalog(runtime.task_execution_state) if row['artifact_id'] not in saved]
        saved.add(edited['artifact_id'])
        native, _ = content.compile_input(runtime.task_execution_state, 'generate_image', {}, ref(edited))
        assert native['mode'] == 'image_to_image'
        assert native['references'] == [{**selected, 'role': '本轮选定成图'}]
        assert native['prompt'] == instruction
        assert native['references'] != original_input['references']
    preserved, _ = content.compile_input(runtime.task_execution_state, 'generate_image', {}, ref(original, 2))
    assert preserved == original_input
    source.load_assets.assert_awaited_once()
    handler._execute_tool_calls.assert_not_awaited()  # Recipe checks do not buy images.
