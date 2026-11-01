"""DAG capability gates narrow disclosure and native chat dispatch together."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.handlers.chat.execution_engine import _execute_tools
from services.task_execution.contracts import PlanStep
from services.task_execution.dispatch import annotate_task_tools, plan_dispatch_blockers
from services.task_execution.reducer import complete_step, register_artifacts
from services.tools.catalog import build_capability_catalog
from services.tools.registry import ToolRegistry
from services.tools.spec import Exposure
from tests.test_task_execution_model_bridge import setup_case, call
from tests.test_task_execution_reducer import state, plan, artifact


def planned():
    return plan(state(), (
        PlanStep(id='prepare', title='先分析'),
        PlanStep(id='read', title='按分析读取资料', depends_on=('prepare',),
            capability='workspace.file.search', requirement_ids=('report',)),
    ))


def prepared_state():
    current = planned()
    current = register_artifacts(current, [artifact(current, 'prepare')])
    return complete_step(current, 'prepare', ['prepare'])


def test_dependency_gate_follows_actual_registry_capability_not_tool_name():
    spec = build_capability_catalog().get('file_search')
    registry = ToolRegistry([replace(spec, name='renamed_reader', schema=None,
        exposure=Exposure.LEGACY_INTERNAL)])
    assert plan_dispatch_blockers(planned(), registry) == {'renamed_reader': 'TASK_DEPENDENCY_INCOMPLETE'}
    assert not plan_dispatch_blockers(prepared_state(), registry)


def test_support_tools_and_legacy_requests_retain_existing_policy_path():
    catalog = build_capability_catalog()
    assert not plan_dispatch_blockers(None, catalog)
    assert 'generate_image' not in plan_dispatch_blockers(planned(), catalog)
    assert plan_dispatch_blockers(planned().model_copy(update={'status': 'waiting_dependencies'}), catalog)[
        'file_search'] == 'TASK_NOT_ACTIVE'


def test_capability_disclosure_only_annotates_already_authorized_tools_without_mutation():
    catalog = build_capability_catalog()
    original = catalog.get('file_search').to_schema()
    description = original['function']['description']
    result = annotate_task_tools([original], catalog)
    assert len(result) == 1
    assert result[0]['function']['name'] == 'file_search'
    assert 'workspace.file.search' in result[0]['function']['description']
    assert original['function']['description'] == description
    assert result[0]['function']['parameters'] == original['function']['parameters']


def test_completed_capability_does_not_dispatch_again():
    current = prepared_state()
    current = register_artifacts(current, [artifact(current, 'read', kind='file')])
    current = complete_step(current, 'read', ['read'])
    assert plan_dispatch_blockers(current, build_capability_catalog())['file_search'] == 'TASK_STEP_COMPLETED'


@pytest.mark.asyncio
@pytest.mark.parametrize('ready', [False, True])
async def test_real_chat_dispatch_continues_with_closed_rejection_or_normal_tool_result(monkeypatch, ready):
    from services.tools.runtime_context import catalog_context
    current = prepared_state() if ready else planned()
    runtime, prepared, handler, sink = setup_case(initial=current)
    prepared.execution_context = catalog_context(None)
    prepared.permission = SimpleNamespace(mode=SimpleNamespace(value='auto'))
    prepared.budget = SimpleNamespace()
    prepared.tool_context = SimpleNamespace(update_from_batch=lambda _: None,
        build_context_prompt=lambda: None, discovered_tools=set())
    handler._get_conv_source = lambda _: 'web'
    handler._pending_emit_payloads = []
    request = SimpleNamespace(task_id=runtime.task_id, conversation_id=runtime.conversation_id,
        message_id='output', user_id=str(current.actor_user_id))
    native_call = call('file_search', {'query': '根据前置分析找资料'}, 'read-call')
    handler._execute_tool_calls = AsyncMock(return_value=[(native_call, '已读取真实资料', False, '已读取真实资料')])
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    blocks = []
    await _execute_tools(handler=handler, request=request, prepared=prepared, turn=0,
        turn_text='', calls=[native_call], previewed_call_ids=set(),
        cancellation_event=runtime.cancellation_event, sink=sink, blocks=blocks, runtime=runtime)
    if ready:
        handler._execute_tool_calls.assert_awaited_once()
    else:
        handler._execute_tool_calls.assert_not_called()
        assert blocks[0]['status'] == 'error'
        assert '前置成果' in blocks[0]['output']
    assert runtime.task_execution_state is current
    assert prepared.messages[-1]['role'] == 'tool'
    assert prepared.messages[-1]['tool_call_id'] == 'read-call'
    payload = runtime._replay_checkpoint_callback.call_args.args[1]
    assert payload['messages'][-1]['tool_call_id'] == 'read-call'


@pytest.mark.asyncio
@pytest.mark.parametrize('capability', ['file_search', 'platform.file_search', 'invented.capability'])
async def test_actual_plan_control_rejects_unknown_capability_without_adopting_plan(capability):
    import json
    from tests.test_task_execution_model_bridge import execute
    runtime, prepared, handler, sink = setup_case()
    before = runtime.task_execution_state
    blocks = []
    await execute(runtime, prepared, handler, sink, [call('update_task_plan', {
        'expected_revision': before.revision, 'steps': [{'id': 'read', 'title': '读取资料',
            'requirement_ids': ['report'], 'capability': capability}]} )], blocks)
    assert runtime.task_execution_state is before
    result = json.loads(prepared.messages[-1]['content'])
    assert result['code'] == 'TASK_PLAN_CAPABILITY_UNKNOWN'
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_actual_plan_control_accepts_canonical_capability_and_gates_dependency():
    from tests.test_task_execution_model_bridge import execute
    runtime, prepared, handler, sink = setup_case()
    before = runtime.task_execution_state
    await execute(runtime, prepared, handler, sink, [call('update_task_plan', {
        'expected_revision': before.revision, 'steps': [step.model_dump(mode='json')
            for step in planned().steps]})], [])
    assert runtime.task_execution_state.revision == before.revision + 1
    assert plan_dispatch_blockers(runtime.task_execution_state, build_capability_catalog())[
        'file_search'] == 'TASK_DEPENDENCY_INCOMPLETE'


@pytest.mark.asyncio
async def test_unknown_plan_capability_returns_existing_host_gate_for_safe_repair(monkeypatch):
    import json
    from tests.test_task_execution_methods import bound
    from tests.test_task_execution_model_bridge import execute
    initial,skills=await bound(monkeypatch)
    runtime,prepared,handler,sink=setup_case(initial=initial)
    runtime.skill_runtime=skills
    steps=[step.model_dump(mode='json') for step in initial.steps]
    steps[-1]['capability']='generate_image'
    await execute(runtime,prepared,handler,sink,[call('update_task_plan',{
        'expected_revision':initial.revision,'steps':steps})],[])
    result=json.loads(prepared.messages[-1]['content'])
    assert result['code']=='TASK_PLAN_CAPABILITY_UNKNOWN'
    assert set(result['capability_hints'])=={gate.capability for gate in initial.capability_gates}
    assert runtime.task_execution_state==initial
    assert runtime.skill_runtime.effective_allowed_tool_names==frozenset()
    assert 'platform.generate_image' in result['capability_hints']
    steps[-1]['capability']='platform.generate_image'
    await execute(runtime,prepared,handler,sink,[call('update_task_plan',{
        'expected_revision':initial.revision,'steps':steps},'repair')],[])
    assert json.loads(prepared.messages[-1]['content'])['ok']
    from services.task_execution.methods import blocked_tool_names
    from services.tools.catalog import build_capability_catalog
    assert 'generate_image' in blocked_tool_names(runtime.task_execution_state,build_capability_catalog())
    handler._execute_tool_calls.assert_not_called()
