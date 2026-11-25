"""Exercise the checkpointed Actor argument guard through normal batch and recovery entry points."""

import copy
import asyncio
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.conversation_commands import SafePoint
from services.handlers.chat import tool_argument_repairs as repairs
from services.handlers.chat.execution_engine import _execute_tools, _run_loop
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat.stream_session import StreamTotals
from services.task_execution.pending_batch import pending_calls, recover_pending_batch
from services.task_execution.user_changes import UserChange, UserChanges
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_model_bridge import call
from tests.tool_context_test_utils import parse_json_tool_content


WEB = {'name': 'web_search', 'parameters': {
    'type': 'object', 'required': ['query'],
    'properties': {'query': {'type': 'string'}, 'page': {'type': 'integer'}},
}}


@pytest.fixture(autouse=True)
def disable_compaction(monkeypatch):
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())


def setup(callback=None):
    runtime, prepared, handler, request = case(callback)
    request = replace(request, context_anchor=SimpleNamespace(input_message_id='source-input-1'))
    prepared.content_visible_tools = {'web_search': WEB}

    async def dispatch(calls, *_args, **_kwargs):
        return [(entry, '查询结果', False, '查询结果') for entry in calls]

    handler._execute_tool_calls.side_effect = dispatch
    return runtime, prepared, handler, request


def error_code(messages, call_id):
    result = next(item for item in messages if item.get('tool_call_id') == call_id)
    return parse_json_tool_content(result['content'])['code']


@pytest.mark.asyncio
@pytest.mark.parametrize('direct', [True, False])
async def test_changed_arguments_and_successful_pages_do_not_reset_corrections(direct):
    runtime, prepared, handler, request = setup()
    if not direct:
        runtime.task_bootstrap = None
    first = call('web_search', {'keyword': '花'}, 'bad-1')
    page1 = call('web_search', {'query': '花', 'page': 1}, 'page-1')
    await execute(runtime, prepared, handler, request, [first, page1])
    assert error_code(prepared.messages, 'bad-1') == repairs.INVALID
    assert not repairs.stopped(runtime, request)
    assert handler._execute_tool_calls.await_args.args[0] == [page1]

    page2 = call('web_search', {'query': '花', 'page': 2}, 'page-2')
    second = call('web_search', {'query': ['花'], 'page': 2}, 'bad-2')
    duplicate = call('web_search', {'query': 1}, 'bad-2-copy')
    await execute(runtime, prepared, handler, request, [second, page2, duplicate])
    assert error_code(prepared.messages, 'bad-2') == repairs.INVALID
    assert error_code(prepared.messages, 'bad-2-copy') == repairs.INVALID
    assert handler._execute_tool_calls.await_args.args[0] == [page2]
    assert list(repairs.read(runtime).counts.values()) == [2]

    third = {'id': 'bad-3', 'name': 'web_search', 'arguments': '{"query":"花","query":"草"}'}
    await execute(runtime, prepared, handler, request, [third])
    assert error_code(prepared.messages, 'bad-3') == repairs.EXHAUSTED
    assert repairs.stopped(runtime, request)
    assert handler._tool_result_stop_reason == repairs.STOP
    assert handler._execute_tool_calls.await_count == 2

    # Only an authenticated new input or a delivered steer changes the version.
    amended = replace(request, context_anchor=SimpleNamespace(input_message_id='source-input-2'))
    assert not repairs.stopped(runtime, amended)
    correction = '使用我刚上传的新素材'
    runtime.task_user_changes = UserChanges(root_task_id=runtime.task_id, items=(UserChange(
        event_id=uuid4(), owner_task_id=runtime.task_id, message=correction, delivered=True,
        message_sha256=hashlib.sha256(correction.encode()).hexdigest(),
    ),))
    assert not repairs.stopped(runtime, request)
    await execute(runtime, prepared, handler, request, [call('web_search', {'typo': True}, 'bad-new')])
    assert error_code(prepared.messages, 'bad-new') == repairs.INVALID
    assert list(repairs.read(runtime).counts.values()) == [1]


