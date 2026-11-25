"""The production read barrier, revision loop and long-draft failures."""
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.handlers.chat.execution_engine import _RepeatedToolCallGuard
from services.task_execution import content_objects as co
from services.task_execution.contracts import Artifact, TaskState
from services.task_execution.direct_loop import schemas
from tests.test_model_directed_loop import case, execute
from tests.test_model_directed_records import operation_state, record_calls, Registry
from tests.test_task_execution_model_bridge import call
from services.tools import ToolCall


def test_version_refresh_and_batch_reordering_are_not_new_read_progress():
    guard = _RepeatedToolCallGuard()
    outcomes = []
    for revision in (50, 51, 52):
        batch = [call('read_task_result', {'expected_revision': revision, 'artifact_id': key}, str(revision)+key)
                 for key in ('image-first', 'image-third')]
        outcomes.append(guard.observe(batch if revision % 2 else batch[::-1]))
    assert outcomes == ['continue', 'nudge', 'stop']
    assert guard.observe([call('read_task_result', {'expected_revision': 53, 'artifact_id': 'different'})]) == 'continue'


@pytest.mark.asyncio
async def test_parallel_reads_keep_snapshot_revision_and_real_tool_boundaries():
    r, p, h, q = case()
    await execute(r, p, h, q, [call(co.SAVE, {'document': {'title': '稿件', 'items': [
        {'title': '第一套', 'body': '第一套完整原文'}, {'title': '第三套', 'body': '第三套完整原文'}]}}, 'save')])
    state = r.task_execution_state
    r.task_artifact_reader = SimpleNamespace(native_result=AsyncMock(return_value={'results': []}))
    from services.task_execution.model_bridge import controller_for
    description = next(s['function']['description'] for s in schemas(r, controller_for(r))
                       if s['function']['name'] == 'read_task_result')
    assert 'native.read.result' in description and 'image' in description
    await execute(r, p, h, q, [call('read_task_text', {'expected_revision': state.revision,
        'artifact_id': state.artifacts[0].id}, 'text'),
        call('read_task_result', {'expected_revision': state.revision}, 'result')])
    outputs = [json.loads(m['content']) for m in p.messages if m.get('tool_call_id') in {'text', 'result'}]
    assert len(outputs) == 2 and all(o['ok'] for o in outputs)
    assert r.task_execution_state == state
    assert r.task_artifact_reader.native_result.await_count == 1
    h._execute_tool_calls.assert_not_awaited()
    # A stale fence must still be rejected, even though it is ignored by loop detection.
    await execute(r, p, h, q, [call('read_task_result', {'expected_revision': state.revision-1}, 'stale')])
    assert json.loads(p.messages[-1]['content'])['code'] == 'TASK_REVISION_STALE'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'changed', 'denied'])
async def test_picture_result_reads_use_scoped_native_images_without_dispatch(fault):
    r, p, h, q = case()
    b, state = operation_state('completed')
    r.task_id = str(b.task_id)
    r.task_bootstrap = b
    image_task = str(uuid4())
    artifact = Artifact(id='image-first', kind='image', reference='task-image:'+image_task,
        sha256='a'*64, source_task_id=image_task, delivery_task_id=b.task_id, step_id=state.steps[0].id, source_call_id='first')
    state = TaskState.model_validate({**state.model_dump(), 'artifacts': [artifact]})
    r.task_execution_state = state
    part = {'type': 'image', 'url': 'https://example.invalid/published.png'}
    rows = [{'task_id': image_task, 'part_sha256': 'b'*64 if fault == 'changed' else 'a'*64,
             'message_id': str(uuid4()), 'content_index': 0, 'part': part}]
    r.task_artifact_reader = SimpleNamespace(reusable_images=AsyncMock(return_value=rows),
        native_result=AsyncMock(side_effect=AssertionError('images are not native.read.result')))
    r.task_image_resource_resolver = AsyncMock(side_effect=PermissionError('denied') if fault == 'denied' else None)
    blocks = await execute(r, p, h, q, [call('read_task_result', {
        'expected_revision': state.revision, 'artifact_id': artifact.id}, 'read-image')])
    result = json.loads(p.messages[-1]['content'])
    if fault:
        assert not result['ok']
        assert not any(x.get('type') == 'image' for x in blocks)
    else:
        assert result['ok'] and result['image'] == part
        assert part in blocks
    assert r.task_execution_state == state
    h._execute_tool_calls.assert_not_awaited()
    r.task_artifact_reader.native_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_three_chinese_drafts_over_50kb_save_render_and_keep_numbered_identity():
    r, p, h, q = case()
    bodies = [('甲乙丙丁戊己庚辛壬癸'*600) + str(i) for i in range(3)]
    draft = {'title': '三套完整讨论稿', 'items': [
        {'title': str(i+1), 'body': body} for i, body in enumerate(bodies)]}
    assert len(json.dumps(draft, ensure_ascii=False).encode()) > 50000
    blocks = await execute(r, p, h, q, [call(co.SAVE, {'document': draft}, 'long-save')])
    row, = co.catalog(r.task_execution_state)
    for index in (1, 3):
        _, _, item = co.load(r.task_execution_state, {'artifact_id': row['artifact_id'], 'item_index': index})
        assert item.body == bodies[index-1]
    assert all(body in blocks[-1]['text'] for body in bodies)
    restored = TaskState.model_validate(r.task_execution_state.model_dump(mode='json'))
    assert co.catalog(restored) == co.catalog(r.task_execution_state)
    assert restored.operations == ()
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_more_text_saves_do_not_authorize_generation_or_end_the_first_draft():
    r, p, h, q = case()
    first = await execute(r, p, h, q, [call(co.SAVE, {'document': {'title': '第一套',
        'items': [{'title': '第一套', 'body': '完整正文一'}]}, 'delivery_complete': False}, 'first')])
    assert not first[-1]['content_delivery_final']
    assert not json.loads(next(m['content'] for m in p.messages if m.get('tool_call_id') == 'first')).get('continue_execution')
    second = await execute(r, p, h, q, [call(co.SAVE, {'document': {'title': '后两套',
        'items': [{'title': '第二套', 'body': '完整正文二'}, {'title': '第三套', 'body': '完整正文三'}]}}, 'rest')])
    assert second[-1]['content_delivery_final']
    assert sum(len(row['items']) for row in co.catalog(r.task_execution_state)) == 3
    assert not r.task_execution_state.operations
    h._execute_tool_calls.assert_not_awaited()


