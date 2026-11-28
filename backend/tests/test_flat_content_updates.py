"""The model has one flat update contract; native saves keep their typed patches."""
import copy
import json

import pytest
from jsonschema import Draft202012Validator

from services.task_execution import content_objects as co
from services.task_execution.content_update_wire import project, public_error
from services.task_execution.image_reference_catalog import ImageReferences
from services.task_execution.reference_boundary import ModelReferenceBoundary
from tests.test_content_objects import registered, ref, observed_revision
from tests.test_model_directed_loop import execute
from tests.test_task_execution_model_bridge import call as raw_call


@pytest.mark.parametrize('reference', [False, True])
def test_flat_contract_examples_roundtrip_without_mutating_native_schema(reference):
    from tests.test_save_contract_failure_paths import contract
    refs, native, public = contract(reference, revise_saved=True)
    before = copy.deepcopy(native)
    node = public['parameters']['properties']['updates']['items']
    assert 'item_index' in node['properties'] and 'item' not in node['properties']
    assert node['minProperties'] == 2
    assert project(public) == public
    for example in public['parameters']['examples']:
        Draft202012Validator(public['parameters']).validate(example)
        assert all('item' not in update for update in example.get('updates', ()))
        co.SaveContent.model_validate(refs.decode_call(co.SAVE, example))
    assert native == before
    good = copy.deepcopy(public['parameters']['examples'][0])
    good['updates'] = [{'item_index': 3}]
    assert not Draft202012Validator(public['parameters']).is_valid(good)
    good['updates'] = [{'item_index': 3, 'item': {'body': '旧包装不能混用'}}]
    assert not Draft202012Validator(public['parameters']).is_valid(good)
    with pytest.raises(ValueError):
        refs.decode_call(co.SAVE, good)


async def test_flat_multi_item_save_without_image_catalog_preserves_unmodified_item():
    r, p, h, q, row = await registered()
    old = co.load(r.task_execution_state, ref(row, 1))[1]
    observed_revision(r, q, row, [1, 3])
    args = {'base': ref(row, 1), 'updates': [
        {'item_index': 1, 'body': '第一项的新完整正文。'},
        {'item_index': 3, 'body': '第三项的新完整正文。'}]}
    await execute(r, p, h, q, [raw_call(co.SAVE, args, 'flat-multi')])
    assert json.loads(p.messages[-1]['content'])['ok']
    latest, = co.catalog(r.task_execution_state)
    doc = co.load(r.task_execution_state, ref(latest, 1))[1]
    assert latest['object_key'] == row['object_key'] and latest['revision'] == row['revision'] + 1
    assert doc.items[0].body == args['updates'][0]['body'] and doc.items[2].body == args['updates'][1]['body']
    assert doc.items[1] == old.items[1]
    assert doc.items[0].materials == old.items[0].materials and doc.items[2].options == old.items[2].options


async def test_fresh_model_nested_update_is_rejected_without_adopting_or_saving():
    r, p, h, q, row = await registered()
    before = copy.deepcopy(r.task_execution_state)
    observed_revision(r, q, row, [3])
    bad = raw_call(co.SAVE, {'base': ref(row, 3), 'updates': [
        {'item_index': 3, 'item': {'body': '旧结构'}}]}, 'reject-wrapper')
    await execute(r, p, h, q, [bad])
    result = json.loads(p.messages[-1]['content'])
    assert not result['ok'] and result['submission_state'] == 'not_dispatched'
    assert any(i['path'] == '$.updates[0].item' for i in result['issues'])
    assert r.task_execution_state == before
    h._execute_tool_calls.assert_not_awaited()


def test_native_error_paths_become_flat_but_unknown_wrapper_stays_identifiable():
    value = {'issues': [{'path': '$.updates[0].item.body'}, {'loc': ['updates', 2, 'item', 'options']},
                        {'path': '$.updates[0].item'}]}
    projected = public_error(value)
    assert projected['issues'] == [{'path': '$.updates[0].body'}, {'loc': ['updates', 2, 'options']},
                                   {'path': '$.updates[0].item'}]
    assert value['issues'][0]['path'] == '$.updates[0].item.body'


def test_no_catalog_final_schema_resolves_refs_and_exposes_only_flat_updates():
    original = copy.deepcopy(co.SCHEMA)
    visible = ModelReferenceBoundary(None).schema(original)['function']
    params = visible['parameters']
    assert 'item' not in params['properties']['updates']['items']['properties']
    assert 'item' not in params['$defs']['ItemUpdate']['properties']
    for example in params['examples']:
        Draft202012Validator(params).validate(example)
        co.SaveContent.model_validate(ModelReferenceBoundary(None).arguments(example, tool=co.SAVE))
    assert co.SCHEMA == original


def test_verified_historical_native_revision_is_displayed_flat_without_rewriting_record():
    refs = ImageReferences({'input_message_id': 'current', 'saved_groups': [{
        'artifact_id': 'known', 'revision': 1, 'presentation_profile': 'reference-prompts-v1',
        'items': [{'item_index': 3, 'title': '旧稿'}]}]})
    refs.contents.profiles['known'] = 'reference-prompts-v1'
    arguments = {'base': {'artifact_id': 'known', 'item_index': 3}, 'updates': [{'item_index': 3, 'item': {
        'body': '## 完整生图提示词\n\n米白笔记本。\n\n## 负面提示词\n\n禁止水印。'}}]}
    messages = [{'role': 'assistant', 'tool_calls': [{'id': 'past', 'function': {
        'name': co.SAVE, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}]
    before = copy.deepcopy(messages)
    shown = json.loads(refs.messages(messages)[0]['tool_calls'][0]['function']['arguments'])
    assert 'prompt' in shown['updates'][0] and 'item' not in shown['updates'][0]
    assert messages == before
