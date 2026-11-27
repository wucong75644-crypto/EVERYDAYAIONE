"""Request identity, not user-word matching, precedes recovery and effects."""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.task_execution import request_resolution as rr, content_objects as co
from services.task_execution.contracts import TaskError
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_model_bridge import call
from tests.test_content_objects import document


def setup_case():
    r, p, h, q = case()
    now, old, recent = [str(uuid4()) for _ in range(3)]
    q = replace(q, context_anchor=SimpleNamespace(input_message_id=now, base_revision=8))
    def row(mid, intent, text):
        return {'message_id': mid, 'root_task_id': str(uuid4()), 'intent': intent,
                'source_sha256': 'a' * 64, 'text_excerpt': text, 'images': [],
                'has_execution_state': intent == 'write', 'resolution': None}
    rows = [row(now, None, '没问题，开始吧'), row(recent, 'edit', '在本子内页添加银色蕾丝'),
            row(old, 'write', '旧灯塔卡册封面')]
    rows[1]['images'] = [{'name': '本子.png', 'reference': {'message_id': recent, 'content_index': 1}}]
    r.checkpoint_base[rr.CONTEXT] = {'version': 1, 'input_message_id': now, 'requests': rows}
    return r, p, h, q, rows


def request_target(rows):
    return {'kind': 'request', 'basis': 'active_request', 'source_message_id': rows[1]['message_id']}


@pytest.mark.parametrize('words', ['开始吧', '可以，按这个来', 'go ahead', '请继续处理刚才的要求'])
def test_followup_words_do_not_change_the_structured_focus(words):
    r, _, _, q, rows = setup_case()
    rows[0]['text_excerpt'] = words
    value = rr.freeze(r, q, request_target(rows))
    assert value['target']['source_message_id'] == rows[1]['message_id']
    assert value['focus'] == 'selected_target'
    with pytest.raises(TaskError, match='REQUEST_TARGET_FOCUS_MISMATCH'):
        rr.freeze(r, q, {'kind': 'request', 'basis': 'active_request', 'source_message_id': rows[2]['message_id']})


def test_current_request_and_explicit_historical_request_are_distinct():
    r, _, _, q, rows = setup_case()
    assert rr.freeze(r, q, {'kind': 'current', 'basis': 'current_input'})['target']['source_message_id'] == rows[0]['message_id']
    value = rr.freeze(r, q, {'kind': 'request', 'basis': 'explicit_reference', 'source_message_id': rows[2]['message_id']})
    assert value['target']['source_message_id'] == rows[2]['message_id']


@pytest.mark.asyncio
async def test_old_content_is_rejected_before_adoption_or_native_dispatch():
    r, p, h, q, rows = setup_case()
    rr.freeze(r, q, request_target(rows))
    r.task_followup_adopt = AsyncMock()
    p.content_controls_available = True
    await execute(r, p, h, q, [call('generate_image', {'content_source': {'artifact_id': 'old-lighthouse', 'item_index': 1}}, 'wrong')])
    result = json.loads(p.messages[-1]['content'])
    assert result['code'] == 'REQUEST_TARGET_CONTENT_MISMATCH' and result['accepted'] is False
    r.task_followup_adopt.assert_not_awaited()
    h._execute_tool_calls.assert_not_awaited()
    assert r.task_execution_state is None


@pytest.mark.asyncio
async def test_new_saved_draft_for_unsubmitted_request_can_be_used():
    r, p, h, q, rows = setup_case()
    rr.freeze(r, q, request_target(rows))
    p.content_controls_available = True
    await execute(r, p, h, q, [call(co.SAVE, {'document': document()}, 'save-current')])
    assert r.task_execution_state is not None
    artifact = co.objects(r.task_execution_state)[0]
    assert rr.check(r, q, call('generate_image', {'content_source': {'artifact_id': artifact.id, 'item_index': 1}})) is None
    h._execute_tool_calls.assert_not_awaited()