@pytest.mark.asyncio
async def test_restart_keeps_the_pre_dispatch_rejection_and_does_not_charge_it_twice():
    snapshots = []

    async def save(point, payload):
        snapshots.append((point, copy.deepcopy(payload)))
        return {'outcome': 'saved'}

    runtime, prepared, handler, request = setup(save)
    await execute(runtime, prepared, handler, request, [call('web_search', {'typo': 1}, 'first')])

    class ConnectionLost(Exception):
        pass

    handler._execute_tool_calls.side_effect = ConnectionLost
    valid = call('web_search', {'query': '实际问题'}, 'valid')
    bad = call('web_search', {'query': True}, 'second')
    with pytest.raises(ConnectionLost):
        await execute(runtime, prepared, handler, request, [bad, valid])

    boundary, checkpoint = snapshots[-1]
    assert boundary == SafePoint.BEFORE_TOOL
    assert error_code(checkpoint['messages'], 'second') == repairs.INVALID
    assert handler._execute_tool_calls.await_args.args[0] == [valid]
    assert [c['id'] for c in pending_calls(checkpoint['messages'])[0]] == ['valid']

    restored, restored_prepared, restored_handler, _ = setup(save)
    restored.task_id, restored.turn_id = runtime.task_id, runtime.turn_id
    restored.conversation_id = runtime.conversation_id
    restored.checkpoint_base = copy.deepcopy(checkpoint)
    restored_prepared.messages = copy.deepcopy(checkpoint['messages'])
    restored_prepared.content_visible_tools = {}
    await recover_pending_batch(
        prepared=restored_prepared, runtime=restored, request=request, handler=restored_handler,
        sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=copy.deepcopy(checkpoint['content_blocks']),
        cancellation_event=restored.cancellation_event, model_round=2, execute_tools=_execute_tools,
        repeated_tool_call_guard=None,
    )
    assert restored_handler._execute_tool_calls.await_args.args[0] == [valid]
    assert restored_handler._execute_tool_calls.await_args.kwargs['recovery_only'] is True
    assert not pending_calls(restored_prepared.messages)[0]
    assert error_code(restored_prepared.messages, 'second') == repairs.INVALID
    assert list(repairs.read(restored).counts.values()) == [2]

    restored_prepared.content_visible_tools = {'web_search': WEB}
    await execute(restored, restored_prepared, restored_handler, request, [
        call('web_search', {'wrong_selector': '草'}, 'third'),
    ])
    assert error_code(restored_prepared.messages, 'third') == repairs.EXHAUSTED
    assert restored_handler._execute_tool_calls.await_count == 1
    assert repairs.stopped(restored, request)


@pytest.mark.asyncio
async def test_main_model_loop_ends_after_three_distinct_invalid_attempts(monkeypatch):
    runtime, prepared, handler, request = setup()
    runtime.task_bootstrap = None
    unknown_fields = iter(('search_word', 'keyword', 'condition'))
    sampled = []

    async def sample(*_args, **kwargs):
        sampled.append(kwargs['model_round'])
        field = next(unknown_fields)
        return '', '', [call('web_search', {field: '库存'}, field)], set()

    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', sample)
    monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_tool_turn', lambda **_: [
        {'type': 'function', 'function': WEB},
    ])
    blocks = []
    await _run_loop(
        handler=handler, request=request, prepared=prepared, runtime=runtime,
        cancellation_event=asyncio.Event(), sink=CollectingExecutionSink(), totals=StreamTotals(),
        blocks=blocks,
    )
    assert sampled == [0, 1, 2]
    assert blocks[-1]['text'] == repairs.STOP
    assert error_code(prepared.messages, 'condition') == repairs.EXHAUSTED
    handler._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_distinct_declared_operation_still_gets_its_own_attempt():
    runtime, prepared, handler, request = setup()
    runtime.task_bootstrap = None
    name = 'manage_scheduled_task'
    prepared.content_visible_tools = {name: {'parameters': {
        'type': 'object',
        'required': ['action'],
        'additionalProperties': False,
        'properties': {'action': {'type': 'string', 'enum': ['create', 'list']}},
    }}}
    for number in range(2):
        await execute(runtime, prepared, handler, request, [
            call(name, {'action': 'create', f'wrong_{number}': True}, f'create-{number}'),
        ])
    await execute(runtime, prepared, handler, request, [
        call(name, {'action': 'create', 'unknown': True}, 'create-exhausted'),
        call(name, {'action': 'list', 'unknown': True}, 'list-first'),
    ])
    assert error_code(prepared.messages, 'create-exhausted') == repairs.EXHAUSTED
    assert error_code(prepared.messages, 'list-first') == repairs.INVALID
    assert not repairs.stopped(runtime, request)
    handler._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_continuation_keeps_the_original_user_input_budget():
    runtime, prepared, handler, request = setup()
    runtime.task_bootstrap = None
    await execute(runtime, prepared, handler, request, [call('web_search', {'bad': 1}, 'first')])
    continued, prepared, handler, continuation_request = setup()
    continued.task_bootstrap = None
    continued.checkpoint_base = {
        **copy.deepcopy(runtime.checkpoint_base),
        '_continuation_input': {'kind': 'continuation'},
    }
    continuation_request = replace(continuation_request, context_anchor=None)
    await execute(continued, prepared, handler, continuation_request, [
        call('web_search', {'bad': 2}, 'after-event'),
    ])
    assert error_code(prepared.messages, 'after-event') == repairs.INVALID
    assert list(repairs.read(continued).counts.values()) == [2]
    assert repairs.read(continued).input_id == 'source-input-1'


