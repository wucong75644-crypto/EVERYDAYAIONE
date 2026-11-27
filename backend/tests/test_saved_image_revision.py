"""Customer changes to an old plan must execute a revised, immutable recipe."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from services.task_execution import content_objects as co, saved_image_revision as revision
from tests.semantic_fixtures import semantic_request
from tests.test_content_objects import registered, ref
from tests.test_model_directed_loop import execute
from tests.test_task_execution_model_bridge import call


@pytest.mark.parametrize(('options', 'size', 'changes'), [
    ({'aspect_ratio': 'auto'}, {'aspect_ratio': '1:1', 'resolution': '1K'}, {'aspect_ratio': '1:1'}),
    ({'aspect_ratio': '3:4', 'resolution': '2K'}, {'aspect_ratio': '1:1'}, {'aspect_ratio': '1:1'}),
    ({'aspect_ratio': '3:4', 'resolution': '2K'}, {'resolution': '4K'}, {'resolution': '4K'}),
    ({'aspect_ratio': '3:4'}, {}, {}),
    ({'aspect_ratio': '3:4'}, {'orientation': 'portrait'}, {}),
    ({'aspect_ratio': 'auto'}, {'orientation': 'portrait'}, {'orientation': 'portrait'}),
    ({'aspect_ratio': '3:4'}, {'width': 1000, 'height': 1000, 'approximate_pixels': False}, {}),
    ({'aspect_ratio': '3:4'}, {'width': 1000, 'height': 1000, 'approximate_pixels': True}, {'aspect_ratio': '1:1'}),
])
def test_compare_only_explicit_structured_changes(options, size, changes):
    assert revision.size_changes(options, size) == changes


async def portrait_case():
    r, p, h, q, row = await registered()
    await execute(r, p, h, q, [call(co.SAVE, {'base': ref(row, 3), 'updates': [
        {'item_index': 3, 'item': {'body': '画面保持竖版构图，不要改为正方形。保留银杏图案。',
            'display_prefix': '### 第三个方案：竖版银杏',
            'options': {'aspect_ratio': 'auto', 'resolution': '2K', 'output_format': 'png'}}}]}, 'portrait')])
    row, = co.catalog(r.task_execution_state)
    q = semantic_request(q, 'execute_saved', size={'aspect_ratio': '1:1'})
    return r, p, h, q, row


@pytest.mark.asyncio
async def test_original_failure_is_recoverable_before_dispatch_then_new_version_compiles():
    r, p, h, q, row = await portrait_case()
    before = r.task_execution_state
    _, original, selected = co.load(before, co.ContentReference.model_validate(ref(row, 3)))
    old_call = call('generate_image', {'content_source': ref(row, 3)}, 'square')
    await execute(r, p, h, q, [old_call])
    error = json.loads(p.messages[-1]['content'])
    assert error['code'] == revision.CODE and error['accepted'] is False
    assert error['saved_options'] == selected.options
    assert error['requested_size'] == {'aspect_ratio': '1:1'}
    assert error['recovery']['read']['arguments']['content_source'] == ref(row, 3)
    assert not getattr(h, '_tool_result_stop_reason', '')
    assert r.task_execution_state == before
    h._execute_tool_calls.assert_not_awaited()

    patch = {'body': '正方形画布，保留银杏图案及产品结构。',
             'display_prefix': '### 第三个方案：正方形银杏',
             'options': {**selected.options, 'aspect_ratio': '1:1'}}
    await execute(r, p, h, q, [call(co.SAVE, {'base': ref(row, 3), 'updates': [
        {'item_index': 3, 'item': patch}], 'continue_execution': True}, 'save-square')])
    receipt = json.loads(p.messages[-1]['content'])
    assert receipt['ok'] is True
    newest, = co.catalog(r.task_execution_state)
    assert newest['artifact_id'] != row['artifact_id'] and newest['revision'] == row['revision'] + 1
    new_call = call('generate_image', {'content_source': ref(newest, 3)}, 'new-square')
    assert revision.check(r, q, [new_call], r.task_execution_state) == {}
    native, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, ref(newest, 3))
    assert (native['aspect_ratio'], native['resolution'], native['output_format']) == ('1:1', '2K', 'png')
    _, revised, item = co.load(r.task_execution_state, co.ContentReference.model_validate(ref(newest, 3)))
    assert revised.items[:2] == original.items[:2] and item.materials == selected.materials
    assert co.load(before, co.ContentReference.model_validate(ref(row, 3)))[1] == original
    with pytest.raises(ValueError, match='CONTENT_BASE_VERSION_CHANGED'):
        co.compile_input(r.task_execution_state, 'generate_image', {}, ref(row, 3))


@pytest.mark.asyncio
@pytest.mark.parametrize('patch', [
    {'options': {'aspect_ratio': '1:1', 'resolution': '2K', 'output_format': 'png'}},
    {'body': '正方形银杏。', 'display_prefix': '正方形方案', 'options': {'aspect_ratio': '1:1'}},
    {'body': '正方形银杏。', 'options': {'aspect_ratio': '1:1', 'resolution': '2K', 'output_format': 'png'}},
])
async def test_incomplete_revision_never_saves_or_dispatches(patch):
    r, p, h, q, row = await portrait_case()
    before = copy.deepcopy(r.task_execution_state)
    await execute(r, p, h, q, [call(co.SAVE, {'base': ref(row, 3), 'updates': [
        {'item_index': 3, 'item': patch}], 'continue_execution': True}, 'incomplete')])
    assert json.loads(p.messages[-1]['content'])['code'] == revision.CODE
    assert r.task_execution_state == before
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejection_budget_survives_reconstruction_and_resets_for_new_input():
    r, p, h, q, row = await portrait_case()
    c = call('generate_image', {'content_source': ref(row, 3)})
    for attempt in range(3):
        errors = revision.check(r, q, [c], r.task_execution_state)
        assert errors[c['id']]['code'] == (revision.CODE if attempt < 2 else 'CONTENT_IMAGE_REVISION_STOPPED')
        r.checkpoint_base = json.loads(json.dumps(r.checkpoint_base))
    q = replace(q, context_anchor=SimpleNamespace(input_message_id='next-input', base_revision=100))
    assert revision.check(r, q, [c], r.task_execution_state)[c['id']]['code'] == revision.CODE


@pytest.mark.asyncio
async def test_display_analysis_and_unchanged_execution_remain_unaffected():
    r, p, h, q, row = await portrait_case()
    c = call('generate_image', {'content_source': ref(row, 3)})
    for intent in ('explain', 'other', 'generate'):
        unchanged = semantic_request(q, intent, size={'aspect_ratio': '1:1'})
        assert revision.check(r, unchanged, [c], r.task_execution_state) == {}
    assert revision.check(r, semantic_request(q, 'execute_saved'), [c], r.task_execution_state) == {}


@pytest.mark.asyncio
async def test_historical_owner_is_checked_before_followup_adoption(monkeypatch):
    from services.task_execution import direct_loop
    r, p, h, q, row = await portrait_case()
    previous = r.task_execution_state
    r.task_execution_state = None
    monkeypatch.setattr(direct_loop, 'content_followup_state', lambda runtime: previous)
    await execute(r, p, h, q, [call('generate_image', {'content_source': ref(row, 3)})])
    assert json.loads(p.messages[-1]['content'])['code'] == revision.CODE
    assert r.task_execution_state is None
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_old_source_stops_without_dispatching_a_valid_sibling():
    r, p, h, q, row = await portrait_case()
    for attempt in range(3):
        await execute(r, p, h, q, [
            call('generate_image', {'content_source': ref(row, 3)}, f'old-{attempt}'),
            call('generate_image', {'content_source': ref(row, 1)}, f'valid-{attempt}')])
        results = [json.loads(m['content']) for m in p.messages[-2:]]
        assert results[0]['code'] == (revision.CODE if attempt < 2 else 'CONTENT_IMAGE_REVISION_STOPPED')
        assert results[1]['code'] == 'CONTENT_IMAGE_REVISION_BATCH_BLOCKED'
        assert bool(getattr(h, '_tool_result_stop_reason', '')) == (attempt == 2)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmation_only', [True, False])
async def test_user_can_approve_one_waiting_plan_with_a_size_change_in_the_same_input(confirmation_only):
    from tests.test_content_objects import document
    from tests.test_model_directed_loop import case
    from services.task_execution import item_confirmation
    r, p, h, q = case()
    p.content_controls_available = True
    draft = document()
    draft['items'][2]['options']['aspect_ratio'] = '3:4'
    await execute(r, p, h, q, [call(co.SAVE, {'document': draft,
        'confirmation': {'tool': 'generate_image', 'wait_all': True}}, 'waiting')])
    old, = co.catalog(r.task_execution_state)
    q = semantic_request(q, 'execute_saved', size={'aspect_ratio': '1:1'}, media={
        'tool': 'generate_image', 'approved': [3], 'confirmation_only': confirmation_only,
        'artifact_id': old['artifact_id'], 'revision': old['revision']})
    decision = {'owner_task_id': str(r.task_id), 'event_id': 'event', 'message_sha256': 'a' * 64,
        'artifact_id': old['artifact_id'], 'revision': old['revision'], 'tool': 'generate_image',
        'approved_items': [3], 'object_key': old['object_key']}
    r.checkpoint_base['_media_decisions_v1'] = {'version': 1, 'items': [decision]}
    await execute(r, p, h, q, [call(co.SAVE, {'base': ref(old, 3), 'updates': [
        {'item_index': 3, 'item': {'body': '正方形封面，保留原图结构与图案。',
            'options': {**draft['items'][2]['options'], 'aspect_ratio': '1:1'}}}],
        'continue_execution': True}, 'revise-approved')])
    result = json.loads(p.messages[-1]['content'])
    assert result['ok'] is True, result
    assert result['approved_execution']['item_indices'] == [3]
    new, = co.catalog(r.task_execution_state)
    assert [i['item_index'] for i in new['items']] == [1, 2, 3]
    directive = item_confirmation.directive(q, r)
    assert directive.artifact_id == new['artifact_id'] and directive.revision == new['revision']
    assert item_confirmation.check(call('generate_image', {'content_source': ref(new, 3)}),
        r.task_execution_state, '', semantic=directive) is None
    assert item_confirmation.check(call('generate_image', {'content_source': ref(new, 1)}),
        r.task_execution_state, '', semantic=directive)[0] == 'CONTENT_CONFIRMATION_DIFFERENT_ITEM'
    assert decision['artifact_id'] == new['artifact_id']
    previous = r.checkpoint_base[revision.KEY]['approval_revision']['previous_decision']
    assert previous['artifact_id'] == old['artifact_id']
    # New input cannot inherit the previous window's version bridge.
    next_input = replace(q, context_anchor=SimpleNamespace(input_message_id='other', base_revision=100))
    assert item_confirmation.directive(next_input, r).artifact_id == old['artifact_id']
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('has_size_change', [True, False])
async def test_revision_bridge_cannot_invent_execution_or_change_a_pure_confirmation(has_size_change):
    from tests.test_content_objects import document
    from tests.test_model_directed_loop import case
    r, p, h, q = case()
    p.content_controls_available = True
    await execute(r, p, h, q, [call(co.SAVE, {'document': document(),
        'confirmation': {'tool': 'generate_image', 'wait_all': True}}, 'waiting')])
    old, = co.catalog(r.task_execution_state)
    before = copy.deepcopy(r.task_execution_state)
    q = semantic_request(q, 'execute_saved', size={'aspect_ratio': '3:4'} if has_size_change else {},
        media={'tool': 'generate_image', 'approved': [3], 'confirmation_only': True,
               'artifact_id': old['artifact_id'], 'revision': old['revision']})
    await execute(r, p, h, q, [call(co.SAVE, {'base': ref(old, 3), 'updates': [
        {'item_index': 3, 'item': {'body': '竖版画布，保留原图。',
            'options': {**document()['items'][2]['options'], 'aspect_ratio': '3:4'}}}],
        'continue_execution': not has_size_change}, 'unauthorized-change')])
    assert json.loads(p.messages[-1]['content'])['code'] == 'CONTENT_CONFIRMATION_EXISTING_ITEM_REQUIRED'
    assert r.task_execution_state == before
    assert not (r.checkpoint_base.get(revision.KEY) or {}).get('approval_revision')
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_only_size_revision_updates_original_instead_of_registering_another_group():
    from services.handlers.chat import content_tools
    from services.tools import build_legacy_catalog
    r, p, h, q, old = await registered()
    q = semantic_request(q, 'write', size={'resolution': '2K'}, focus='selected_target', target={
        'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': old['artifact_id'],
        'revision': old['revision'], 'item_indices': [3]})
    assert content_tools.revises_saved_item(q, r)
    source = [co.SCHEMA, *[build_legacy_catalog().require(n).to_schema() for n in ('generate_image', 'generate_video')]]
    before = content_tools.model_tools(source, saved_text_available=True)
    tools = content_tools.model_tools(source, saved_text_available=True, revise_saved=True)
    assert tools[1:] == before[1:]  # A save-form change grants no extra media capability.
    save = tools[0]['function']['parameters']
    assert set(save['required']) == {'base', 'updates'}
    assert not {'document', 'new_content', 'message_source'} & save['properties'].keys()
    await execute(r, p, h, q, [call(co.SAVE, {'document': {
        'title': '重新登记的一整组', 'items': [{'title': '第三个', 'body': '只改清晰度。'}]}}, 'fork')])
    assert json.loads(p.messages[-1]['content'])['code'] == 'CONTENT_EXECUTION_SOURCE_REQUIRED'
    assert co.catalog(r.task_execution_state) == [old]
    _, original, item = co.load(r.task_execution_state, co.ContentReference.model_validate(ref(old, 3)))
    await execute(r, p, h, q, [call(co.SAVE, {'base': ref(old, 3), 'updates': [{
        'item_index': 3, 'item': {'body': item.body + '\n输出清晰度2K。',
            'options': {**item.options, 'resolution': '2K'}}}], 'continue_execution': False}, 'revise')])
    latest, = co.catalog(r.task_execution_state)
    assert latest['object_key'] == old['object_key'] and latest['revision'] == old['revision'] + 1
    _, revised, item = co.load(r.task_execution_state, co.ContentReference.model_validate(ref(latest, 3)))
    assert revised.items[:2] == original.items[:2]
    assert item.options == {**original.items[2].options, 'resolution': '2K'}
    h._execute_tool_calls.assert_not_awaited()


def test_selected_content_cannot_be_forked_or_modify_unselected_slots(monkeypatch):
    from tests.test_request_catalog import catalog_case
    from services.task_execution import request_resolution as rr
    r, p, h, q, rows, _ = catalog_case(monkeypatch)
    rr.freeze(r, q, {'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': 'old-a',
        'revision': 1, 'item_indices': [1], 'source_message_id': rows[2]['message_id']}, focus='selected_target')
    assert rr.check(r, q, call(co.SAVE, {'document': {'title': '复制稿', 'items': []}}))[0] == 'REQUEST_TARGET_REVISION_REQUIRED'
    assert rr.check(r, q, call(co.SAVE, {'message_source': {}}))[0] == 'REQUEST_TARGET_REVISION_REQUIRED'
    args = {'base': {'artifact_id': 'old-a', 'item_index': 1},
            'updates': [{'item_index': 2, 'item': {'body': '不该修改的第二项'}}]}
    assert rr.check(r, q, call(co.SAVE, args))[0] == 'REQUEST_TARGET_ITEM_SELECTION_MISMATCH'
    args['updates'][0]['item_index'] = 1
    assert rr.check(r, q, call(co.SAVE, args)) is None