def test_saved_version_items_and_owner_must_match(monkeypatch):
    r, _, _, q, rows = setup_case()
    group = {'artifact_id': 'a', 'revision': 2, 'root_task_id': rows[2]['root_task_id'], 'items': [{'item_index': 1}, {'item_index': 2}]}
    monkeypatch.setattr(rr, 'groups', lambda _: [group])
    rr.freeze(r, q, {'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': 'a', 'revision': 2, 'item_indices': [2]})
    assert rr.check(r, q, call('generate_image', {'content_source': {'artifact_id': 'a', 'item_index': 2}})) is None
    assert rr.check(r, q, call('generate_image', {'content_source': {'artifact_id': 'a', 'item_index': 1}}))[0] == 'REQUEST_TARGET_CONTENT_MISMATCH'
    assert rr.check(r, q, call('resume_task_execution', {}), root_task_id=group['root_task_id']) is None
    assert rr.check(r, q, call('resume_task_execution', {}), root_task_id=str(uuid4()))[0] == 'REQUEST_TARGET_OWNER_MISMATCH'
    with pytest.raises(TaskError, match='REQUEST_TARGET_CONTENT_CHANGED'):
        rr.freeze(r, q, {'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': 'a', 'revision': 1, 'item_indices': [2]})


def test_generated_image_and_uploaded_image_remain_selectable():
    r, _, _, q, rows = setup_case()
    generated = {'source_message_id': str(uuid4()), 'content_index': 0, 'source_sha256': 'b' * 64,
                 'root_task_id': rows[2]['root_task_id']}
    r.checkpoint_base[rr.CONTEXT]['image_results'] = [generated]
    for mid, index in [(generated['source_message_id'], 0), (rows[1]['message_id'], 1)]:
        rr.freeze(r, q, {'kind': 'image', 'basis': 'explicit_reference', 'source_message_id': mid, 'content_index': index})
        assert rr.check(r, q, call('generate_image', {'source_message_id': mid, 'source_content_index': index, 'edit_instruction': '改背景'})) is None
        assert rr.check(r, q, call('generate_image', {'source_message_id': mid, 'source_content_index': index + 1, 'edit_instruction': '改背景'}))[0] == 'REQUEST_TARGET_IMAGE_MISMATCH'


def test_new_input_invalidates_target_but_old_checkpoint_does_not_acquire_new_contract():
    r, _, _, q, rows = setup_case()
    rr.freeze(r, q, request_target(rows))
    q = replace(q, context_anchor=SimpleNamespace(input_message_id=str(uuid4()), base_revision=9))
    assert rr.check(r, q, call('generate_image', {'draft': {}}))[0] == 'REQUEST_TARGET_UNRESOLVED'
    r.checkpoint_base = {}
    assert rr.check(r, q, call('generate_image', {'content_source': {'artifact_id': 'original', 'item_index': 1}})) is None


@pytest.mark.asyncio
async def test_structured_question_is_saved_without_dispatch_and_survives_replay():
    r, p, h, q, rows = setup_case()
    p.content_controls_available = True
    rr.freeze(r, q, {'kind': 'ambiguous', 'basis': 'ambiguous'})
    await execute(r, p, h, q, [call(rr.RESPOND, {'purpose': 'clarification', 'text': '要处理本子内页，还是之前的灯塔封面？'}, 'ask')])
    saved = r.checkpoint_base['_request_response_v1']
    assert saved['status'] == 'not_submitted' and '本子内页' in saved['text']
    assert h._tool_result_stop_reason.startswith(saved['text'])
    h._execute_tool_calls.assert_not_awaited()
    assert json.loads(p.messages[-1]['content'])['accepted'] is False


def test_strict_unsubmitted_status_cannot_publish_speculative_acceptance():
    from services.handlers.chat.content_tools import unconfirmed_image_reply, IMAGE_STATUS_NOT_SUBMITTED
    assert unconfirmed_image_reply('已提交，后台处理中。', False, structured_status=True) == IMAGE_STATUS_NOT_SUBMITTED
    assert unconfirmed_image_reply('请问需要哪张图？', False).startswith('请问需要哪张图？')


