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
