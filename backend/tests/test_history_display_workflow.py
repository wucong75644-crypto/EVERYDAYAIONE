"""Display intent survives lookup and a real delivery closes the readonly workflow."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow, WorkflowBinding
from services.handlers.chat import content_tools, existing_image_delivery as display
from services.task_execution import request_resolution as rr
from services.task_execution.image_reference_catalog import for_runtime
from services.adapters.types import StreamChunk, ToolCallDelta
from tests.test_existing_image_delivery import scenario
from tests.test_request_resolution import setup_case
from tests.test_content_objects import run_content_loop
from tests.test_model_directed_loop import execute
from tests.test_task_execution_model_bridge import call


@pytest.mark.asyncio
@pytest.mark.parametrize('target_kind', ['ambiguous', 'missing_image'])
@pytest.mark.parametrize('intent,action,expected', [
    ('explain', 'display_existing', 'explain'), ('explain', 'analyze', 'explain'),
    ('edit', None, 'other'), ('generate', None, 'other')])
async def test_identify_keeps_valid_readonly_intent_and_execution_guards(intent, action, expected, target_kind):
    r, _, h, q, _ = setup_case()
    w = object.__new__(EcommerceWorkflow)
    w.runtime, w.handler, w.request, w.previous = r, h, q, None
    w.allow_server_planning = False
    w.save = AsyncMock()
    w.route = {'kind': 'unrelated' if intent == 'explain' else 'ordinary_image', 'mode': 'new',
        'reuse_through_stage': 0, 'intent': intent, 'image_count': None,
        'request_context': {'target': {'kind': 'ambiguous', 'basis': 'ambiguous'},
            'focus': 'selected_target', 'image_action': action}}
    if target_kind == 'missing_image':
        w.route['request_context']['target'] = {'kind': 'image', 'basis': 'explicit_reference',
            'source_message_id': str(uuid4()), 'content_index': 0}
    await w.identify()
    assert w.value.intent == expected
    assert rr.binding(r)['status'] == 'ambiguous'
    r.workflow_projection = w.value.model_dump(mode='json')
    assert display.action(r, q) == action
    assert rr.check(r, q, call('generate_image', {'content_source': {'artifact_id': 'old', 'item_index': 1}}))[0] == 'REQUEST_TARGET_UNRESOLVED'


def test_schema_and_examples_follow_selection_and_analysis_stage(monkeypatch):
    r, _, _, q, ref, _ = scenario(monkeypatch)
    refs = for_runtime(r)
    read = copy.deepcopy(content_tools.READ_SCHEMA)
    original = copy.deepcopy(read)
    rr.freeze(r, q, {'kind': 'ambiguous', 'basis': 'ambiguous'})
    offered = display.offer(r, q, [read])[0]['function']
    assert 'show_image' not in offered['parameters']['properties']
    assert 'view_image' in offered['parameters']['properties']
    assert read == original
    wire = refs.schema({'type': 'function', 'function': offered})['function']
    assert '当前可用核图参数示例' in wire['description']
    assert '"show_image":' not in wire['description']
    schema = refs.schema(rr.selection_schema(r))['function']
    readonly = [e for e in schema['parameters']['examples'] if e['intent'] == 'explain']
    assert {e['request_context']['image_action'] for e in readonly} == {'display_existing', 'analyze'}
    for example in readonly:
        Draft202012Validator(schema['parameters']).validate(example)
        assert json.dumps(readonly, ensure_ascii=False) in schema['description']
    rr.freeze(r, q, {'kind': 'image', 'basis': 'explicit_reference', 'source_message_id': ref['message_id'], 'content_index': ref['content_index']})
    assert 'show_image' in display.offer(r, q, [read])[0]['function']['parameters']['properties']
    r.workflow_projection['request_context']['image_action'] = 'analyze'
    assert 'show_image' not in display.offer(r, q, [read])[0]['function']['parameters']['properties']


@pytest.mark.asyncio
async def test_skipping_selection_returns_exact_model_chosen_reference_without_display(monkeypatch):
    r, p, h, q, ref, _ = scenario(monkeypatch)
    rr.freeze(r, q, {'kind': 'ambiguous', 'basis': 'ambiguous'})
    p.content_controls_available = True
    blocks = await execute(r, p, h, q, [call(content_tools.READ, {'show_image': ref})])
    result = json.loads(p.messages[-1]['content'])
    assert result['code'] == 'REQUEST_TARGET_SELECTION_REQUIRED'
    assert result['recovery']['tool'] == rr.SELECT
    expected = for_runtime(r).encode(display.selection_arguments(ref))
    assert result['recovery']['arguments'] == expected
    assert not any(b.get('type') == 'image' for b in blocks)
    h._execute_tool_calls.assert_not_awaited()


def workflow(r, p, q):
    frame = {'target': {'kind': 'ambiguous', 'basis': 'ambiguous'}, 'focus': 'selected_target', 'image_action': 'display_existing'}
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
    w = SimpleNamespace(value=WorkflowBinding(kind='ordinary_image', root_task_id=r.task_id,
        window_task_id=r.task_id, generation_run_id=str(uuid4()), intent='explain', request_context=ImageRequestContext(**frame)),
        needs_planner=AsyncMock(return_value=False), context_message=lambda: None)
    async def save():
        r.workflow_projection = w.value.model_dump(mode='json')
    w.save = save
    p.ecom_workflow = w
    return w


@pytest.mark.asyncio
async def test_plain_promise_is_not_published_and_loop_continues_to_actual_image(monkeypatch):
    r, p, h, q, ref, image = scenario(monkeypatch)
    rr.freeze(r, q, {'kind': 'ambiguous', 'basis': 'ambiguous'})
    workflow(r, p, q)
    rounds = []
    async def stream(**kwargs):
        rounds.append(kwargs)
        if len(rounds) == 1:
            yield StreamChunk(content='我核实后再发给你。')
        else:
            args = for_runtime(r).encode(display.selection_arguments(ref))
            yield StreamChunk(tool_calls=[ToolCallDelta(index=0, id='select-image', name=rr.SELECT, arguments_delta=json.dumps(args))])
    _, blocks, totals = await run_content_loop(r, p, h, q, stream)
    assert len(rounds) == 2
    assert display.complete(r, q, blocks)
    assert [b for b in blocks if b.get('type') == 'image'] == [image]
    assert '核实后再发' not in totals.text and display.MISSING not in totals.text
    assert any(m.get('role') == 'assistant' and m.get('content') == '我核实后再发给你。' for m in p.messages)
    assert r.checkpoint_base['_existing_image_recovery_v1']['attempts'] == 1
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_prose_stops_with_truthful_missing_status_and_bounded_retries(monkeypatch):
    r, p, h, q, _, _ = scenario(monkeypatch)
    rr.freeze(r, q, {'kind': 'ambiguous', 'basis': 'ambiguous'})
    count = []
    async def stream(**kwargs):
        count.append(1)
        yield StreamChunk(content='已经给你发了。')
    _, blocks, totals = await run_content_loop(r, p, h, q, stream)
    assert len(count) == 3
    assert display.MISSING in totals.text and '已经给你发了' not in totals.text
    assert not any(b.get('type') == 'image' for b in blocks)
    assert not display.can_continue(r, q, p)
    r.checkpoint_base = json.loads(json.dumps(r.checkpoint_base))
    assert not display.can_continue(r, q, p)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_clarification_ends_without_automatic_selection_or_image(monkeypatch):
    r, p, h, q, _, _ = scenario(monkeypatch)
    rr.freeze(r, q, {'kind': 'ambiguous', 'basis': 'ambiguous'})
    p.content_controls_available = True
    blocks = await execute(r, p, h, q, [call(rr.RESPOND, {
        'purpose': 'clarification', 'text': '有两张灯塔图，你要原版还是换过桌布的那张？'})])
    assert h._request_response_stop is True
    assert '有两张灯塔图' in h._tool_result_stop_reason
    assert '尚未展示图片' in h._tool_result_stop_reason
    assert rr.binding(r)['status'] == 'ambiguous'
    assert not any(b.get('type') == 'image' for b in blocks)


@pytest.mark.asyncio
async def test_unavailable_file_does_not_change_target_or_create_an_image_task(monkeypatch):
    r, p, h, q, ref, _ = scenario(monkeypatch)
    def missing(*args):
        raise ValueError('CHAT_IMAGE_FILE_UNAVAILABLE')
    monkeypatch.setattr(content_tools, 'show_image', missing)
    async def unexpected(**kwargs):
        raise AssertionError('Resource failure must not restart generation')
        yield StreamChunk(content='')
    _, blocks, totals = await run_content_loop(r, p, h, q, unexpected)
    assert display.FAILED in totals.text
    assert not any(b.get('type') == 'image' for b in blocks)
    assert display.references(r, q) == [ref]
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_stop_reports_undelivered_status_without_model_success_claim(monkeypatch):
    from services.handlers.chat.execution_engine import _apply_budget_stop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    r, p, h, q, _, _ = scenario(monkeypatch)
    rr.freeze(r, q, {'kind': 'ambiguous', 'basis': 'ambiguous'})
    p.budget = SimpleNamespace(stop_reason='max_turns')
    synthesis = AsyncMock(side_effect=AssertionError('Undelivered image is a native outcome'))
    monkeypatch.setattr('services.agent.stop_policy.synthesize_wrap_up', synthesis)
    blocks, totals = [], StreamTotals()
    await _apply_budget_stop(p, totals, blocks, CollectingExecutionSink(), runtime=r, request=q)
    assert '旧图展示尚未完成' in totals.text
    assert not any(b.get('type') == 'image' for b in blocks)
    synthesis.assert_not_awaited()