def test_distinct_frozen_items_allow_accepted_work_but_never_unknown_acceptance():
    b, state = operation_state('accepted')
    original = {'normalized': {'source_prompt': {'content_object': {'source': {
        'artifact_id': 'content-original', 'item_index': 1}}}}}
    text = json.dumps(original)
    recipe = Artifact(id='recipe-original', kind='text', reference='task-text:recipe-original',
        inline_text=text, sha256=hashlib.sha256(text.encode()).hexdigest(),
        source_task_id=b.task_id, delivery_task_id=b.task_id, source_call_id='first',
        step_id=state.steps[0].id, validations=('artifact.exists', 'image.direct.recipe'))
    state = state.model_copy(update={'artifacts': (recipe,)})
    args = {'prompt': '另一条真实方案', 'source_prompt': {'content_object': {'source': {
        'artifact_id': 'content-original', 'item_index': 3}}}}
    call2 = ToolCall('new-item', 'generate_image', args)
    next_state = record_calls(state, b, [call2], Registry())
    assert len(next_state.steps) == 2 and next_state.operations == state.operations
    from services.task_execution.contracts import TaskError
    uncertain = state.model_copy(update={'operations': tuple(o.model_copy(update={'status': 'uncertain'}) for o in state.operations)})
    with pytest.raises(TaskError, match='EFFECT_UNRESOLVED'):
        record_calls(uncertain, b, [call2], Registry())
    args['source_prompt']['content_object']['source']['item_index'] = 1
    with pytest.raises(TaskError, match='EFFECT_UNRESOLVED'):
        record_calls(state, b, [ToolCall('changed-same-item', 'generate_image', args)], Registry())


