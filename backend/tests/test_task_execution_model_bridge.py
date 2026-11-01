"""Exercise actual chat dispatch boundary and Actor save/pause behavior."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.conversation_commands import CommandType, ConversationCommand
from services.conversation_state import ConversationPauseRequested
from services.conversation_turn_runtime import ConversationTurnRuntime
from services.handlers.chat.execution_engine import _execute_tools
from tests.test_task_execution_reducer import plan, state


def setup_case(checkpoint=None, initial=None):
    initial = initial or state()
    runtime = ConversationTurnRuntime(conversation_id=str(initial.conversation_id),
        task_id=str(initial.root_task_id), turn_id='turn', cancellation_event=asyncio.Event(),
        replay_checkpoint_callback=checkpoint or AsyncMock(return_value={'outcome': 'saved'}))
    runtime.task_execution_state = initial
    prepared = SimpleNamespace(messages=[{'role': 'user', 'content': initial.original_goal}],
        last_provider_output={'id': 'provider-output'}, last_reasoning_content='reasoning')
    handler = SimpleNamespace(_execute_tool_calls=AsyncMock(side_effect=AssertionError('business IO')))
    sink = SimpleNamespace(on_block=AsyncMock(), on_block_update=AsyncMock())
    return runtime, prepared, handler, sink


def call(name='update_task_plan', arguments=None, key='control'):
    return {'id': key, 'name': name, 'arguments': json.dumps(arguments if arguments is not None else {
        'expected_revision': 0, 'steps': [{'id': 'report', 'title': '报告', 'requirement_ids': ['report']}]
    })}


async def execute(runtime, prepared, handler, sink, calls, blocks):
    return await _execute_tools(handler=handler, request=SimpleNamespace(), prepared=prepared,
        turn=0, turn_text='', calls=calls, previewed_call_ids=set(),
        cancellation_event=runtime.cancellation_event, sink=sink, blocks=blocks,
        runtime=runtime, next_model_round=1)


@pytest.mark.asyncio
async def test_mixed_control_business_batch_saves_complete_pairs_without_business_dispatch():
    snapshots = []

    async def checkpoint(point, payload):
        snapshots.append(copy.deepcopy(payload))
        return {'outcome': 'saved'}

    runtime, prepared, handler, sink = setup_case(checkpoint)
    calls = [call(), call('generate_image', {'prompt': 'premature'}, 'image')]
    blocks = []
    await execute(runtime, prepared, handler, sink, calls, blocks)
    saved = snapshots[0]
    assert len(snapshots) == 1 and runtime.task_execution_state.revision == 1
    assert [m['tool_call_id'] for m in saved['messages'] if m['role'] == 'tool'] == ['control', 'image']
    results = [json.loads(m['content']) for m in saved['messages'] if m['role'] == 'tool']
    assert results[0]['ok'] and results[1]['code'] == 'TASK_CONTROL_BATCH_BARRIER'
    assert saved['_task_execution']['revision'] == 1
    assistant = saved['messages'][1]
    assert assistant['_dashscope_output'] == prepared.last_provider_output
    assert assistant['reasoning_content'] == 'reasoning'
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['disabled', 'exception'])
async def test_unconfirmed_save_restores_live_state_and_never_delivers_success(failure):
    async def checkpoint(*args):
        if failure == 'exception':
            raise RuntimeError('connection lost')
        return {'outcome': failure}

    runtime, prepared, handler, sink = setup_case(checkpoint)
    original = copy.deepcopy(prepared.messages)
    blocks = []
    with pytest.raises(RuntimeError):
        await execute(runtime, prepared, handler, sink, [call()], blocks)
    assert prepared.messages == original and blocks == []
    assert runtime.task_execution_state.revision == 0
    assert runtime.replay_save_sequence == 0
    sink.on_block_update.assert_not_called()
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_pause_after_confirmed_save_retains_new_state_and_closed_history():
    runtime, prepared, handler, sink = setup_case()
    runtime.push(ConversationCommand(command_id='pause', command_type=CommandType.PAUSE,
        conversation_id=runtime.conversation_id, task_id=runtime.task_id))
    blocks = []
    with pytest.raises(ConversationPauseRequested):
        await execute(runtime, prepared, handler, sink, [call()], blocks)
    assert runtime.task_execution_state.revision == 1
    assert runtime.committed_task_execution_revision == 1
    assert prepared.messages[-1]['role'] == 'tool'
    assert blocks[-1]['status'] == 'completed'
    sink.on_block_update.assert_not_called()


@pytest.mark.asyncio
async def test_parallel_controls_are_rejected_without_inferred_order():
    runtime, prepared, handler, sink = setup_case()
    await execute(runtime, prepared, handler, sink, [call(), call(key='second')], [])
    assert runtime.task_execution_state.revision == 0
    assert all(json.loads(m['content'])['code'] == 'TASK_CONTROL_BATCH_BARRIER'
               for m in prepared.messages if m['role'] == 'tool')


@pytest.mark.asyncio
async def test_saved_text_and_display_are_in_same_checkpoint_before_delivery():
    captured = []

    async def checkpoint(point, payload):
        assert not sink.on_block.called
        captured.append(copy.deepcopy(payload))
        return {'outcome': 'saved'}

    runtime, prepared, handler, sink = setup_case(checkpoint, plan(state()))
    await execute(runtime, prepared, handler, sink, [call('save_task_text', {
        'expected_revision': 1, 'step_id': 'research', 'text': '完整研究资料'}, 'text')], [])
    assert captured[0]['_task_execution']['artifacts'][0]['inline_text'] == '完整研究资料'
    text = captured[0]['content_blocks'][-1]
    assert text['text'] == '完整研究资料' and text['task_artifact_id']
    sink.on_block.assert_awaited_once_with(text)


@pytest.mark.asyncio
async def test_malformed_arguments_return_paired_error_without_mutating_plan():
    runtime, prepared, handler, sink = setup_case()
    malformed = {**call(), 'arguments': '{invalid'}
    await execute(runtime, prepared, handler, sink, [malformed], [])
    assert runtime.task_execution_state.revision == 0
    assert json.loads(prepared.messages[-1]['content'])['code'] == 'TASK_CONTROL_INPUT_INVALID'


def test_legacy_runtime_has_no_control_schemas():
    from services.task_execution.model_bridge import controller_for
    runtime, *_ = setup_case()
    runtime.task_execution_state = None
    assert controller_for(runtime) is None


@pytest.mark.asyncio
async def test_final_response_without_delivery_continues_then_stops_partial_with_requirements():
    from services.handlers.chat.execution_engine import _build_replay_context
    from services.task_execution.model_bridge import review_model_completion
    runtime, prepared, handler, sink = setup_case(initial=plan(state()))
    for expected in (True, True, True, False):
        again = await review_model_completion(runtime=runtime, prepared=prepared, blocks=[],
            build_checkpoint=_build_replay_context, turn=0, next_model_round=1)
        assert again is expected
    assert runtime.task_execution_state.status == 'partial'
    assert runtime.task_execution_state.requirements[0].minimum == 1
    assert runtime.task_execution_state.artifacts == ()
    assert runtime.task_execution_state.correction_count == 3
    handler._execute_tool_calls.assert_not_called()


@pytest.mark.asyncio
async def test_real_completed_delivery_finishes_without_extra_model_call():
    from services.handlers.chat.execution_engine import _build_replay_context
    from services.task_execution.contracts import PlanStep
    from services.task_execution.model_bridge import review_model_completion
    from services.task_execution.reducer import complete_step, register_artifacts
    from tests.test_task_execution_reducer import artifact
    initial = plan(state(), [PlanStep(id='report', title='报告', requirement_ids=('report',))])
    initial = register_artifacts(initial, [artifact(initial, 'report', 'file')])
    initial = complete_step(initial, 'report', ['report'])
    runtime, prepared, handler, sink = setup_case(initial=initial)
    assert not await review_model_completion(runtime=runtime, prepared=prepared, blocks=[],
        build_checkpoint=_build_replay_context, turn=0, next_model_round=1)
    assert runtime.task_execution_state.status == 'completed'
    assert runtime.task_execution_state.correction_count == 0


@pytest.mark.asyncio
async def test_completion_save_failure_restores_state_and_correction_message():
    from services.handlers.chat.execution_engine import _build_replay_context
    from services.task_execution.model_bridge import review_model_completion
    runtime, prepared, handler, sink = setup_case(AsyncMock(return_value={'outcome': 'disabled'}))
    initial, messages = runtime.task_execution_state, copy.deepcopy(prepared.messages)
    with pytest.raises(RuntimeError, match='NOT_SAVED'):
        await review_model_completion(runtime=runtime, prepared=prepared, blocks=[],
            build_checkpoint=_build_replay_context, turn=0, next_model_round=1)
    assert runtime.task_execution_state == initial and prepared.messages == messages


@pytest.mark.asyncio
async def test_model_budget_is_persisted_before_dispatch_and_not_reset_on_restore():
    from services.conversation_commands import SafePoint
    from services.handlers.chat.execution_engine import _build_replay_context
    from services.task_execution.contracts import TaskState, TaskError
    from services.task_execution.model_bridge import commit_projection, model_round_candidate
    runtime, prepared, handler, sink = setup_case()
    await commit_projection(runtime=runtime, candidate=model_round_candidate(runtime), prepared=prepared,
        blocks=[], point=SafePoint.BEFORE_MODEL, build_checkpoint=_build_replay_context,
        turn=0, next_model_round=0)
    payload = runtime._replay_checkpoint_callback.call_args.args[1]
    assert payload['_task_execution']['model_rounds_used'] == 1
    runtime.task_execution_state = TaskState.model_validate({**payload['_task_execution'],
        'model_round_limit': 1})
    with pytest.raises(TaskError, match='TASK_BUDGET_EXHAUSTED'):
        model_round_candidate(runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['logical_limit', 'outer_limit', 'false_final', 'fast_knowledge'])
async def test_real_loop_revision_matches_model_context_and_finishes_budget_honestly(monkeypatch, boundary):
    from services.handlers.chat.execution_engine import (
        _run_loop, _RepeatedToolCallGuard, ChatExecutionRequest)
    from services.handlers.chat.stream_session import StreamTotals
    from services.task_execution.contracts import TaskState
    from services.tools.runtime_context import catalog_context
    initial = state()
    if boundary == 'logical_limit':
        initial = TaskState.model_validate({**initial.model_dump(), 'model_round_limit': 1})
    snapshots = []

    async def checkpoint(point, payload):
        snapshots.append(copy.deepcopy(payload))
        return {'outcome': 'saved'}

    runtime, prepared, handler, sink = setup_case(checkpoint, initial)
    prepared.budget = SimpleNamespace(stop_reason=None, turns_used=0)

    def use_turn():
        prepared.budget.turns_used += 1
        if boundary == 'outer_limit':
            prepared.budget.stop_reason = 'max_turns'

    prepared.budget.use_turn = use_turn
    prepared.core_tools = []
    prepared.execution_context = catalog_context(None)
    prepared.tool_context = SimpleNamespace(discovered_tools=set())
    prepared.permission = SimpleNamespace(mode=SimpleNamespace(value='auto'),
        need_exit_attachment=False, get_reminder=lambda _: '')
    if boundary == 'fast_knowledge':
        prepared.knowledge_answer_plan = {'mode': 'answer'}
        handler._knowledge_run = SimpleNamespace(revalidate=lambda: None)
    handler.org_id = None
    handler._calculate_credits = lambda _: 0
    handler._pending_emit_payloads = [{'kind': 'file', 'name': 'research.txt',
        'url': 'https://example.invalid/research.txt', 'mime_type': 'text/plain'}]
    handler._pending_change_set_blocks = []
    handler._pending_form_block = None
    sink.on_text = AsyncMock()
    sink.on_thinking = AsyncMock()
    samples = []

    async def read_turn(prepared, tools, *args, **kwargs):
        facts = next(m for m in prepared.messages if isinstance(m.get('content'), str)
                     and m['content'].startswith('[Current task execution facts]'))
        view = json.loads(facts['content'].split('\n')[-1])
        assert view['revision'] == runtime.task_execution_state.revision
        assert snapshots[-1]['_task_execution']['revision'] == view['revision']
        samples.append(view['revision'])
        if boundary in {'false_final', 'fast_knowledge'}:
            return '全部完成', '', [], set()
        assert any(t['function']['name'] == 'update_task_plan' for t in tools)
        return '', '', [call(arguments={'expected_revision': view['revision'], 'steps': [
            {'id': 'report', 'title': '报告', 'requirement_ids': ['report']}]
        })], set()

    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', read_turn)
    guard = _RepeatedToolCallGuard()
    guard.observe([call('erp_execute', {}, 'old')])
    blocks = []
    request = ChatExecutionRequest(content=[], user_id=str(initial.actor_user_id),
        conversation_id=str(initial.conversation_id), task_id=str(initial.root_task_id),
        message_id='output', model_id='offline', context_anchor=None)
    from services.handlers.chat.execution_engine import _finalize_output_parts
    totals = StreamTotals()
    finalized = []

    async def finalize_task():
        parts = await _finalize_output_parts(handler=handler, prepared=prepared,
            totals=totals, blocks=blocks, sink=sink,
            cancellation_event=runtime.cancellation_event, runtime=runtime)
        finalized.extend(parts)
        return parts

    await _run_loop(handler=handler, request=request, prepared=prepared,
        cancellation_event=runtime.cancellation_event, sink=sink, totals=totals,
        blocks=blocks, runtime=runtime, repeated_tool_call_guard=guard, finalize_task=finalize_task)
    assert runtime.task_execution_state.status == 'partial'
    notice = next(b for b in blocks if b.get('task_status') == 'partial')
    assert '待完成：交付报告文件' in notice['text']
    assert snapshots[-1]['repeated_tool_call_guard'] == guard.replay_state()
    assert snapshots[-1]['checkpoint_kind'] == 'commit_ready'
    assert any('待完成：交付报告文件' in p.get('text', '') for p in snapshots[-1]['result_content'])
    assert any(p.get('type') == 'file' and p['name'] == 'research.txt'
               for p in snapshots[-1]['result_content'])
    from schemas.message import serialize_content_parts
    assert snapshots[-1]['result_content'] == serialize_content_parts(finalized)
    # Crash immediately after that save: the actual Actor executor can commit
    # the exact persisted result without constructing a handler or sampling.
    from services.conversation_execution import GenerationClaim
    from services.handlers.chat.executor import ChatGenerationExecutor
    from tests.test_chat_generation_executor import _DB, _ReplayCheckpointStore
    saved = snapshots[-1]
    store = _ReplayCheckpointStore()
    store.read_result = {'outcome': 'found', 'payload': saved}
    actor, conversation = str(initial.actor_user_id), str(initial.conversation_id)
    source = str(initial.source_message_ids[0])
    db = _DB({'id': source, 'conversation_id': conversation, 'turn_id': 'turn',
              'role': 'user', 'content': [{'type': 'text', 'text': initial.original_goal}]},
             conversation={'id': conversation, 'org_id': None, 'user_id': actor,
                           'scope_type': 'user', 'source': 'web'})
    executor = ChatGenerationExecutor(db,
        handler_factory=lambda _: pytest.fail('terminal restore must not construct handler'),
        replay_checkpoint_store=store)
    claim = GenerationClaim(task_id=str(initial.root_task_id), execution_token='token',
        conversation_id=conversation, turn_id='turn', input_message_id=source,
        base_context_revision=0, context_through_message_id=None,
        execution_attempt=2, execution_mode='serial')
    restored = await executor.execute({'id': claim.task_id, 'conversation_id': conversation,
        'assistant_message_id': 'output', 'user_id': actor, 'org_id': None},
        claim, asyncio.Event())
    assert restored.result_content == saved['result_content']
    assert restored.usage == saved['usage'] and restored.credits_cost == saved['credits_cost']
    if boundary not in {'false_final', 'fast_knowledge'}:
        assert len(samples) == 1
        assert runtime.task_execution_state.steps[0].id == 'report'
        control_save = next(p for p in snapshots if p['tool_call_ids'])
        assert json.loads(control_save['messages'][-1]['content'])['ok']
        assert control_save['repeated_tool_call_guard'] == guard.replay_state()
    elif boundary == 'fast_knowledge':
        assert len(samples) == 1
    else:
        assert len(samples) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('command_type', [CommandType.PAUSE, CommandType.CANCEL])
async def test_noop_control_saved_before_command_keeps_paired_history(command_type):
    from services.conversation_state import ConversationStopRequested
    runtime, prepared, handler, sink = setup_case()
    runtime.push(ConversationCommand(command_id='stop', command_type=command_type,
        conversation_id=runtime.conversation_id, task_id=runtime.task_id))
    blocks = []
    with pytest.raises((ConversationPauseRequested, ConversationStopRequested)):
        await execute(runtime, prepared, handler, sink,
            [call(arguments={'expected_revision': 999, 'steps': []})], blocks)
    assert runtime.task_execution_state.revision == 0 and runtime.replay_save_sequence == 1
    assert prepared.messages[-1]['role'] == 'tool' and blocks[-1]['status'] == 'error'


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', ['completed', 'partial'])
async def test_resume_logical_terminal_before_commit_ready_does_not_sample(monkeypatch, terminal):
    from services.handlers.chat.execution_engine import _run_loop
    from services.task_execution.contracts import PlanStep, TaskState
    from services.task_execution.reducer import complete_step, finish, register_artifacts
    from tests.test_task_execution_reducer import artifact
    initial = plan(state(), [PlanStep(id='report', title='报告', requirement_ids=('report',))])
    if terminal == 'completed':
        initial = register_artifacts(initial, [artifact(initial, 'report', 'file')])
        initial = finish(complete_step(initial, 'report', ['report']))
    else:
        initial = TaskState.model_validate({**initial.model_dump(), 'status': 'partial',
            'correction_count': 3, 'model_rounds_used': 4})
    runtime, prepared, handler, sink = setup_case(initial=initial)
    read = AsyncMock(side_effect=AssertionError('must not resample terminal task'))
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', read)
    saved = [{'type': 'text', 'text': '已保存的交付与状态', 'task_status': terminal}]
    blocks = copy.deepcopy(saved)
    await _run_loop(handler=handler, request=SimpleNamespace(), prepared=prepared,
        cancellation_event=runtime.cancellation_event, sink=sink, totals=SimpleNamespace(),
        blocks=blocks, runtime=runtime)
    read.assert_not_called()
    assert blocks == saved and runtime.task_execution_state == initial
