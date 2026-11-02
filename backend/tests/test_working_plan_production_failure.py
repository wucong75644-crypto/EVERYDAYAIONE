"""Replay the 2026-11-02 browser failure without provider IO or business effects."""
import copy
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
import httpx

from core.config import get_settings
from schemas.message import TextPart
from services.adapters.dashscope.responses import response_tools, stable_tools
from services.handlers.chat.execution_engine import (
    _RepeatedToolCallGuard, _build_replay_context, _read_turn, execute_chat, StreamTotals,
)
from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.model_gateway import ModelCallRequest, ModelGateway
from services.task_execution.contracts import WorkingPlan
from services.task_execution.direct_loop import plan_guard_calls
from services.task_execution.model_directed import PLAN_SCHEMA
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_bootstrap import bootstrap
from tests.test_task_execution_model_bridge import call


NAMES = ['梳理需求要点', '编写文字方案', '检查方案完整性']
# The eight actual argument variants in the saved production assistant calls.
FAILED_ARGUMENTS = [
    {'steps': [{'title': n, 'status': 'pending'} for n in NAMES], 'explanation': '仅登记计划。'},
    {'steps': [{'name': n, 'status': 'pending'} for n in NAMES], 'explanation': '仅登记计划。'},
    {'steps': [{'name': n, 'status': 'todo'} for n in NAMES]},
    {'steps': [{'title': n} for n in NAMES]},
    {'steps': [{'title': n, 'name': n, 'status': 'pending'} for n in NAMES]},
    {'steps': [{'title': n, 'status': 'not_started'} for n in NAMES]},
    {'steps': [{'title': NAMES[0], 'status': 'pending'}]},
    {'steps': [{'name': n, 'state': 'pending'} for n in NAMES]},
]


def test_provider_tool_conversions_keep_the_complete_work_item_contract():
    parameters = PLAN_SCHEMA['function']['parameters']
    assert parameters['properties']['steps']['items'] == {'$ref': '#/$defs/WorkItem'}
    assert parameters['$defs']['WorkItem']['required'] == ['step', 'status']
    assert 'step' in PLAN_SCHEMA['function']['description']
    assert stable_tools([PLAN_SCHEMA])[0]['function']['parameters'] == parameters
    assert response_tools([PLAN_SCHEMA], ())[0]['parameters'] == parameters


@pytest.mark.asyncio
async def test_http_boundary_sends_full_schema_and_preserves_fragmented_provider_arguments():
    """Real stream reader and adapter, mock HTTP only; no claim about model behavior."""
    r, p, _, req = case()
    p.stream_kwargs = {}
    requests = []
    arguments = json.dumps(FAILED_ARGUMENTS[0], ensure_ascii=False)

    async def respond(request):
        assert request.url.host == 'schema-test.invalid'
        assert request.url.path == '/v1/chat/completions'
        body = json.loads(request.content)
        requests.append(body)
        names = [tool['function']['name'] for tool in body['tools']]
        assert names.count('update_task_plan') == 1
        schema = next(t['function']['parameters'] for t in body['tools']
                      if t['function']['name'] == 'update_task_plan')
        assert schema == PLAN_SCHEMA['function']['parameters']
        assert schema['$defs']['WorkItem']['required'] == ['step', 'status']
        assert body['model'] == 'qwen3.8-max'
        assert 'response_format' not in body  # A function schema is not structured body output.
        frames = [
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'provider-plan',
                'type': 'function', 'function': {'name': 'update_task_plan', 'arguments': arguments[:20]}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0,
                'function': {'arguments': arguments[20:]}}]}, 'finish_reason': 'tool_calls'}]},
        ]
        content = ''.join('data: '+json.dumps(frame)+'\n\n' for frame in frames)+'data: [DONE]\n\n'
        return httpx.Response(200, text=content, headers={'content-type': 'text/event-stream'})

    adapter = DashScopeChatAdapter(api_key='local-not-a-secret', model='qwen3.8-max',
        base_url='https://schema-test.invalid/v1')
    async with httpx.AsyncClient(base_url=adapter._base_url, transport=httpx.MockTransport(respond)) as client:
        adapter._client = client
        p.model_gateway = ModelGateway(adapter_factory=lambda *args, **kwargs: adapter).open_chat(
            ModelCallRequest(model_id='qwen3.8-max', task_id=r.task_id))
        _, _, calls, _ = await _read_turn(p, [copy.deepcopy(PLAN_SCHEMA)], r.cancellation_event,
            CollectingExecutionSink(), StreamTotals(), [], None, request=req)
        await p.model_gateway.close()
    assert len(requests) == 1
    assert len(calls) == 1 and calls[0]['name'] == 'update_task_plan'
    assert calls[0]['arguments'] == arguments