@pytest.mark.parametrize('source_code', ['CONTENT_BASE_VERSION_CHANGED', 'IMAGE_CANVAS_REFERENCE_REQUIRED', 'IMAGE_DIMENSIONS_UNAVAILABLE'])
def test_saved_source_and_schema_errors_share_one_persisted_correction_budget(source_code):
    import json

    runtime, prepared, _, request = setup()
    offer = {'generate_video': {'name': 'generate_video', 'parameters': {
        'type': 'object', 'required': ['content_source'],
        'properties': {'content_source': {'type': 'object'}},
    }}}
    schema_error = call('generate_video', {'unexpected': True}, 'invalid-schema')
    stale = call('generate_video', {'content_source': {'item_index': 2}}, 'invalid-source')
    errors = {'invalid-source': (source_code, '来源或尺寸需要核对')}
    first = repairs.Batch.prepare(runtime, request, [schema_error, stale], offer)
    combined = repairs.Batch.with_sources(runtime, request, [schema_error, stale], errors, first)
    assert list(combined.state.counts.values()) == [1]
    assert json.loads(combined.rejections['invalid-source'])['submission_state'] == 'not_dispatched'
    combined.record(runtime, prepared.messages, [])

    second = repairs.Batch.with_sources(runtime, request, [stale], errors)
    second.record(runtime, prepared.messages, [])
    third = repairs.Batch.prepare(runtime, request, [schema_error], offer)
    assert list(third.state.counts.values()) == [3]
    assert third.state.stopped


@pytest.mark.asyncio
async def test_missing_planner_count_is_rejected_before_dispatch_and_uses_existing_repair_ledger():
    from services.handlers.chat.content_tools import model_tools
    from services.tools import build_legacy_catalog
    runtime,prepared,handler,request=setup()
    spec,=model_tools([build_legacy_catalog().require('plan_ecommerce_images').to_schema()])
    prepared.content_visible_tools={'plan_ecommerce_images':spec['function']}
    missing=call('plan_ecommerce_images',{'references':[{'file_id':'source','role':'product'}]},'missing-count')
    await execute(runtime,prepared,handler,request,[missing])
    handler._execute_tool_calls.assert_not_awaited()
    assert error_code(prepared.messages,'missing-count')==repairs.INVALID
    assert list(repairs.read(runtime).counts.values())==[1]
    # The next attempt supplies the user's actual count, without rewriting input or selecting a new Skill.
    corrected=call('plan_ecommerce_images',{'references':[{'file_id':'source','role':'product'}],'image_count':3},'correct-count')
    await execute(runtime,prepared,handler,request,[corrected])
    handler._execute_tool_calls.assert_awaited_once()
    assert handler._execute_tool_calls.await_args.args[0]==[corrected]