@pytest.mark.asyncio
async def test_router_receives_unsubmitted_requests_and_can_target_them(monkeypatch):
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow, ImageRequestContext, classify_workflow
    r, _, _, q, rows = setup_case()
    w = object.__new__(EcommerceWorkflow)
    w.runtime = r; w.request = q; w.input = [{'type': 'text', 'text': rows[0]['text_excerpt']}]
    ctx = w.action_context()
    assert ctx['request_resolution']['active_request_message_id'] == rows[1]['message_id']
    assert ctx['original_user_request'] == rows[1]['text_excerpt']
    assert any(i.get('reference', {}).get('message_id') == rows[1]['message_id'] for i in w.action_input())
    frame = ImageRequestContext(target=rr.Target.model_validate(request_target(rows)), focus='selected_target').model_dump(mode='json', exclude_none=True)
    answer = {'kind': 'ordinary_image', 'mode': 'new', 'reuse_through_stage': 0, 'intent': 'edit', 'image_count': None, 'request_context': frame}
    router = SimpleNamespace(close=AsyncMock(), call_tool_model=AsyncMock(return_value={'choices': [{'message': {'tool_calls': [
        {'function': {'name': 'select_image_workflow', 'arguments': json.dumps(answer)}}]}}]}))
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(intent_router_enabled=True, dashscope_api_key='offline', intent_router_model='offline', intent_router_timeout=5))
    monkeypatch.setattr('services.intent_router.IntentRouter', lambda: router)
    result = await classify_workflow(w.action_input(), context=ctx)
    assert result['request_context']['target']['source_message_id'] == rows[1]['message_id']
    sent = router.call_tool_model.await_args.kwargs
    assert rows[1]['message_id'] in sent['text'] and '本子内页' in sent['text']
    assert 'target' in sent['tools'][0]['function']['parameters']['properties']['request_context']['required']


@pytest.mark.asyncio
async def test_main_model_can_correct_target_before_dispatch(monkeypatch):
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow, WorkflowBinding
    r, p, h, q, rows = setup_case()
    p.content_controls_available = True
    rr.freeze(r, q, {'kind': 'ambiguous', 'basis': 'ambiguous'})
    w = object.__new__(EcommerceWorkflow)
    w.runtime = r; w.request = q
    w.value = WorkflowBinding(kind='ordinary_image', root_task_id=r.task_id, window_task_id=r.task_id,
                              generation_run_id=str(uuid4()), intent='other')
    w.save = AsyncMock()
    p.ecom_workflow = w
    await execute(r, p, h, q, [call(rr.SELECT, {'intent': 'edit', 'request_context': {'target': request_target(rows), 'focus': 'selected_target'}}, 'resolve')])
    result = json.loads(p.messages[-1]['content'])
    assert result['status'] == 'target_selected' and result['accepted'] is False
    assert 'source_sha256' not in json.dumps(result)
    assert 'execution_task_id' not in json.dumps(result)
    assert w.value.intent == 'edit' and rr.binding(r)['target']['source_message_id'] == rows[1]['message_id']
    w.save.assert_awaited_once()
    h._execute_tool_calls.assert_not_awaited()


def test_generated_result_cannot_be_delivered_as_a_new_execution():
    r, _, _, q, rows = setup_case()
    rr.freeze(r, q, request_target(rows))
    r.workflow_projection = {'intent': 'edit'}
    with pytest.raises(TaskError, match='REQUEST_TARGET_EXISTING_RESULT_NOT_REQUESTED'):
        rr.check_display(r, q, {'message_id': rows[2]['message_id'], 'content_index': 0})
    r.workflow_projection = {'intent': 'explain'}
    with pytest.raises(TaskError, match='REQUEST_TARGET_IMAGE_MISMATCH'):
        rr.check_display(r, q, {'message_id': rows[2]['message_id'], 'content_index': 0})
    assert rr.check_display(r, q, {'message_id': rows[1]['message_id'], 'content_index': 1}) is None


def test_truncated_target_requires_full_source_before_generation():
    r, _, _, q, rows = setup_case()
    rows[1].update(text_requires_read=True, text_part_indices=[0, 2], context_revision=5)
    rr.freeze(r, q, request_target(rows))
    operation = call('generate_image', {'draft': {}})
    assert rr.check(r, q, operation)[0] == 'REQUEST_TARGET_FULL_TEXT_REQUIRED'
    r.checkpoint_base['_chat_content_tools'] = {'observed_history': {
        f"{rows[1]['message_id']}:{i}": {'sha256': 'c' * 64, 'context_revision': 5} for i in (0, 2)}}
    assert rr.check(r, q, operation) is None


def test_executed_followup_becomes_focus_without_losing_original_request_link():
    r, _, _, q, rows = setup_case()
    rows[1]['has_execution_state'] = True
    rows[1]['resolution'] = {'status': 'resolved', 'target': {'kind': 'request', 'source_message_id': rows[2]['message_id']}}
    rows[1]['focus'] = 'selected_target'
    assert rr.active_request(rr.context(r)) == rows[1]['message_id']