@pytest.mark.asyncio
@pytest.mark.parametrize('arguments', FAILED_ARGUMENTS + [None, [], 'invalid json'])
async def test_failed_plan_preserves_context_and_returns_actionable_contract(arguments):
    r, p, h, req = case()
    history = copy.deepcopy(p.messages)
    bad = call('update_task_plan', arguments, 'bad-plan')
    # Exercise the raw protocol, including null and malformed JSON.
    bad['arguments'] = arguments if arguments == 'invalid json' else json.dumps(arguments)
    blocks = await execute(r, p, h, req, [bad])
    result = json.loads(blocks[0]['output'])
    assert result['code'] == 'TASK_WORKING_PLAN_INVALID' and result['state_unchanged']
    assert result['parameters'] == PLAN_SCHEMA['function']['parameters']
    assert result['errors'] and all('input' not in e and 'ctx' not in e for e in result['errors'])
    assert p.messages[:len(history)] == history
    assert r.task_direct_plan is None and r.task_execution_state is None
    h._execute_tool_calls.assert_not_awaited()


def test_variant_guard_restores_existing_checkpoint_and_keeps_valid_and_native_updates():
    guard = _RepeatedToolCallGuard()
    assert guard.observe(plan_guard_calls([call(arguments=FAILED_ARGUMENTS[0])])) == 'continue'
    assert guard.observe(plan_guard_calls([call(arguments=FAILED_ARGUMENTS[1])])) == 'nudge'
    restored = _RepeatedToolCallGuard.from_replay_context(
        _build_replay_context([], [], 1, repeated_tool_call_guard=guard))
    assert restored.observe(plan_guard_calls([call(arguments=FAILED_ARGUMENTS[2])])) == 'stop'
    valid = [call(arguments={'steps': [{'step': n, 'status': 'pending'} for n in NAMES]})]
    assert plan_guard_calls(valid) == valid
    assert restored.observe(plan_guard_calls(valid)) == 'continue'
    mixed = [call(arguments=FAILED_ARGUMENTS[0]), call('code_execute', {'code': 'pass'})]
    assert plan_guard_calls(mixed) is mixed


@pytest.mark.asyncio
@pytest.mark.parametrize('corrected', [False, True])
async def test_real_actor_loop_corrects_or_stops_invalid_variants_without_business_calls(monkeypatch, corrected):
    settings = get_settings().model_copy(update={'skill_catalog_enabled': False,
        'skill_runtime_enabled': False, 'skill_recommendations_enabled': False,
        'ecom_image_planning_enabled': False})
    monkeypatch.setattr('core.config.get_settings', lambda: settings)
    b, _, anchor, _ = bootstrap()
    b = replace(b, model_directed=True, require_coverage_review=False)
    r, p, h, req = case()
    r.task_bootstrap = b
    r.task_id, r.conversation_id = str(b.task_id), str(b.conversation_id)
    p.execution_context = replace(p.execution_context, actor_user_id=str(b.actor_user_id),
        workspace_owner_id=str(b.actor_user_id), task_id=r.task_id, conversation_id=r.conversation_id)
    history = copy.deepcopy(p.messages)
    monkeypatch.setattr('services.handlers.chat.execution_engine.prepare_chat_stream', AsyncMock(return_value=p))
    req = replace(req, task_id=r.task_id, conversation_id=r.conversation_id, user_id=str(b.actor_user_id),
        content=[TextPart(text='只登记三项待办，不执行。')], context_anchor=anchor)
    h.org_id, h.db = None, object()
    rounds = []

    async def model(current, tools, *args, **kwargs):
        assert [m for m in current.messages if m in history] == history
        assert sum(t['function']['name']=='update_task_plan' for t in tools) == 1
        assert next(t['function']['parameters'] for t in tools
                    if t['function']['name']=='update_task_plan') == PLAN_SCHEMA['function']['parameters']
        rounds.append(kwargs['model_round'])
        if corrected and len(rounds) == 2:
            feedback = json.loads(next(m['content'] for m in reversed(current.messages) if m['role']=='tool'))
            assert any(e['loc'] == ['steps', 0, 'step'] for e in feedback['errors'])
            assert feedback['parameters']['$defs']['WorkItem']['required'] == ['step', 'status']
            arguments = {'steps': [{'step': n, 'status': 'pending'} for n in NAMES]}
        elif corrected and len(rounds) == 3:
            return '已登记三项待办，未执行。', '', [], set()
        else:
            arguments = FAILED_ARGUMENTS[len(rounds)-1]
        return '', '', [call(arguments=arguments, key=f'plan-{len(rounds)}')], set()

    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', model)
    await execute_chat(handler=h, request=req, runtime=r)
    assert len(rounds) == 3  # Existing guard stops the third invalid variant, no extra summarizer call.
    assert r.task_execution_state is None
    assert r.task_requirement_review is None and r.task_plan_reviews is None
    h._execute_tool_calls.assert_not_awaited()
    if corrected:
        assert r.task_direct_plan == WorkingPlan.model_validate(
            {'steps': [{'step': n, 'status': 'pending'} for n in NAMES]})
    else:
        assert r.task_direct_plan is None
