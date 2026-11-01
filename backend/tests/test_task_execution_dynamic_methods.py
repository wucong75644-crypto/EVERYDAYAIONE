"""A later Skill activation adds host obligations in the existing Actor batch."""
import copy
from types import SimpleNamespace

import pytest

from services.conversation_commands import CommandType, ConversationCommand
from services.conversation_state import ConversationPauseRequested
from services.handlers.chat.execution_engine import _execute_tools
from services.skills.contracts import SkillCatalogMetadata
from services.task_execution.methods import append_active_methods
from tests.test_skill_runtime import Source, item, state as skill_state
from tests.test_skill_runtime_actor import prepared as actor_prepared
from tests.test_task_execution_bootstrap import bootstrap, proposal
from tests.test_task_execution_model_bridge import setup_case, call


async def dynamic_setup(checkpoint=None, *, text_only=False):
    candidate = item('new-method', tools=(), task_stage={
        'version': 1, 'output_profile': 'reference-prompts-v1', 'restriction_scope': 'phase'})
    skills = skill_state(Source([candidate], body='明确的逐图规划与交接方法。'),
        platform_tool_names={'generate_image', 'file_search'},
        authorized_tool_names={'generate_image', 'file_search'})
    await skills.initialize()
    source, *_ = bootstrap()
    value = ({'action': 'plan', 'requirements': [{'id': 'prompts', 'description': '五条完整提示词',
        'artifact_kind': 'text', 'item_count': 5}]} if text_only else proposal())
    state = source.initialize(value, model_rounds_used=1)
    runtime, _, handler, sink = setup_case(checkpoint, state)
    runtime.skill_runtime = skills
    prepared = actor_prepared()
    return runtime, prepared, handler, sink


async def activate_method(runtime, prepared, handler, sink, blocks):
    request = SimpleNamespace(task_id=runtime.task_id, conversation_id=runtime.conversation_id,
        message_id='output', user_id=str(runtime.task_execution_state.actor_user_id))
    await _execute_tools(handler=handler, request=request, prepared=prepared, turn=0,
        turn_text='', calls=[call('activate_skill', {'skill_id': 'new-method'}, 'activate')],
        previewed_call_ids=set(), cancellation_event=runtime.cancellation_event,
        sink=sink, blocks=blocks, runtime=runtime, next_model_round=2)


@pytest.mark.asyncio
async def test_declared_profile_is_added_after_start_and_keeps_user_requirements():
    runtime, prepared, handler, sink = await dynamic_setup()
    before = runtime.task_execution_state
    blocks = []
    await activate_method(runtime, prepared, handler, sink, blocks)
    after = runtime.task_execution_state
    assert after.requirements[:len(before.requirements)] == before.requirements
    assert len(after.requirements) == len(before.requirements) + 1
    assert after.revision == before.revision + 1
    assert after.method_bindings[0].contract_source == 'declared'
    assert after.method_bindings[0].retire_on_complete is True
    payload = runtime._replay_checkpoint_callback.call_args.args[1]
    assert payload['_task_execution']['revision'] == after.revision
    assert payload['skill_runtime']['active'][0]['skill_key'] == 'new-method'
    assert payload['messages'][-1]['tool_call_id'] == 'activate'
    assert append_active_methods(after, runtime.skill_runtime) is after
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_append_failure_rolls_back_task_skill_messages_and_scope():
    async def checkpoint(*args):
        raise RuntimeError('database unavailable')
    runtime, prepared, handler, sink = await dynamic_setup(checkpoint)
    original, skills, messages, context = (runtime.task_execution_state, runtime.skill_runtime,
        copy.deepcopy(prepared.messages), prepared.execution_context)
    with pytest.raises(RuntimeError, match='database unavailable'):
        await activate_method(runtime, prepared, handler, sink, [])
    assert runtime.task_execution_state is original and runtime.skill_runtime is skills
    assert not skills.active and prepared.messages == messages
    assert prepared.execution_context is context


@pytest.mark.asyncio
async def test_pause_after_append_save_retains_new_obligations_and_pairs():
    runtime, prepared, handler, sink = await dynamic_setup()
    async def checkpoint(*args):
        runtime.push(ConversationCommand(command_id='pause', command_type=CommandType.PAUSE,
            conversation_id=runtime.conversation_id, task_id=runtime.task_id))
        return {'outcome': 'saved'}
    runtime._replay_checkpoint_callback = checkpoint
    blocks = []
    with pytest.raises(ConversationPauseRequested):
        await activate_method(runtime, prepared, handler, sink, blocks)
    assert runtime.task_execution_state.method_bindings[0].skill_key == 'new-method'
    assert 'new-method' in runtime.skill_runtime.active
    assert any(m.get('tool_call_id') == 'activate' for m in prepared.messages)


def test_old_metadata_serialization_is_unchanged_without_stage_declaration():
    assert 'task_stage' not in SkillCatalogMetadata().model_dump(mode='json')


def test_unknown_output_protocol_is_rejected_at_published_metadata_boundary():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SkillCatalogMetadata(task_stage={'output_profile': 'model-says-completed'})


@pytest.mark.asyncio
async def test_later_selected_method_binds_text_only_plan_with_requested_item_count():
    runtime, prepared, handler, sink = await dynamic_setup(text_only=True)
    assert not runtime.task_execution_state.applicable_method_ids
    await activate_method(runtime, prepared, handler, sink, [])
    state = runtime.task_execution_state
    assert state.applicable_method_ids == ('new-method',)
    assert state.method_bindings[0].image_count == 5
    assert state.requirements[0].minimum == 1
    assert state.requirements[0].item_count == 5


@pytest.mark.asyncio
async def test_append_does_not_bind_unselected_preloaded_method_to_text_plan():
    from tests.test_skill_runtime import activate
    runtime, *_ = await dynamic_setup(text_only=True)
    skills = runtime.skill_runtime
    assert (await skills.activate(activate('new-method')))['ok']
    before = runtime.task_execution_state
    assert append_active_methods(before, skills, activated_skill_id='another-method') is before
