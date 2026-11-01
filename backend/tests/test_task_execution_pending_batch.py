"""Actual run-loop recovery closes original mixed batches before model IO."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.handlers.chat.execution_engine import _run_loop, _assistant_tool_message, ChatExecutionRequest
from services.handlers.chat.stream_session import StreamTotals
from services.task_execution.image_binding import bind_image_call, image_prompt
from services.task_execution.image_operations import ImageOperations
from services.task_execution.methods import ImageMethodDocument
from services.task_execution.pending_batch import pending_calls
from services.tools import ToolCall
from services.tools.runtime_context import catalog_context
from tests.test_task_execution_image_operations import case, execute
from tests.test_task_execution_methods import document
from tests.test_task_execution_model_bridge import setup_case


class ModelBoundaryReached(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('count,mixed,closed_images,existing_source', [
    (1,False,False,False), (1,True,False,False), (5,True,False,False), (5,True,True,False),
    (5,False,False,True), (5,True,True,True)])
async def test_native_accepted_pending_checkpoint_replays_before_first_model_sample(monkeypatch,count,mixed,closed_images,existing_source):
    source_runtime, coordinator, context, actor_handler, business_io, service, first = await case(monkeypatch)
    if existing_source:
        from tests.task_execution_source_fixtures import existing_source_state
        source_runtime.task_execution_state=existing_source_state(source_runtime.task_execution_state)
    briefs = ImageMethodDocument.model_validate_json(document()).image_plans
    original_calls = []
    for index in range(count):
        call = ToolCall(f'image-{index+1}', 'generate_image', {'mode': 'text_to_image',
            'prompt': image_prompt(briefs[index]), 'aspect_ratio': '3:4'})
        binding = bind_image_call(source_runtime.task_execution_state, call, coordinator.store.registry)
        current = ImageOperations(source_runtime, {'first': binding})
        await execute(service, current, actor_handler, context, call)
        original_calls.append({'id': call.call_id, 'name': call.name, 'arguments': json.dumps(dict(call.arguments),ensure_ascii=False)})
    assert business_io.await_count == count
    if mixed:
        original_calls.append({'id': 'source-read', 'name': 'web_search', 'arguments': '{"query":"资料"}'})
    accepted = source_runtime.task_execution_state
    runtime, prepared, handler, sink = setup_case(initial=accepted)
    runtime.task_operation_store = coordinator.store
    prepared.messages.append(_assistant_tool_message('', original_calls))
    if closed_images:
        for call in original_calls:
            if call['name']=='generate_image':
                prepared.messages.append({'role':'tool','tool_call_id':call['id'],
                    'content':'已保存的真实图片受理结果'})
    prepared.budget = SimpleNamespace(stop_reason=None, turns_used=0, use_turn=lambda: None)
    prepared.core_tools = []
    prepared.execution_context = catalog_context(None)
    prepared.permission = SimpleNamespace(mode=SimpleNamespace(value='auto'), need_exit_attachment=False,
        get_reminder=lambda _: '')
    prepared.tool_context = SimpleNamespace(discovered_tools=set(), update_from_batch=lambda _: None,
        build_context_prompt=lambda: None)
    handler.org_id = None
    handler._calculate_credits = lambda _: 0
    handler._get_conv_source = lambda _: 'web'
    handler._pending_emit_payloads = []
    handler._pending_change_set_blocks = []
    handler._pending_form_block = None
    sink.on_text = AsyncMock()
    sink.on_thinking = AsyncMock()
    recovered = []
    async def recover(calls,*args,task_image_operations=None,recovery_only=False,**kwargs):
        assert recovery_only is True
        values = []
        for call in calls:
            recovered.append(call['id'])
            if call['name']=='generate_image':
                native = ToolCall(call['id'],call['name'],json.loads(call['arguments']))
                lifecycle = task_image_operations.lifecycle(actor_handler,context,call['id'])
                result = await lifecycle.replay(native,context,service.policy.decide(native.name,context,native.arguments))
                assert result.execution.replayed
                values.append((call,result,False,'已受理'))
            else:
                values.append((call,'核实来源',False,'核实来源'))
        return values
    handler._execute_tool_calls = AsyncMock(side_effect=recover)
    async def read_turn(*args,**kwargs):
        assert pending_calls(prepared.messages)[0] == ()
        assert {message.get('tool_call_id') for message in prepared.messages if message['role']=='tool'} == {
            call['id'] for call in original_calls}
        raise ModelBoundaryReached()
    sample = AsyncMock(side_effect=read_turn)
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',sample)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    request = ChatExecutionRequest(content=[],user_id=str(accepted.actor_user_id),
        conversation_id=str(accepted.conversation_id),task_id=str(accepted.root_task_id),
        message_id='output',model_id='offline',context_anchor=None)
    kwargs = dict(handler=handler,request=request,prepared=prepared,cancellation_event=runtime.cancellation_event,
        sink=sink,totals=StreamTotals(),blocks=[],runtime=runtime,model_round=2)
    if count<5:
        with pytest.raises(ModelBoundaryReached):
            await _run_loop(**kwargs)
        sample.assert_awaited_once()
    else:
        await _run_loop(**kwargs)
        sample.assert_not_awaited()
        assert runtime.task_execution_state.status == 'waiting_dependencies'
    assert business_io.await_count == count
    assert len(runtime.task_execution_state.operations) == count
    assert recovered == [call['id'] for call in original_calls
        if not (closed_images and call['name']=='generate_image')]
    assert not pending_calls(prepared.messages)[0]
    payload = runtime._replay_checkpoint_callback.call_args.args[1]
    assert not pending_calls(payload['messages'])[0]
    assert len([m for m in payload['messages'] if m.get('tool_calls')]) == 1


def test_partial_closed_pairs_are_not_repeated_and_invalid_transcript_stops():
    message = _assistant_tool_message('',[{'id':'one','name':'web_search','arguments':'{}'},
        {'id':'two','name':'web_search','arguments':'{}'}])
    calls,_ = pending_calls([message,{'role':'tool','tool_call_id':'one','content':'真实原结果'}])
    assert [call['id'] for call in calls] == ['two']
    from services.task_execution.contracts import TaskError
    with pytest.raises(TaskError,match='TASK_PENDING_TRANSCRIPT_INVALID'):
        pending_calls([message,{'role':'user','content':'不能把未配对调用交模型'}])


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['missing','running','uncertain','succeeded'])
async def test_actual_tool_runtime_recovery_never_dispatches_unknown_effect_without_original_result(status):
    from unittest.mock import Mock
    from services.handlers.chat.tool_lifecycle import ActorToolLifecycle
    from services.tools import ToolResult
    from services.tools.result_payload import encode_result
    from services.tool_invocation_store import hash_tool_arguments
    from tests.tool_runtime_support import MockHandlerExecutor
    executor = MockHandlerExecutor(agent_domain='general',task_id='task1',permission_mode='auto')
    runtime = executor.tool_runtime
    arguments = {'prompt':'原始视频请求'}
    call = ToolCall('original','generate_video',arguments)
    context = runtime.context(call.call_id)
    decision = runtime.policy.decide(call.name,context,call.arguments)
    payload = encode_result(ToolResult.wrap('真实已存结果',call=call,context=context,decision=decision))
    store = SimpleNamespace(lookup=Mock(return_value=None if status=='missing' else {
        'tool_name':call.name,'args_hash':hash_tool_arguments(arguments),'status':status,'result':payload}),
        mark_stale=Mock(return_value={'outcome':'uncertain'}))
    handler = SimpleNamespace(_actor_enabled=True,_actor_invocation_store=store,
        _actor_turn_id='turn',_actor_execution_token='fence')
    result = await runtime.execute(call.name,arguments,call_id=call.call_id,
        lifecycle=ActorToolLifecycle(handler,context,recovery_only=True))
    executor.handler.assert_not_awaited()
    store.lookup.assert_called_once()
    if status=='succeeded':
        assert result.execution.replayed and result.raw=='真实已存结果'
    else:
        assert result.execution.status=='uncertain'
        assert result.exception is not None
