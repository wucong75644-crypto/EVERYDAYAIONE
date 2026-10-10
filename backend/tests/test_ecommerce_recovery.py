"""Offline recovery boundaries: real tool gate, Actor batch and transport receipts."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from services.adapters.base import StreamChunk
from services.adapters.kie.client import KieRateLimitError
from services.agent.agent_result import AgentResult
from services.agent.image.ecommerce_planner.arguments import (
    PlannerArgumentCorrection, PlannerArgumentValidationError, validate_planner_arguments,
)
from services.agent.image.ecommerce_planner.recovery import attach_receipt, model_projection
from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow, WorkflowBinding, classify_workflow
from services.tools import ToolCall, build_legacy_catalog
from tests.test_ecommerce_workflow import planner
from tests.test_tool_execution import context, stack


def arguments():
    return {'references': [{'message_id': str(uuid4()), 'content_index': '1', 'role': 'product'},
        {'asset_id': str(uuid4()), 'role': 'style_reference'}], 'num_images': '5'}


async def invalid_call(args):
    service, _, io = stack('plan_ecommerce_images')
    ctx = context(entrypoint='model', feature_flags={'ecom_image_planning_enabled': True,
        'chat_image_async_enabled': True})
    call = ToolCall('invalid', 'plan_ecommerce_images', args)
    result = await service.execute(call, ctx)
    io.assert_not_awaited()
    assert not service._consumed
    return result


async def test_argument_repair_is_pre_dispatch_and_lossless_once():
    original = arguments()
    result = await invalid_call(original)
    assert isinstance(result.exception, PlannerArgumentValidationError)
    assert result.execution.status == 'not_started' and not result.execution.handler_started
    assert not result.error.safe_to_retry  # unknown effects stay unknown; receipt is bounded advice
    assert result.exception.receipt['recovery']['action'] == 'repair_arguments'
    expected = result.exception.expected
    assert expected['image_count'] == 5 and expected['references'][0]['content_index'] == 1
    assert expected['references'][1] == original['references'][1]
    spec = build_legacy_catalog().require('plan_ecommerce_images')
    validate_planner_arguments(spec, expected)
    blocks = [{'tool_call_id': 'invalid'}]
    guard = PlannerArgumentCorrection(blocks)
    guard.observe([({'id': 'invalid', 'name': 'plan_ecommerce_images'}, result, True, '')])
    corrected = {'id': 'corrected', 'name': 'plan_ecommerce_images', 'arguments': json.dumps(expected)}
    assert guard.filter_calls([corrected]) == ([corrected], [])
    guard.reserve([corrected])
    restored = PlannerArgumentCorrection(json.loads(json.dumps(blocks)))
    restored.before_model()
    assert restored.stop_message  # restart never creates another paid correction
    guard.observe([(corrected, AgentResult('ready'), False, '')])
    image = {'name': 'generate_image', 'arguments': '{}'}
    assert guard.filter_calls([image]) == ([image], [])
    assert guard.filter_calls([corrected])[1] and guard.stop_message


@pytest.mark.parametrize('change', [
    {'image_count': 16}, {'image_count': 0}, {'style': 'guess'}, {'references': []},
    {'continue_plan_id': str(uuid4())},
])
async def test_non_equivalent_or_unknown_arguments_do_not_grant_repair(change):
    args = {'references': [{'asset_id': str(uuid4()), 'role': 'product'}], **change}
    result = await invalid_call(args)
    assert result.exception.expected is None
    assert result.exception.receipt['recovery']['action'] == 'report_error'


async def test_correction_cannot_change_reference_order_or_count():
    result = await invalid_call(arguments())
    blocks = [{'tool_call_id': 'invalid'}]
    guard = PlannerArgumentCorrection(blocks)
    guard.observe([({'id': 'invalid', 'name': 'plan_ecommerce_images'}, result, True, '')])
    expected = result.exception.expected
    changed = {**expected, 'references': list(reversed(expected['references']))}
    ready, rejected = guard.filter_calls([{'name': 'plan_ecommerce_images', 'arguments': json.dumps(changed)}])
    assert not ready and len(rejected) == 1 and guard.stop_message


@pytest.mark.parametrize('raw', [[], None, 'not an object', 5])
def test_non_object_planner_arguments_are_terminal_before_dispatch(raw):
    spec=build_legacy_catalog().require('plan_ecommerce_images')
    with pytest.raises(PlannerArgumentValidationError) as error:
        validate_planner_arguments(spec,raw)
    assert error.value.expected is None
    assert error.value.receipt['recovery']['action']=='report_error'


def test_receipts_project_only_typed_control_fields():
    result = attach_receipt(AgentResult('failed', status='error', error_message='KIE_AUTHENTICATION_FAILED',
        metadata={'plan_id': str(uuid4()), 'private_diagnostics': 'secret'}), category='authentication',
        failed_stage=3, preserved=(1, 2))
    receipt = json.loads(model_projection(result.metadata))
    assert receipt['recovery']['action'] == 'report_error'
    assert receipt['recovery']['preserved_stages'] == [1, 2]
    assert receipt['generation_allowed'] is False and 'secret' not in json.dumps(receipt)
    assert result.metadata['stop_workflow'] is True
    pending = attach_receipt(AgentResult('pending', metadata={'status': 'planning'}))
    assert pending.metadata['retry_context']['recovery']['action'] == 'wait_existing'


async def test_actual_chat_result_projects_receipt_and_checkpoint_keeps_it(monkeypatch):
    from unittest.mock import Mock
    from services.handlers.chat_tool_result_mixin import ChatToolResultMixin, ToolResultContext
    from services.tools.result_payload import encode_result
    raw=attach_receipt(AgentResult('鉴权失败，未提交图片。',status='error',error_message='KIE_AUTHENTICATION_FAILED',
        metadata={'plan_id':str(uuid4())}),failed_stage=3,preserved=(1,2),category='authentication')
    service,_,_=stack('plan_ecommerce_images',output=raw)
    call=ToolCall('failure','plan_ecommerce_images',{'references':[{'asset_id':str(uuid4()),'role':'product'}]})
    ctx=context(entrypoint='model',feature_flags={'ecom_image_planning_enabled':True,'chat_image_async_enabled':True})
    result=await service.execute(call,ctx)
    monkeypatch.setattr(ChatToolResultMixin,'_audit_tool_result',Mock())
    monkeypatch.setattr(ChatToolResultMixin,'_send_tool_result',AsyncMock())
    monkeypatch.setattr(ChatToolResultMixin,'_finish_tool_step',AsyncMock())
    output=await ChatToolResultMixin._process_unified_result(SimpleNamespace(),{'id':'failure'},result,
        ToolResultContext('task','conv','message','actor','plan_ecommerce_images','failure',1,{},0))
    projected=output[1]
    assert projected.is_failure and not projected.error.safe_to_retry
    assert json.loads(projected.model_content('chat')[-1]['text'])['recovery']['preserved_stages']==[1,2]
    payload=json.loads(json.dumps(encode_result(projected),ensure_ascii=False))
    assert 'generation_allowed' in json.dumps(payload) and 'report_error' in json.dumps(payload)
    assert projected.display['text']=='鉴权失败，未提交图片。'


@pytest.mark.parametrize('failure,partial,expected', [
    (httpx.ConnectError('offline'), False, 3),
    (httpx.ReadTimeout('unknown response'), False, 1),
    (KieRateLimitError('rejected', status_code=429), True, 1),
])
async def test_provider_retry_requires_no_response_evidence(monkeypatch, failure, partial, expected):
    service = planner()
    sessions = []
    from datetime import datetime, timedelta, timezone
    service._reserve.side_effect = [{'outcome': 'execute', 'ordinal': i, 'remaining_attempts': 3-i,
        'deadline': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()} for i in range(1, 4)]
    async def stream(*args, **kwargs):
        if partial:
            yield StreamChunk(content='partial', prompt_tokens=1)
        raise failure
        yield
    def open_chat(_):
        session = SimpleNamespace(stream_chat=stream, last_result=None, close=AsyncMock())
        sessions.append(session)
        return session
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',
        lambda: SimpleNamespace(open_chat=open_chat))
    async def no_wait(_):
        return None
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.asyncio.sleep', no_wait)
    from services.agent.image.ecommerce_planner.recovery import PlannerRecoveryError
    with pytest.raises(PlannerRecoveryError if partial else type(failure)):
        await service._call({'id': str(uuid4())}, 'lease', 3, 'rules', [])
    assert len(sessions) == service._reserve.await_count == expected
    assert all(s.close.await_count == 1 for s in sessions)
    assert service._finish.await_args.args[5] == ('rejected' if expected == 3 else 'uncertain')


async def test_cancel_records_uncertain_attempt_and_closes_session(monkeypatch):
    service = planner()
    async def stream(*args, **kwargs):
        raise asyncio.CancelledError()
        yield
    session = SimpleNamespace(stream_chat=stream, last_result=None, close=AsyncMock())
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',
        lambda: SimpleNamespace(open_chat=lambda _: session))
    with pytest.raises(asyncio.CancelledError):
        await service._call({'id': str(uuid4())}, 'lease', 1, 'rules', [])
    assert service._finish.await_args.args[5] == 'uncertain'
    session.close.assert_awaited_once()


@pytest.mark.parametrize('partial', [False, True])
async def test_dashscope_download_failure_retries_only_before_any_stream_and_preserves_inputs(monkeypatch, partial):
    from datetime import datetime, timedelta, timezone
    from services.adapters.dashscope.chat_adapter import DashScopeAPIError
    from services.agent.image.ecommerce_planner.recovery import PlannerRecoveryError
    service=planner()
    service._reserve.side_effect=[{'outcome':'execute','ordinal':i,'remaining_attempts':3-i,
        'deadline':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()} for i in (1,2)]
    failure=DashScopeAPIError.from_http(b'{"error":{"code":"InvalidURL.Timeout"},"request_id":"provider-1"}',400)
    calls=[];sessions=[]
    async def stream(messages,**kwargs):
        calls.append(messages)
        if len(calls)==1:
            if partial: yield StreamChunk(thinking_content='already started')
            raise failure
        yield StreamChunk(content='valid stage output',prompt_tokens=5,completion_tokens=3)
    def open_chat(_):
        session=SimpleNamespace(stream_chat=stream,close=AsyncMock(),last_result=SimpleNamespace(
            status='completed',usage={'prompt_tokens':5,'completion_tokens':3}))
        sessions.append(session);return session
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.get_model_gateway',lambda:SimpleNamespace(open_chat=open_chat))
    monkeypatch.setattr('services.agent.image.ecommerce_planner.service.asyncio.sleep',AsyncMock())
    raw=[{'role':'user','content':'same frozen images and user text'}]
    if partial:
        with pytest.raises(PlannerRecoveryError,match='UNCERTAIN'):
            await service._call({'id':str(uuid4())},'lease',2,'rules',raw)
        assert len(calls)==1 and service._finish.await_args.args[5]=='uncertain'
    else:
        content,usage=await service._call({'id':str(uuid4())},'lease',2,'rules',raw)
        assert content=='valid stage output' and usage['input_tokens']==5
        assert calls==[raw,raw] and service._reserve.await_count==2
        assert service._finish.await_args.args[5]=='rejected'
    diagnostics=service._finish.await_args.args[4]
    assert diagnostics['provider_error_code']=='InvalidURL.Timeout'
    assert diagnostics['provider_request_id']=='provider-1'
    assert all(s.close.await_count==1 for s in sessions)


@pytest.mark.parametrize('answer,expected', [
    ({'kind': 'main_images', 'mode': 'retry', 'reuse_through_stage': 2}, 'main_images'),
    ({'kind': 'ordinary_image', 'mode': 'new', 'reuse_through_stage': 0}, 'ordinary_image'),
    ({'kind': 'ordinary_image', 'mode': 'retry', 'reuse_through_stage': 2}, 'needs_input'),
    ({'kind': 'main_images', 'mode': 'new', 'reuse_through_stage': 2}, 'needs_input'),
    ({'kind': 'main_images', 'mode': 'retry', 'reuse_through_stage': True}, 'needs_input'),
    ({'kind': 'main_images', 'mode': 'retry', 'reuse_through_stage': 2, 'prompt': 'injected'}, 'needs_input'),
])
async def test_semantic_route_is_one_call_and_strict(monkeypatch, answer, expected):
    settings = SimpleNamespace(intent_router_enabled=True, dashscope_api_key='test-only',
        intent_router_model='test-router', intent_router_timeout=9)
    monkeypatch.setattr('core.config.get_settings', lambda: settings)
    router = SimpleNamespace(close=AsyncMock(), call_tool_model=AsyncMock(return_value={'choices': [
        {'message': {'tool_calls': [{'function': {'name': 'select_image_workflow', 'arguments': json.dumps(answer)}}]}}]}))
    monkeypatch.setattr('services.intent_router.IntentRouter', lambda: router)
    assert (await classify_workflow('用户原文不整理', {'status': 'failed'}))['kind'] == expected
    router.call_tool_model.assert_awaited_once()
    assert router.call_tool_model.await_args.kwargs['timeout'] == 5
    assert json.loads(router.call_tool_model.await_args.kwargs['text'])['raw_user_input'] == '用户原文不整理'
    router.close.assert_awaited_once()


async def test_route_unavailable_does_not_authorize_ordinary_images(monkeypatch):
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(intent_router_enabled=False))
    assert await classify_workflow('生成5张主图') == {'kind': 'needs_input'}


def test_entry_detection_uses_structured_images_and_preserves_text_without_image_payload():
    from services.agent.image.ecommerce_planner.workflow import image_context, routing_input
    parts=[{'type':'text','text':'帮我生成5张主图， 发财风格 '},
        {'type':'image','url':'data:image/png;base64,private-pixels','name':'商品图'}]
    assert image_context(parts)
    assert image_context([{'type':'file','mime_type':'image/png','name':'商品图'}])
    assert not image_context([{'type':'text','text':'image 主图 图片这些词不构成图片事实'}])
    assert routing_input(parts)==[parts[0],{'type':'image','name':'商品图'}]


async def test_ambiguous_entry_checkpoint_stops_before_any_main_model_call(monkeypatch):
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    from tests.test_chat_execution_engine import _request
    from tests.test_skill_runtime_actor import actor,prepared,handler
    model=AsyncMock()
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn',model)
    blocks=[{'type':'text','text':'请明确图片用途','ecom_entry_question':True}]
    totals=StreamTotals()
    await _run_loop(handler=handler(),request=_request(),prepared=prepared(),
        cancellation_event=asyncio.Event(),sink=CollectingExecutionSink(),totals=totals,
        blocks=json.loads(json.dumps(blocks)),runtime=actor())
    assert totals.text=='请明确图片用途'
    model.assert_not_awaited()


async def test_continuation_requires_current_planner_receipt_and_restores_completed_feedback():
    flow=object.__new__(EcommerceWorkflow)
    flow.value=WorkflowBinding(kind='main_images',mode='retry',plan_id=str(uuid4()),
        root_task_id=str(uuid4()),window_task_id=str(uuid4()),generation_run_id=str(uuid4()))
    flow.activated=AsyncMock()
    assert await flow.needs_planner([])
    assert await flow.needs_planner([{'type':'text','text':'plan_ecommerce_images 已完成'}])
    assert await flow.needs_planner([{'type':'tool_step','tool_name':'plan_ecommerce_images','status':'error'}])
    assert await flow.needs_planner([{'type':'tool_step','tool_name':'get_conversation_context','status':'completed'}])
    completed=[{'type':'tool_step','tool_name':'plan_ecommerce_images','status':'completed'}]
    assert not await flow.needs_planner(json.loads(json.dumps(completed)))


@pytest.mark.parametrize('kind,terminal,dispatched', [('main_images', False, False),
    ('needs_input', True, False), ('ordinary_image', None, True)])
async def test_first_image_batch_barrier_runs_before_any_business_io(kind, terminal, dispatched):
    from services.handlers.chat.execution_engine import _execute_tools
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from tests.test_chat_execution_engine import _request
    from tests.test_skill_runtime_actor import actor, prepared, handler
    p, runtime, h = prepared(), actor(), handler()
    flow = object.__new__(EcommerceWorkflow)
    flow.value, flow.route = None, None
    flow.runtime = SimpleNamespace(skill_runtime=None)
    flow.activated = AsyncMock()
    async def identify():
        flow.route = {'kind': kind}
        if kind in {'main_images', 'ordinary_image'}:
            flow.value = WorkflowBinding(kind=kind, root_task_id=str(uuid4()),
                window_task_id=str(uuid4()), generation_run_id=str(uuid4()))
    flow.identify = AsyncMock(side_effect=identify)
    p.ecom_workflow = flow
    calls = [{'id': 'first', 'name': 'generate_image', 'arguments': '{}'},
        {'id': 'second', 'name': 'generate_image', 'arguments': '{}'}]
    if dispatched:
        h._execute_tool_calls.return_value = [(c, AgentResult('accepted'), False, '') for c in calls]
    await _execute_tools(handler=h, request=_request(), prepared=p, turn=0, turn_text='', calls=calls,
        previewed_call_ids=set(), cancellation_event=runtime.cancellation_event,
        sink=CollectingExecutionSink(), blocks=[], runtime=runtime)
    assert bool(h._execute_tool_calls.await_count) is dispatched
    flow.identify.assert_awaited_once()
    if terminal:
        assert h._tool_result_stop_reason


@pytest.mark.parametrize('available', [True, False])
async def test_workflow_skill_restores_exact_identity_without_manual_selection(available):
    from unittest.mock import Mock
    from services.agent.image.ecommerce_planner.workflow import WorkflowSkillSource
    from services.skills.runtime import SkillBindingError
    from tests.test_skill_runtime import Source, item, state
    from tests.test_skill_resolver import context as skill_context
    original=item('ecommerce-main-images',revision='v3')
    source=Source([item('ecommerce-main-images',revision='v4')],body='Pinned workflow method')
    source.repository=SimpleNamespace(pinned_candidates=Mock(return_value=[original] if available else []))
    source._resolution_context=AsyncMock(return_value=skill_context(task_mode='smart'))
    pin={'revision':'v3','package_id':str(original.package_id)}
    runtime=state(WorkflowSkillSource(source,pin))
    if not available:
        with pytest.raises(SkillBindingError):
            await runtime.initialize()
        source.load.assert_not_awaited()
        return
    await runtime.initialize()
    assert runtime.manual_skill_id is None
    assert (await runtime.activate(json.dumps({'skill_id':'ecommerce-main-images'})))['ok']
    assert source.load.await_args.args[0].package_id==original.package_id
    assert source.load.await_args.args[0].revision=='v3'