def test_old_receipt_cannot_supply_current_request_status():
    from services.task_execution.reducer import digest
    from services.handlers.chat.content_tools import IMAGE_STATUS_NOT_SUBMITTED
    r, _, _, q, rows = setup_case()
    rr.freeze(r, q, request_target(rows))
    old = SimpleNamespace(step_id='old-image', owner_task_id=rows[2]['root_task_id'], status='accepted')
    r.task_execution_state = SimpleNamespace(call_bindings=[SimpleNamespace(step_id='old-image', tool_name='generate_image', input_sha256=digest([]))],
                                             operations=[old], artifacts=[])
    assert rr.image_status_reply(r, q) == IMAGE_STATUS_NOT_SUBMITTED
    old.owner_task_id = r.task_id
    assert rr.image_status_reply(r, q) == '图片任务已受理，正在后台处理中。'


def test_unrelated_explanation_does_not_erase_business_focus():
    r, _, _, q, rows = setup_case()
    question = {'message_id': str(uuid4()), 'root_task_id': str(uuid4()), 'intent': 'other',
                'focus': 'retain_previous',
                'resolution': {'status': 'resolved', 'target': {'kind': 'current', 'source_message_id': str(uuid4())}}}
    rr.context(r)['requests'].insert(1, question)
    assert rr.active_request(rr.context(r)) == rows[2]['message_id']


def test_new_design_discussion_is_focus_even_without_execution_or_saved_plan():
    r, _, _, q, rows = setup_case()
    rows[1].update(intent='explain', focus='current_request', has_execution_state=False)
    assert rr.active_request(rr.context(r)) == rows[1]['message_id']
    rr.freeze(r, q, request_target(rows))
    with pytest.raises(TaskError, match='REQUEST_TARGET_FOCUS_MISMATCH'):
        rr.freeze(r, q, {'kind': 'request', 'basis': 'active_request', 'source_message_id': rows[2]['message_id']})


@pytest.mark.parametrize('arguments', [[], {'content_source': 'invalid'}, {'content_source': []}])
def test_target_guard_leaves_invalid_argument_shapes_to_native_validation(arguments):
    r, _, _, q, rows = setup_case()
    rr.freeze(r, q, request_target(rows))
    assert rr.check(r, q, call('generate_image', arguments)) is None


def test_read_task_result_cannot_substitute_an_old_image_for_new_execution():
    from services.task_execution.reducer import digest
    r, _, _, q, rows = setup_case()
    rr.freeze(r, q, request_target(rows))
    r.workflow_projection = {'intent': 'edit'}
    image = SimpleNamespace(id='old-output', kind='image', delivery_task_id=rows[2]['root_task_id'],
                            source_task_id=str(uuid4()), step_id='old-step')
    r.task_execution_state = SimpleNamespace(artifacts=[image], call_bindings=[
        SimpleNamespace(tool_name='generate_image', step_id='old-step', input_sha256=digest([]))])
    assert rr.check(r, q, call('read_task_result', {'artifact_id': 'old-output'}))[0] == 'REQUEST_TARGET_EXISTING_RESULT_NOT_REQUESTED'
    image.delivery_task_id = r.task_id
    assert rr.check(r, q, call('read_task_result', {'artifact_id': 'old-output'})) is None


def test_new_target_cannot_be_overridden_by_legacy_previous_image_default():
    r, _, _, q, rows = setup_case()
    rr.freeze(r, q, request_target(rows))
    assert rr.check(r, q, call('generate_image', {'previous_result_edit': '修改背景'}))[0] == 'REQUEST_TARGET_IMAGE_REFERENCE_REQUIRED'
    assert rr.check(r, q, call('generate_image', {'previous_result_edit': '修改背景',
        'source_message_id': rows[2]['message_id'], 'source_content_index': 0}))[0] == 'REQUEST_TARGET_IMAGE_MISMATCH'
    r.checkpoint_base = {}
    assert rr.check(r, q, call('generate_image', {'previous_result_edit': '修改背景'})) is None


