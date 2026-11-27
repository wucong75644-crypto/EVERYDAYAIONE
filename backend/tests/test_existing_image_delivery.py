"""Existing images require a current-input block and receipt, not a success claim."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator

from services.handlers.chat import content_tools, existing_image_delivery as display
from services.handlers.chat.execution_engine import _run_loop
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat.stream_session import StreamTotals
from services.task_execution import request_resolution as rr
from services.task_execution.content_delivery import input_revision
from services.task_execution.image_reference_catalog import for_runtime
from schemas.message import serialize_content_parts
from schemas.media_parts import ImagePart
from tests.test_request_catalog import catalog_case


def scenario(monkeypatch, action='display_existing'):
    r, p, h, q, rows, _ = catalog_case(monkeypatch)
    r.skill_coordination = True
    rr.context(r)['image_reference_version'] = 1
    ref = rows[1]['images'][0]['reference']
    rr.freeze(r, q, {'kind': 'image', 'basis': 'explicit_reference',
                     'source_message_id': ref['message_id'], 'content_index': ref['content_index']})
    r.workflow_projection = {'intent': 'explain', 'request_context': {'image_action': action}}
    h.org_id = None
    h.db = object()
    image = {'type': 'image', 'url': 'https://test.invalid/original.png', 'image_presentation': 'existing_image',
             'width': 1024, 'height': 1024, 'source_message_id': ref['message_id'],
             'source_content_index': ref['content_index']}
    monkeypatch.setattr(content_tools, 'show_image', lambda *args: copy.deepcopy(image))
    return r, p, h, q, ref, image


@pytest.mark.asyncio
async def test_real_loop_delivers_without_sampling_a_success_claim_and_recovers_once(monkeypatch):
    r, p, h, q, ref, _ = scenario(monkeypatch)
    model = AsyncMock(side_effect=AssertionError('Display before sampling narrative'))
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', model)
    blocks = []
    for _ in range(2):
        await _run_loop(handler=h, request=q, prepared=p, runtime=r, cancellation_event=r.cancellation_event,
                        sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=blocks)
        assert display.complete(r, q, blocks)
        assert len([b for b in blocks if b.get('type') == 'image']) == 1
    assert ref in r.checkpoint_base['_displayed_image_delivery_v1']['images']
    model.assert_not_awaited()
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_source_is_not_reported_as_sent(monkeypatch):
    r, p, h, q, _, _ = scenario(monkeypatch)
    def failed(*args):
        raise ValueError('CHAT_IMAGE_VIEW_UNAVAILABLE')
    monkeypatch.setattr(content_tools, 'show_image', failed)
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', AsyncMock(side_effect=AssertionError('No success fabrication')))
    blocks = []
    await _run_loop(handler=h, request=q, prepared=p, runtime=r, cancellation_event=r.cancellation_event,
                    sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=blocks)
    assert not display.complete(r, q, blocks)
    assert not any(b.get('type') == 'image' for b in blocks)
    assert blocks[-1]['text'] == display.FAILED
    assert 'CHAT_IMAGE_VIEW_UNAVAILABLE' in json.dumps(p.messages)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.parametrize('variation', ['receipt_only', 'block_only', 'old_input', 'other_task', 'wrong_image'])
def test_partial_or_historical_evidence_does_not_satisfy_current_delivery(monkeypatch, variation):
    r, _, _, q, ref, image = scenario(monkeypatch)
    r.checkpoint_base['_displayed_image_delivery_v1'] = {'owner_task_id': str(r.task_id),
        'input_revision': input_revision(r, q), 'images': [ref]}
    blocks = [image]
    if variation == 'receipt_only': blocks = []
    elif variation == 'block_only': r.checkpoint_base.pop('_displayed_image_delivery_v1')
    elif variation == 'old_input': q = replace(q, context_anchor=SimpleNamespace(input_message_id='new', base_revision=9))
    elif variation == 'other_task': r.checkpoint_base['_displayed_image_delivery_v1']['owner_task_id'] = 'old-task'
    else: image['source_content_index'] += 1
    assert not display.complete(r, q, blocks)


@pytest.mark.parametrize('action', ['analyze', None])
async def test_analysis_and_legacy_frames_do_not_automatically_display(monkeypatch, action):
    r, p, _, q, _, _ = scenario(monkeypatch, action)
    execute, finish = AsyncMock(), AsyncMock()
    assert not await display.deliver(r, q, p, [], execute=execute, finish=finish)
    execute.assert_not_awaited()


def test_analysis_rejects_show_but_legacy_frames_keep_the_original_tool(monkeypatch):
    from services.task_execution.contracts import TaskError
    r, _, _, q, ref, _ = scenario(monkeypatch, 'analyze')
    with pytest.raises(TaskError, match='REQUEST_TARGET_EXISTING_RESULT_NOT_REQUESTED'):
        rr.check_display(r, q, ref)
    r.workflow_projection['request_context'].pop('image_action')
    rr.check_display(r, q, ref)


def test_model_facing_example_and_repair_schema_agree(monkeypatch):
    r, _, _, _, _, _ = scenario(monkeypatch)
    refs = for_runtime(r)
    tool = refs.schema(content_tools.READ_SCHEMA)['function']
    example = json.loads(tool['description'].split('不默认选示例）：')[1].split('。view_image')[0])
    Draft202012Validator(tool['parameters']).validate(example)
    assert 'message_id' not in json.dumps(example)
    schema = refs.schema(rr.selection_schema(r))['function']['parameters']
    field = schema['properties']['request_context']['properties']['image_action']
    assert 'display_existing' in json.dumps(field) and 'analyze' in json.dumps(field)


def test_presentation_and_source_survive_message_serialization(monkeypatch):
    _, _, _, _, _, image = scenario(monkeypatch)
    actual = serialize_content_parts([ImagePart.model_validate(image)])[0]
    assert actual['image_presentation'] == 'existing_image'
    assert actual['source_message_id'] == image['source_message_id']
    assert actual['source_content_index'] == image['source_content_index']


async def test_actual_model_text_cannot_claim_display_when_target_is_unresolved(monkeypatch):
    from services.adapters.types import StreamChunk
    from tests.test_content_objects import run_content_loop
    r, p, h, q, _, _ = scenario(monkeypatch)
    r.checkpoint_base.pop(rr.KEY)
    async def stream(**kwargs):
        yield StreamChunk(content='已重新发给你了。没有重新生成。')
    sink, blocks, totals = await run_content_loop(r, p, h, q, stream)
    assert not any(b.get('type') == 'image' for b in blocks)
    assert '已重新发给你了' not in totals.text
    assert display.MISSING in totals.text
    assert not display.complete(r, q, blocks)


def test_history_distinguishes_a_displayed_original_from_a_generated_chart(monkeypatch):
    from services.handlers.chat_context.history_outcomes import project_completed_assistant
    _, _, _, _, _, image = scenario(monkeypatch)
    history = project_completed_assistant([image])
    assert '已展示已有图片' in history and '已生成图表' not in history


async def test_router_wire_requires_and_explains_the_display_action(monkeypatch):
    from services.agent.image.ecommerce_planner.workflow import classify_workflow
    r, _, _, _, ref, _ = scenario(monkeypatch)
    output = {'kind': 'ordinary_image', 'mode': 'new', 'reuse_through_stage': 0, 'intent': 'explain',
        'image_count': None, 'request_context': {'image_action': 'display_existing', 'focus': 'selected_target',
        'target': {'kind': 'image', 'basis': 'explicit_reference',
                   'source_message_id': ref['message_id'], 'content_index': ref['content_index']}}}
    router = SimpleNamespace(close=AsyncMock(), call_tool_model=AsyncMock(return_value={
        'choices': [{'message': {'tool_calls': [{'function': {'name': 'select_image_workflow', 'arguments': json.dumps(output)}}]}}]}))
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(intent_router_enabled=True,
        dashscope_api_key='offline', intent_router_model='offline', intent_router_timeout=15))
    monkeypatch.setattr('services.intent_router.IntentRouter', lambda: router)
    selected = await classify_workflow('最早那张灯塔图再发一下，不用重新做', context={'request_resolution': rr.native_context(r)})
    assert selected['request_context']['image_action'] == 'display_existing'
    wire = router.call_tool_model.await_args.kwargs
    assert 'display_existing' in json.dumps(wire, ensure_ascii=False)
    assert 'analyze' in json.dumps(wire, ensure_ascii=False)
    context = wire['tools'][0]['function']['parameters']['properties']['request_context']
    assert 'image_action' in context['required']


async def test_checkpoint_contains_image_and_receipt_before_stream_emits_it(monkeypatch):
    r, p, h, q, _, _ = scenario(monkeypatch)
    class VerifyingSink(CollectingExecutionSink):
        async def on_block(self, block):
            if block.get('type') == 'image':
                assert display.complete(r, q, r.checkpoint_base['content_blocks'])
            await super().on_block(block)
    sink = VerifyingSink()
    await _run_loop(handler=h, request=q, prepared=p, runtime=r, cancellation_event=r.cancellation_event,
                    sink=sink, totals=StreamTotals(), blocks=[])
    assert any(b.get('type') == 'image' for b in sink.blocks)


async def test_new_input_can_request_the_same_original_again(monkeypatch):
    from uuid import uuid4
    r, p, h, q, ref, _ = scenario(monkeypatch)
    first = []
    await _run_loop(handler=h, request=q, prepared=p, runtime=r, cancellation_event=r.cancellation_event,
                    sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=first)
    previous_revision = r.checkpoint_base['_displayed_image_delivery_v1']['input_revision']
    q = replace(q, context_anchor=SimpleNamespace(input_message_id=str(uuid4()), base_revision=9))
    rr.freeze(r, q, {'kind': 'image', 'basis': 'explicit_reference',
                     'source_message_id': ref['message_id'], 'content_index': ref['content_index']})
    second = []
    await _run_loop(handler=h, request=q, prepared=p, runtime=r, cancellation_event=r.cancellation_event,
                    sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=second)
    assert display.complete(r, q, second)
    assert r.checkpoint_base['_displayed_image_delivery_v1']['input_revision'] != previous_revision
    assert sum(b.get('type') == 'image' for b in first) == sum(b.get('type') == 'image' for b in second) == 1


async def test_a_receipt_without_reply_blocks_is_recovered_instead_of_skipped(monkeypatch):
    r, p, h, q, ref, _ = scenario(monkeypatch)
    r.checkpoint_base['_displayed_image_delivery_v1'] = {'owner_task_id': str(r.task_id),
        'input_revision': input_revision(r, q), 'images': [ref]}
    blocks = []
    await _run_loop(handler=h, request=q, prepared=p, runtime=r, cancellation_event=r.cancellation_event,
                    sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=blocks)
    assert display.complete(r, q, blocks)


def test_a_multi_image_request_does_not_implicitly_pick_its_first_image(monkeypatch):
    r, _, _, q, ref, _ = scenario(monkeypatch)
    row = next(row for row in rr.context(r)['requests'] if row['message_id'] == ref['message_id'])
    row['images'].append({'name': 'second.png', 'reference': {**ref, 'content_index': 2}})
    rr.freeze(r, q, {'kind': 'request', 'basis': 'explicit_reference', 'source_message_id': ref['message_id']})
    assert display.references(r, q) == []


def test_a_corrected_content_display_does_not_adopt_a_different_saved_revision(monkeypatch):
    from services.task_execution.contracts import TaskError
    r, _, _, q, ref, _ = scenario(monkeypatch)
    group = rr.groups(r)[0]
    group['items'][0]['image_task_id'] = 'completed-image'
    rr.context(r)['image_results'] = [{'task_id': 'completed-image', 'source_message_id': ref['message_id'], 'content_index': ref['content_index']}]
    rr.freeze(r, q, {'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': group['artifact_id'],
        'revision': group['revision'], 'item_indices': [1], 'source_message_id': rr.context(r)['requests'][2]['message_id']})
    assert display.references(r, q) == [ref]
    group['revision'] += 1
    assert display.references(r, q) == []
    with pytest.raises(TaskError, match='REQUEST_TARGET_CONTENT_CHANGED'):
        rr.check_display(r, q, ref)


@pytest.mark.parametrize('mode,code', [('analyze', 'REQUEST_TARGET_EXISTING_RESULT_NOT_REQUESTED'),
                                       ('display_existing', 'REQUEST_TARGET_IMAGE_SELECTION_REQUIRED')])
async def test_display_rejection_gives_the_model_the_matching_correction(monkeypatch, mode, code):
    from tests.test_model_directed_loop import execute
    from tests.test_task_execution_model_bridge import call
    r, p, h, q, ref, _ = scenario(monkeypatch, mode)
    p.content_controls_available = True
    if mode == 'display_existing':
        row = next(row for row in rr.context(r)['requests'] if row['message_id'] == ref['message_id'])
        row['images'].append({'name': 'second.png', 'reference': {**ref, 'content_index': 2}})
        rr.freeze(r, q, {'kind': 'request', 'basis': 'explicit_reference', 'source_message_id': ref['message_id']})
    blocks = await execute(r, p, h, q, [call(content_tools.READ, {'show_image': ref})])
    result = json.loads(p.messages[-1]['content'])
    assert result['code'] == code
    assert 'resolve_request_target' in result['message'] and 'image_action' in result['message']
    assert not any(b.get('type') == 'image' for b in blocks)