@pytest.mark.asyncio
async def test_model_loop_can_finish_all_three_text_saves_without_image_authorization():
    from services.adapters.types import StreamChunk, ToolCallDelta
    from tests.test_content_objects import run_content_loop
    r, p, h, q = case()
    rounds = []
    async def stream(**kwargs):
        index = len(rounds) + 1
        rounds.append(index)
        assert index <= 3
        yield StreamChunk(tool_calls=[ToolCallDelta(index=0, id=f'save-{index}', name=co.SAVE,
            arguments_delta=json.dumps({'document': {'title': f'方案{index}',
                'items': [{'title': f'方案{index}', 'body': f'第{index}套完整正文'}]},
                'delivery_complete': index == 3}, ensure_ascii=False))])
    _, blocks, _ = await run_content_loop(r, p, h, q, stream)
    assert rounds == [1, 2, 3]
    texts = [block for block in blocks if block.get('content_artifact_id')]
    assert len(texts) == 3
    assert [x['content_delivery_final'] for x in texts] == [False, False, True]
    assert sum(len(row['items']) for row in co.catalog(r.task_execution_state)) == 3
    assert not r.task_execution_state.operations
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_turn_style_is_available_to_router_and_saved_with_new_subject():
    from schemas.message import ImagePart, TextPart
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow, ImageRequestContext
    from services.task_execution.content_groups import bind_group
    r, p, h, q = case()
    first_id = str(uuid4())
    q = replace(q, context_anchor=SimpleNamespace(input_message_id=first_id),
        content=[TextPart(text='帮我写三套提示词，我先看看'), ImagePart(name='波点.png', workspace_path='style.png')])
    await execute(r, p, h, q, [call(co.SAVE, {'document': {'title': '三套稿', 'items': [
        {'title': str(i), 'body': f'完整原文{i}'} for i in range(3)]}}, 'original')])
    old = co.historical_source_images(r)[0]
    next_id = str(uuid4())
    next_q = replace(q, context_anchor=SimpleNamespace(input_message_id=next_id),
        content=[TextPart(text='补这张，主体用这个，上一张只参考波点'), ImagePart(name='主体.png', workspace_path='subject.png')])
    current = co.registration_source_images(next_q, r)
    assert len(current) == 2 and current[1] == old
    assert len(co.current_source_images(next_q, r)) == 1
    # Exercise the actual workflow input builder, without constructing its DB facade.
    workflow = object.__new__(EcommerceWorkflow)
    workflow.runtime, workflow.request = r, next_q
    workflow.input = [part.model_dump(mode='json') for part in next_q.content]
    routed = workflow.action_input()
    assert any(part.get('origin') == 'history' and part.get('reference') == old.reference.model_dump(mode='json', exclude_none=True)
               for part in routed)
    subject = co.Material(reference=current[0].reference, role='手账主体', usage='input')
    style = co.Material(reference=old.reference, role='波点参考', usage='input')
    context = ImageRequestContext(materials=[subject, style])
    doc = co.ContentDocument(title='修订三套', items=tuple(co.ContentItem(title=str(i), body=f'新正文{i}', materials=(subject,)) for i in range(3)))
    bound = bind_group(doc, current, '', semantic_context=context)
    assert all(item.materials == (subject, style) for item in bound.items)
    # Shared inputs never override an explicit per-item analysis-only choice.
    analysis = style.model_copy(update={'usage': 'analysis'})
    explicit = doc.model_copy(update={'items': (doc.items[0].model_copy(update={'materials': (subject, analysis)}),)})
    assert bind_group(explicit, current, '', semantic_context=context).items[0].materials == (subject, analysis)


def test_same_result_can_be_requested_again_by_a_new_authenticated_input():
    guard = _RepeatedToolCallGuard()
    batch = [call('read_task_result', {'expected_revision': 10, 'artifact_id': 'image-first'})]
    assert guard.observe(batch, input_revision='first-user-input') == 'continue'
    assert guard.observe(batch, input_revision='first-user-input') == 'nudge'
    restored = _RepeatedToolCallGuard.from_replay_context({'repeated_tool_call_guard': guard.replay_state()})
    assert restored.observe(batch, input_revision='first-user-input') == 'stop'
    assert restored.observe(batch, input_revision='next-user-input') == 'continue'