def test_unsubmitted_target_does_not_inherit_old_recovery_state_as_its_base():
    from services.task_execution.direct_loop import content_followup_state
    from tests.test_model_directed_records import operation_state
    r, _, _, q, rows = setup_case()
    _, old = operation_state('accepted')
    r.task_followup_candidate = {'state': old.model_dump(mode='json')}
    rr.freeze(r, q, request_target(rows))
    assert content_followup_state(r) is None
    r.checkpoint_base = {}
    assert content_followup_state(r) == old


@pytest.mark.asyncio
async def test_router_image_token_resolves_real_content_position_instead_of_counting_images(monkeypatch):
    from services.agent.image.ecommerce_planner.workflow import classify_workflow
    r, _, _, q, rows = setup_case()
    current = rows[0]['message_id']
    raw = {'kind': 'ordinary_image', 'mode': 'new', 'reuse_through_stage': 0, 'intent': 'edit', 'image_count': None,
           'request_context': {'focus': 'current_request', 'target': {'kind': 'image', 'basis': 'current_input', 'image_reference': 'material:1'}}}
    router = SimpleNamespace(close=AsyncMock(), call_tool_model=AsyncMock(return_value={'choices': [{'message': {'tool_calls': [
        {'function': {'name': 'select_image_workflow', 'arguments': json.dumps(raw)}}]}}]}))
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(intent_router_enabled=True, dashscope_api_key='offline', intent_router_model='offline', intent_router_timeout=5))
    monkeypatch.setattr('services.intent_router.IntentRouter', lambda: router)
    result = await classify_workflow([{'type': 'text', 'text': '改这张'}, {'type': 'image', 'reference': {'message_id': current, 'content_index': 1}}],
                                     context={'request_resolution': rr.native_context(r)})
    assert result['request_context']['target']['source_message_id'] == current
    assert result['request_context']['target']['content_index'] == 1
    offered = router.call_tool_model.await_args.kwargs['tools'][0]['function']['parameters']['$defs']['Target']
    assert 'content_index' not in offered['properties']
    image = next(form for form in offered['oneOf'] if form['properties']['kind']['const'] == 'image')
    assert 'image_reference' in image['required']


def test_model_resolution_exposes_selectable_identity_without_host_proof():
    r, _, _, q, rows = setup_case()
    value = rr.freeze(r, q, request_target(rows), focus='selected_target')
    rows[1]['resolution'] = value
    public = rr.model_context(r)
    assert public['resolution']['target']['source_message_id'] == rows[1]['message_id']
    assert public['resolution']['focus'] == 'selected_target'
    assert 'source_sha256' not in json.dumps(public)
    assert 'execution_task_id' not in json.dumps(public)
    assert 'input_revision' not in json.dumps(public)
    assert 'source_sha256' in value['target']


def test_focused_previous_image_relation_is_normalized_without_switching_source():
    r, _, _, q, rows = setup_case()
    target = {'kind': 'image', 'basis': 'current_input', 'source_message_id': rows[1]['message_id'], 'content_index': 1}
    value = rr.freeze(r, q, target, focus='selected_target')
    assert value['target']['basis'] == 'active_request'
    assert value['target']['source_message_id'] == rows[1]['message_id']
    assert value['focus'] == 'selected_target'
    with pytest.raises(TaskError, match='REQUEST_TARGET_BASIS_INVALID'):
        rr.freeze(r, q, {**target, 'source_message_id': rows[2]['message_id']})


@pytest.mark.parametrize('form', ['edit', 'draft'])
def test_selected_image_accepts_both_existing_single_image_forms(form):
    r, _, _, q, rows = setup_case()
    mid = rows[1]['message_id']
    rr.freeze(r, q, {'kind': 'image', 'basis': 'active_request', 'source_message_id': mid, 'content_index': 1})
    args = ({'source_message_id': mid, 'source_content_index': 1, 'edit_instruction': '改背景'} if form == 'edit'
            else {'draft': {'body': '改背景', 'materials': [{'reference': {'message_id': mid, 'content_index': 1}, 'role': '底图', 'usage': 'input'}]}})
    assert rr.check(r, q, call('generate_image', args)) is None
    if form == 'draft':
        args['draft']['materials'].insert(0, {'reference': {'message_id': rows[2]['message_id'], 'content_index': 1}, 'role': '另一图', 'usage': 'input'})
    else:
        args['source_message_id'] = rows[2]['message_id']
    assert rr.check(r, q, call('generate_image', args))[0] == 'REQUEST_TARGET_IMAGE_MISMATCH'
