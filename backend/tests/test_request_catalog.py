"""Turn provenance, historical selection and one shared target contract."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.task_execution import request_resolution as rr
from services.task_execution.contracts import TaskError
from services.agent.image.ecommerce_planner.workflow import ImageRequestContext, classify_workflow
from tests.test_request_resolution import setup_case, request_target
from tests.test_task_execution_model_bridge import call


def catalog_case(monkeypatch):
    r, p, h, q, rows = setup_case()
    rr.context(r)['catalog_version'] = 2
    for number, row in zip([3, 2, 1], rows):
        row.update(turn_number=number, task_id=row['root_task_id'], task_status='completed', selected_images=[])
    rows[0]['task_status'] = 'running'
    groups = [{'artifact_id': 'old-a', 'revision': 1, 'root_task_id': rows[2]['root_task_id'],
               'creation_task_id': rows[2]['root_task_id'], 'title': '编辑本轮指定图片',
               'items': [{'item_index': 1, 'title': '旧条目'}],
               'item_inputs': [[{'reference': {'message_id': rows[2]['message_id'], 'content_index': 1}, 'usage': 'input'}]]}]
    monkeypatch.setattr(rr, 'groups', lambda _: groups)
    return r, p, h, q, rows, groups


def test_unsubmitted_b_is_visible_with_upload_beside_old_a_and_neutral_turn_label(monkeypatch):
    r, _, _, _, rows, _ = catalog_case(monkeypatch)
    ctx = rr.model_context(r)
    b = ctx['focused_request']
    assert b['message_id'] == rows[1]['message_id'] and b['turn_number'] == 2
    assert b['new_uploads'][0]['reference'] == rows[1]['images'][0]['reference']
    assert b['new_uploads'][0]['target_fields'] == {'kind': 'image', 'source_message_id': rows[1]['message_id'], 'content_index': 1}
    assert 'basis' not in b['new_uploads'][0]['target_fields']
    assert b['saved_contents'] == [] and b['generated_results'] == []
    a = ctx['saved_groups'][0]
    assert a['title'].startswith('第1轮：')
    assert a['source_request']['message_id'] == rows[2]['message_id']
    assert a['document_title'] == '编辑本轮指定图片'
    assert a['title_provenance'] == 'saved_document_title_not_current_turn'
    assert ctx['current_turn']['new_uploads'] == []  # No automatic image inheritance.
    assert ctx['current_turn']['chat_turn_status'] == 'running'
    assert 'source_sha256' not in json.dumps(ctx)


def test_new_upload_quote_and_model_selection_preserve_distinct_provenance(monkeypatch):
    r, _, _, _, rows, _ = catalog_case(monkeypatch)
    old_image = {'source_message_id': str(uuid4()), 'content_index': 0, 'source_sha256': 'b'*64,
                 'name': 'result.png', 'generated_for_message_id': rows[2]['message_id'], 'generated_for_turn': 1}
    rr.context(r)['image_results'] = [old_image]
    rows[0]['images'] = [
        {'name': 'new.png', 'reference': {'message_id': rows[0]['message_id'], 'content_index': 1}, 'client_reference': None},
        {'name': 'quoted.png', 'reference': {'message_id': rows[0]['message_id'], 'content_index': 2},
         'client_reference': {'message_id': old_image['source_message_id'], 'content_index': 0}},
    ]
    rows[0]['selected_images'] = [{'reference': {'message_id': rows[1]['message_id'], 'content_index': 1}, 'role': '风格参考', 'usage': 'input'}]
    current = rr.model_context(r)['current_turn']
    assert len(current['new_uploads']) == len(current['quoted_images']) == len(current['selected_images']) == 1
    assert current['quoted_images'][0]['original_source']['turn_number'] == 1
    assert current['quoted_images'][0]['original_source']['kind'] == 'generated_result'
    assert current['selected_images'][0]['reference_provenance'] == 'model_selection'
    assert current['selected_images'][0]['original_source']['turn_number'] == 2
    assert rows[0]['images'][1]['client_reference']['message_id'] == old_image['source_message_id']


def test_invalid_client_quote_is_not_promoted_to_a_verified_upload(monkeypatch):
    r, _, _, _, rows, _ = catalog_case(monkeypatch)
    rows[0]['images'] = [{'name': 'claim.png', 'reference': {'message_id': rows[0]['message_id'], 'content_index': 1},
                          'client_reference': {'message_id': rows[1]['message_id'], 'content_index': 99}}]
    current = rr.model_context(r)['current_turn']
    assert current['new_uploads'] == []
    assert current['quoted_images'][0]['original_source']['kind'] == 'reference_not_found_in_message'


def test_published_result_is_not_described_as_a_pending_image_from_an_old_receipt(monkeypatch):
    r, _, _, _, rows, groups = catalog_case(monkeypatch)
    groups[0]['items'][0].update(image_task_id='native-a', image_status='accepted')
    rr.context(r)['image_results'] = [{'source_message_id': str(uuid4()), 'content_index': 0,
                                     'task_id': 'native-a', 'source_sha256': 'a'*64}]
    item = rr.model_context(r)['saved_groups'][0]['items'][0]
    assert item['result_status'] == 'published' and item['operation_status'] == 'accepted'
    assert 'image_status' not in item


def test_historical_content_requires_its_true_request_and_still_accepts_explicit_selection(monkeypatch):
    r, _, _, q, rows, _ = catalog_case(monkeypatch)
    raw = {'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': 'old-a', 'revision': 1, 'item_indices': [1]}
    with pytest.raises(TaskError, match='REQUEST_TARGET_CONTENT_OWNER_MISMATCH'):
        rr.freeze(r, q, {**raw, 'source_message_id': rows[1]['message_id']})
    value = rr.freeze(r, q, {**raw, 'source_message_id': rows[2]['message_id']}, focus='selected_target')
    assert value['target']['source_message_id'] == rows[2]['message_id']
    assert rr.check(r, q, call('generate_image', {'content_source': {'artifact_id': 'old-a', 'item_index': 1}})) is None


def test_mixed_saved_recipe_and_new_input_is_not_an_unchanged_repeat(monkeypatch):
    r, _, _, q, rows, _ = catalog_case(monkeypatch)
    raw = {'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': 'old-a', 'revision': 1,
           'item_indices': [1], 'source_message_id': rows[2]['message_id']}
    frame = ImageRequestContext(target=rr.Target.model_validate(raw), focus='current_request')
    with pytest.raises(TaskError, match='REQUEST_TARGET_FOCUS_CONFLICT'):
        rr.freeze_frame(r, q, frame, 'execute_saved')
    frame = ImageRequestContext(target=rr.Target.model_validate(raw), focus='selected_target', materials=[
        {'reference': {'message_id': rows[1]['message_id'], 'content_index': 1}, 'role': '新底图', 'usage': 'input'}])
    with pytest.raises(TaskError, match='REQUEST_TARGET_SAVED_INPUTS_CHANGED'):
        rr.freeze_frame(r, q, frame, 'repeat')
    # New work may deliberately combine historical inputs. It is not disguised
    # as exact execution of the old saved recipe.
    frame = ImageRequestContext(target=rr.Target.model_validate(request_target(rows)), focus='selected_target', materials=frame.materials + [
        {'reference': {'message_id': rows[2]['message_id'], 'content_index': 1}, 'role': '历史风格', 'usage': 'input'}])
    assert rr.freeze_frame(r, q, frame, 'generate')['status'] == 'resolved'


def test_legacy_unbound_item_keeps_native_source_binding_instead_of_a_false_empty_set(monkeypatch):
    r, _, _, q, rows, groups = catalog_case(monkeypatch)
    groups[0]['item_inputs'] = [[]]  # Older records bind shared source_images in the native compiler.
    frame = ImageRequestContext(target=rr.Target.model_validate({'kind': 'content', 'basis': 'explicit_reference',
        'artifact_id': 'old-a', 'revision': 1, 'item_indices': [1], 'source_message_id': rows[2]['message_id']}),
        focus='selected_target', materials=[{'reference': {'message_id': rows[2]['message_id'], 'content_index': 1},
                                            'role': '原稿共享素材', 'usage': 'input'}])
    assert rr.freeze_frame(r, q, frame, 'execute_saved')['status'] == 'resolved'


def test_saved_auto_canvas_conflict_is_correctable_before_adopting_or_dispatching(monkeypatch):
    r, _, _, q, rows, groups = catalog_case(monkeypatch)
    groups[0]['items'][0]['options'] = {'aspect_ratio': 'auto', 'resolution': '1K'}
    target = rr.Target.model_validate({'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': 'old-a',
                                      'revision': 1, 'item_indices': [1], 'source_message_id': rows[2]['message_id']})
    with pytest.raises(TaskError, match='REQUEST_TARGET_SAVED_SIZE_CHANGED'):
        rr.freeze_frame(r, q, ImageRequestContext(target=target, focus='selected_target', size={'mode': 'inherit_reference'}), 'execute_saved')
    assert r.task_execution_state is None and rr.binding(r) is None
    assert rr.freeze_frame(r, q, ImageRequestContext(target=target, focus='selected_target'), 'execute_saved')['status'] == 'resolved'


def test_unresolved_new_window_cannot_commit_a_draft_before_selecting_target(monkeypatch):
    r, _, _, q, _, _ = catalog_case(monkeypatch)
    assert rr.check(r, q, call('save_task_text', {'document': {}}))[0] == 'REQUEST_TARGET_UNRESOLVED'


@pytest.mark.asyncio
async def test_target_only_correction_preserves_existing_delivery_conditions_and_size(monkeypatch):
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding
    r, p, _, q, rows, _ = catalog_case(monkeypatch)
    frame = ImageRequestContext(target=rr.Target.model_validate(request_target(rows)), focus='selected_target',
                                size={'mode': 'explicit', 'resolution': '1K'},
                                media={'tool': 'generate_video', 'wait_all': True}, deliveries=['generate_video'])
    workflow = SimpleNamespace(value=WorkflowBinding(kind='ordinary_image', root_task_id=r.task_id,
        window_task_id=r.task_id, generation_run_id=str(uuid4()), intent='edit', request_context=frame), save=AsyncMock())
    p.ecom_workflow = workflow
    schema = rr.selection_schema(r)['function']['parameters']['properties']['request_context']
    assert not {'deliveries', 'media', 'continue_delivery'} & schema['properties'].keys()
    await rr.select(r, q, p, {'intent': 'edit', 'request_context': {'target': request_target(rows), 'focus': 'selected_target'}})
    assert workflow.value.request_context.media.wait_all is True
    assert workflow.value.request_context.deliveries == ['generate_video']
    assert workflow.value.request_context.size.resolution == '1K'
    workflow.save.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('old_target', [{'kind': 'ambiguous', 'basis': 'ambiguous'}, {'kind': 'current', 'basis': 'current_input'}])
async def test_target_repair_keeps_unbound_waiting_conditions(monkeypatch, old_target):
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding
    r, p, _, q, rows, _ = catalog_case(monkeypatch)
    rows[0]['images'] = rows[1]['images']
    rows[0]['images'][0]['reference'] = {'message_id': rows[0]['message_id'], 'content_index': 1}
    frame = ImageRequestContext(target=rr.Target.model_validate(old_target), focus='current_request',
        media={'tool': 'generate_image', 'wait_all': True})
    workflow = SimpleNamespace(value=WorkflowBinding(kind='ordinary_image', root_task_id=r.task_id,
        window_task_id=r.task_id, generation_run_id=str(uuid4()), intent='write_then_submit_images', request_context=frame), save=AsyncMock())
    p.ecom_workflow = workflow
    target = {'kind': 'image', 'basis': 'current_input', 'source_message_id': rows[0]['message_id'], 'content_index': 1}
    await rr.select(r, q, p, {'intent': 'write_then_submit_images', 'request_context': {'target': target, 'focus': 'current_request'}})
    assert workflow.value.request_context.media.wait_all is True


@pytest.mark.asyncio
@pytest.mark.parametrize('approved', [False, True])
async def test_correcting_old_recipe_to_new_image_drops_foreign_confirmation(monkeypatch, approved):
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding
    from services.task_execution.item_confirmation import check
    r, p, _, q, rows, _ = catalog_case(monkeypatch)
    media = {'tool': 'generate_image', 'wait_for': [] if approved else [1],
             'approved': [1] if approved else [], 'confirmation_only': approved,
             'artifact_id': 'old-a', 'revision': 1}
    frame = ImageRequestContext(target=rr.Target.model_validate({'kind': 'content', 'basis': 'explicit_reference',
        'source_message_id': rows[2]['message_id'], 'artifact_id': 'old-a', 'revision': 1, 'item_indices': [1]}),
        focus='selected_target', media=media)
    workflow = SimpleNamespace(value=WorkflowBinding(kind='ordinary_image', root_task_id=r.task_id,
        window_task_id=r.task_id, generation_run_id=str(uuid4()), intent='execute_saved', request_context=frame), save=AsyncMock())
    p.ecom_workflow = workflow
    target = {'kind': 'image', 'basis': 'active_request', 'source_message_id': rows[1]['message_id'], 'content_index': 1}
    await rr.select(r, q, p, {'intent': 'edit', 'request_context': {'target': target, 'focus': 'current_request'}})
    corrected = workflow.value.request_context.media
    assert not corrected.approved and not corrected.wait_for and not corrected.confirmation_only
    assert corrected.artifact_id is None
    assert check(call('generate_image', {'source_message_id': rows[1]['message_id'], 'source_content_index': 1,
        'edit_instruction': '局部修改'}), None, '', semantic=corrected, allow_new=True) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['router_unavailable', 'genuine_ambiguity', 'resolved_generation', 'stale_input'])
async def test_read_only_display_recovers_only_failed_router_and_never_overrides_generation(monkeypatch, failure):
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding
    from tests.test_model_directed_loop import execute
    r, p, h, q, rows, _ = catalog_case(monkeypatch)
    ref = rows[1]['images'][0]['reference']
    value = rr.freeze(r, q, {'kind': 'ambiguous', 'basis': 'ambiguous'})
    value['rejection'] = {'code': 'IMAGE_WORKFLOW_UNAVAILABLE'}
    if failure == 'genuine_ambiguity':
        value.pop('rejection')
    elif failure == 'resolved_generation':
        rr.freeze(r, q, request_target(rows))
        r.workflow_projection = {'intent': 'generate'}
    elif failure == 'stale_input':
        value['input_revision'] = '0'*64
    workflow = SimpleNamespace(value=WorkflowBinding(kind='ordinary_image', root_task_id=r.task_id,
        window_task_id=r.task_id, generation_run_id=str(uuid4()), intent='other'), save=AsyncMock())
    async def save():
        r.workflow_projection = workflow.value.model_dump(mode='json')
    workflow.save.side_effect = save
    p.ecom_workflow = workflow; p.content_controls_available = True
    image = {'type': 'image', 'url': 'https://test.invalid/existing.png', 'source_message_id': ref['message_id'], 'source_content_index': ref['content_index']}
    monkeypatch.setattr('services.handlers.chat.content_tools.show_image', lambda *args: image)
    blocks = await execute(r, p, h, q, [call('read_conversation_text', {'show_image': ref}, 'show-selected')])
    if failure == 'router_unavailable':
        assert image in blocks
        assert rr.binding(r)['target']['source_message_id'] == ref['message_id']
        assert workflow.value.intent == 'explain'
    else:
        assert image not in blocks
        workflow.save.assert_not_awaited()
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_and_correction_use_same_image_identity_and_content_owner_contract(monkeypatch):
    r, _, _, _, rows, _ = catalog_case(monkeypatch)
    target = {'kind': 'image', 'basis': 'active_request', 'source_message_id': rows[1]['message_id'], 'content_index': 1}
    output = {'kind': 'ordinary_image', 'mode': 'new', 'reuse_through_stage': 0, 'intent': 'edit', 'image_count': None,
              'request_context': {'focus': 'selected_target', 'target': target}}
    router = SimpleNamespace(close=AsyncMock(), call_tool_model=AsyncMock(return_value={'choices': [{'message': {'tool_calls': [
        {'function': {'name': 'select_image_workflow', 'arguments': json.dumps(output)}}]}}]}))
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(intent_router_enabled=True, dashscope_api_key='offline', intent_router_model='offline', intent_router_timeout=15))
    monkeypatch.setattr('services.intent_router.IntentRouter', lambda: router)
    selected = await classify_workflow([{'type': 'text', 'text': '开始吧'}], context={'request_resolution': rr.native_context(r)})
    assert selected['request_context']['target']['source_message_id'] == target['source_message_id']
    args = router.call_tool_model.await_args.kwargs
    assert args['timeout'] == 15
    route_schema = args['tools'][0]['function']['parameters']['$defs']['Target']
    correction_schema = rr.selection_schema(r)['function']['parameters']['$defs']['Target']
    assert route_schema == correction_schema
    assert 'image_reference' not in route_schema['properties']
    assert 'image目标不要填写source_message_id或content_index' not in args['system_prompt']


@pytest.mark.asyncio
async def test_canvas_material_alias_maps_current_attachment_without_defaulting_to_history(monkeypatch):
    r, _, _, _, rows, _ = catalog_case(monkeypatch)
    mid = rows[0]['message_id']
    rows[0]['images'] = [{'name': 'new.png', 'reference': {'message_id': mid, 'content_index': 1}}]
    raw = {'kind': 'ordinary_image', 'mode': 'new', 'reuse_through_stage': 0, 'intent': 'write', 'image_count': None,
           'request_context': {'focus': 'current_request', 'canvas_resource': 'image:1',
                               'target': {'kind': 'image', 'basis': 'current_input', 'source_message_id': mid, 'content_index': 1}}}
    router = SimpleNamespace(close=AsyncMock(), call_tool_model=AsyncMock(return_value={'choices': [{'message': {'tool_calls': [
        {'function': {'name': 'select_image_workflow', 'arguments': json.dumps(raw)}}]}}]}))
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(intent_router_enabled=True, dashscope_api_key='offline', intent_router_model='offline', intent_router_timeout=5))
    monkeypatch.setattr('services.intent_router.IntentRouter', lambda: router)
    parts = [{'type': 'image', 'name': 'new.png', 'reference': rows[0]['images'][0]['reference']}]
    value = await classify_workflow(parts, context={'request_resolution': rr.native_context(r)})
    assert value['request_context']['canvas_image'] == 1
    assert 'canvas_resource' not in value['request_context']
    raw['request_context']['canvas_image'] = 2
    router.call_tool_model.return_value['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = json.dumps(raw)
    assert await classify_workflow(parts, context={'request_resolution': rr.native_context(r)}) == {'kind': 'unavailable'}


@pytest.mark.asyncio
async def test_ordinary_submission_preserves_target_with_a_proven_redundant_size_reference(monkeypatch):
    r, _, _, _, rows, _ = catalog_case(monkeypatch)
    mid = rows[1]['message_id']
    raw = {'kind': 'ordinary_image', 'mode': 'revise', 'reuse_through_stage': 0, 'intent': 'write_then_submit_images', 'image_count': 1,
           'request_context': {'focus': 'selected_target', 'size': {'mode': 'inherit_reference', 'reference_index': 0},
               'materials': [{'material_index': 1, 'role': '底图', 'usage': 'input'}],
               'target': {'kind': 'image', 'basis': 'active_request', 'source_message_id': mid, 'content_index': 1}}}
    router = SimpleNamespace(close=AsyncMock(), call_tool_model=AsyncMock(return_value={'choices': [{'message': {'tool_calls': [
        {'function': {'name': 'select_image_workflow', 'arguments': json.dumps(raw)}}]}}]}))
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(intent_router_enabled=True, dashscope_api_key='offline', intent_router_model='offline', intent_router_timeout=5))
    monkeypatch.setattr('services.intent_router.IntentRouter', lambda: router)
    parts = [{'type': 'image', 'name': 'b.png', 'origin': 'history', 'reference': {'message_id': mid, 'content_index': 1}}]
    value = await classify_workflow(parts, context={'request_resolution': rr.native_context(r)})
    assert value['intent'] == 'write_then_submit_images' and value['mode'] == 'new'
    assert value['request_context']['size'] == {'mode': 'inherit_reference'}
    assert value['request_context']['target']['source_message_id'] == mid
    raw['request_context']['size']['reference_index'] = 1
    router.call_tool_model.return_value['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = json.dumps(raw)
    invalid = await classify_workflow(parts, context={'request_resolution': rr.native_context(r)})
    assert invalid['kind'] == 'unavailable' and invalid['rejection']['code'] == 'IMAGE_WORKFLOW_ARGUMENTS_INVALID'