@pytest.mark.asyncio
async def test_explicitly_unfinished_text_is_partial_when_the_model_stops():
    from services.adapters.types import StreamChunk, ToolCallDelta
    from services.task_execution.content_delivery import KEY
    from tests.test_content_objects import run_content_loop
    r, p, h, q = case()
    rounds = []
    async def stream(**kwargs):
        rounds.append(True)
        if len(rounds) == 1:
            yield StreamChunk(tool_calls=[ToolCallDelta(index=0, id='save-first', name=co.SAVE,
                arguments_delta=json.dumps({'document': {'title': '第一套',
                    'items': [{'title': '第一套', 'body': '第一套完整稿'}]}, 'delivery_complete': False}))])
        else:
            yield StreamChunk(content='先给你第一套，还有两套尚未写完。')
    _, blocks, _ = await run_content_loop(r, p, h, q, stream)
    assert r.checkpoint_base[KEY]['delivery_complete'] is False
    assert r.task_execution_state.status == 'partial'
    assert blocks[-1]['task_status'] == 'partial' and '未全部' in blocks[-1]['text']
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_total_text_budget_still_rejects_without_losing_saved_drafts():
    r, p, h, q = case()
    for i in range(4):
        await execute(r, p, h, q, [call(co.SAVE, {'document': {'title': f'文稿{i}',
            'items': [{'title': '正文', 'body': '中'*19500}]}, 'delivery_complete': False}, f'draft-{i}')])
    before = r.task_execution_state
    assert len(co.catalog(before)) == 4
    await execute(r, p, h, q, [call(co.SAVE, {'document': {'title': '超出总量',
        'items': [{'title': '正文', 'body': '中'*19500}]}}, 'over-budget')])
    assert json.loads(p.messages[-1]['content'])['ok'] is False
    assert r.task_execution_state == before
    assert len(co.catalog(r.task_execution_state)) == 4
    assert not before.operations
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_patch_nesting_is_repaired_without_rewriting_other_items_or_authorizing_images():
    r, p, h, q = case()
    initial = {'title': '三套方案', 'items': [{'title': str(i), 'body': f'原稿{i}'} for i in range(1, 4)]}
    await execute(r, p, h, q, [call(co.SAVE, {'document': initial}, 'save-original')])
    old = r.task_execution_state
    row, = co.catalog(old)
    base = {'artifact_id': row['artifact_id'], 'item_index': 1}
    await execute(r, p, h, q, [call(co.SAVE, {'base': base,
        'updates': [{'item_index': 1, 'body': '第一套修订原文'}]}, 'bad-patch')])
    error = json.loads(p.messages[-1]['content'])
    assert error['code'] == co.INVALID_OBJECT
    assert any(issue['loc'] == ['updates', 0, 'item'] for issue in error['issues'])
    assert r.task_execution_state == old
    await execute(r, p, h, q, [call(co.SAVE, {'base': base,
        'updates': [{'item_index': 1, 'item': {'body': '第一套修订原文'}}]}, 'fixed-patch')])
    updated, = co.catalog(r.task_execution_state)
    assert updated['revision'] == row['revision'] + 1 and len(updated['items']) == 3
    assert co.load(r.task_execution_state, {'artifact_id': updated['artifact_id'], 'item_index': 1})[2].body == '第一套修订原文'
    assert [co.load(r.task_execution_state, {'artifact_id': updated['artifact_id'], 'item_index': i})[2].body
            for i in (2, 3)] == ['原稿2', '原稿3']
    assert not r.task_execution_state.operations
    h._execute_tool_calls.assert_not_awaited()


def test_semantic_previous_result_binds_the_receipt_but_explicit_old_image_stays_available():
    from services.handlers.chat.content_tools import model_tools
    from services.task_execution.inline_image_action import request_type, PreviousResultEdit, SingleImageEdit
    from services.tools.catalog import build_tool_catalog
    image = build_tool_catalog().require('generate_image').to_schema()
    tool, = model_tools([image], inline_image=True, generated_image_edit=True,
        previous_result=True, single_image_edit=True)
    properties = tool['function']['parameters']['properties']
    assert set(properties) == {'previous_result_edit', 'source_message_id', 'source_content_index', 'edit_instruction'}
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        request_type(properties, {'draft': {'title': '旧底图'}}).model_validate({'draft': {'title': '旧底图'}})
    assert request_type(properties, {'previous_result_edit': '再换成浅灰色'}) is PreviousResultEdit
    explicit, = model_tools([image], inline_image=True, generated_image_edit=True, single_image_edit=True)
    assert request_type(explicit['function']['parameters']['properties'], {'edit_instruction': '修改用户指定的旧图'}) is SingleImageEdit


@pytest.mark.asyncio
async def test_historical_materials_are_context_not_new_user_attachments(monkeypatch):
    from core.config import get_settings
    from services.agent.image.ecommerce_planner.workflow import classify_workflow
    settings = get_settings()
    monkeypatch.setattr(settings, 'intent_router_enabled', True)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'offline-fixture')
    async def route(_self, **kwargs):
        payload = json.loads(kwargs['text'])
        assert payload['raw_user_input'] == [{'type': 'text', 'text': '再换成浅灰色。'}]
        context = payload['available_context']
        assert context['previous_image_result']['receipt'] == 'task:previous-blue'
        history, = context['historical_materials']
        assert history['origin'] == 'history' and history['material_index'] == 1
        assert 'reference' not in history
        args = {'kind': 'ordinary_image', 'mode': 'revise', 'reuse_through_stage': 0,
                'intent': 'edit', 'image_count': 1, 'request_context': {}}
        return {'choices': [{'message': {'tool_calls': [{'function': {
            'name': 'select_image_workflow', 'arguments': json.dumps(args)}}]}}]}
    monkeypatch.setattr('services.intent_router.IntentRouter.call_tool_model', route)
    source_context = {'image_results': [{'receipt': 'task:previous-blue', 'status': 'accepted'}]}
    result = await classify_workflow([{'type': 'text', 'text': '再换成浅灰色。'},
        {'type': 'image', 'origin': 'history', 'name': '旧底图.png',
         'reference': {'message_id': str(uuid4()), 'content_index': 1}}], context=source_context)
    assert result['intent'] == 'edit'
    assert 'historical_materials' not in source_context
