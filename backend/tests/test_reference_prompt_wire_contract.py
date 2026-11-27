"""Customer regressions: complete sections, lossless old reads and exact draft identities."""
import copy
import json
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from services.task_execution import content_objects as co, request_resolution as rr
from services.task_execution.image_reference_catalog import ImageReferences, for_runtime
from services.task_execution.reference_prompt_format import (
    DISPLAY_FIELDS, PROMPT_FIELDS, MODEL_GUIDANCE, FormatArgumentsError, decode_save,
)
from services.task_execution.content_reference_catalog import ContentReferenceError
from services.task_execution.methods import presentation_issues, REFERENCE_PROMPTS_PRESENTATION
from services.handlers.chat import content_tools
from tests.test_content_objects import registered, ref
from tests.test_content_revision_contract import configure_reference, read
from tests.test_model_directed_loop import execute
from tests.test_task_execution_model_bridge import call


def refs_with_drafts():
    groups = [{'artifact_id': 'content-real-version-' + str(n), 'revision': n,
               'items': [{'item_index': 3, 'title': '方案3｜蓝白灯塔'}]} for n in (1, 2)]
    return ImageReferences({'input_message_id': str(uuid4()), 'saved_groups': groups}), groups


def public_first_tool(refs):
    tool = copy.deepcopy(co.SCHEMA)
    tool['function'].update(parameters=co.save_parameters(require_presentation=True),
                            description=REFERENCE_PROMPTS_PRESENTATION + tool['function']['description'])
    return refs.tools(content_tools.model_tools([tool], first_delivery=True))[0]['function']


def test_model_examples_and_backend_render_the_same_complete_six_sections():
    refs, _ = refs_with_drafts()
    tool = public_first_tool(refs)
    example = tool['parameters']['examples'][0]
    Draft202012Validator(tool['parameters']).validate(example)
    item = example['document']['items'][0]
    assert set(item['presentation']) == set(DISPLAY_FIELDS)
    assert set(item['prompt']) == set(PROMPT_FIELDS)
    assert 'body' not in item and 'display_prefix' not in item
    doc = co.SaveContent.model_validate(refs.decode_call(co.SAVE, example)).document
    assert not presentation_issues(doc, 'reference-prompts-v1')
    rendered = co.render(doc)
    for heading in ('参考图判断', '方案内容', '视觉设定', '参考图使用方式', '完整生图提示词', '负面提示词'):
        assert rendered.count(heading) == 1
    assert doc.items[0].body.endswith(item['prompt']['negative'])
    assert 'scheme_content' not in rendered
    assert tool['description'].startswith(MODEL_GUIDANCE)


def test_missing_section_and_duplicate_native_fields_are_rejected_at_public_path():
    refs, _ = refs_with_drafts()
    tool = public_first_tool(refs)
    value = copy.deepcopy(tool['parameters']['examples'][0])
    del value['document']['items'][0]['prompt']['negative']
    assert not Draft202012Validator(tool['parameters']).is_valid(value)
    with pytest.raises(FormatArgumentsError) as caught:
        refs.decode_call(co.SAVE, value)
    assert caught.value.issues[0]['path'] == '$.document.items[0].prompt.negative'
    value = copy.deepcopy(tool['parameters']['examples'][0])
    value['document']['items'][0]['body'] = '不能覆盖结构化正文'
    with pytest.raises(FormatArgumentsError):
        refs.decode_call(co.SAVE, value)


def test_draft_typo_stale_input_and_conflicting_identity_never_choose_another_draft():
    refs, groups = refs_with_drafts()
    short = refs.encode({'artifact_id': groups[1]['artifact_id'], 'item_index': 3})
    assert refs.decode(short) == {'artifact_id': groups[1]['artifact_id'], 'item_index': 3}
    assert refs.encode({'artifact_id': groups[0]['artifact_id']}) != refs.encode({'artifact_id': groups[1]['artifact_id']})
    for bad in ({**short, 'content_ref': short['content_ref'] + 'x'},
                {**short, 'artifact_id': groups[1]['artifact_id']}):
        with pytest.raises(ContentReferenceError) as caught:
            refs.decode({'base': bad})
        repair = refs.contents.repair(caught.value)
        assert repair['issues'][0]['path'] == '$.base.content_ref'
        assert repair['submission_state'] == 'not_dispatched'
        assert set(repair['recovery']['allowed_content_refs']) == set(refs.contents.references)
    other = ImageReferences({'input_message_id': str(uuid4()), 'saved_groups': groups})
    with pytest.raises(ContentReferenceError):
        other.decode(short)


@pytest.mark.asyncio
async def test_old_draft_read_short_save_and_execution_preserve_other_items_and_original_body(monkeypatch):
    r, p, h, q, row = await registered()
    row = configure_reference(r, row)
    r.checkpoint_base[rr.CONTEXT] = {'input_message_id': str(uuid4()), 'requests': [], 'image_reference_version': 1}
    # No active Skill: the saved object's profile owns the public contract.
    r.checkpoint_base[rr.KEY] = {'target': {'kind': 'content', 'artifact_id': row['artifact_id'], 'revision': 1}}
    monkeypatch.setattr(content_tools, 'check_source_scope', lambda *args: None)
    refs = for_runtime(r)
    selected = refs.encode({'artifact_id': row['artifact_id'], 'item_index': 3})
    receipt = await read(r, p, h, q, row, 3)
    visible = refs.encode(receipt)
    assert visible['content_source']['content_ref'] == selected['content_ref']
    assert set(visible['item']['presentation']) == set(DISPLAY_FIELDS)
    assert 'body' not in visible['item'] and 'display_prefix' not in visible['item']
    assert 'sha256' not in visible['content_source'] and visible['content_sha256']
    Draft202012Validator(refs.schema(content_tools.READ_SCHEMA)['function']['parameters']).validate(
        {'content_source': visible['content_source']})
    # Successful historical calls are presented in the current wire format;
    # neither persisted call arguments nor the stored draft are rewritten.
    original = co.objects(r.task_execution_state)[0]
    record = co.ContentRecord.model_validate_json(original.inline_text)
    history = [{'role': 'assistant', 'content': None, 'tool_calls': [{'id': original.source_call_id,
        'type': 'function', 'function': {'name': co.SAVE, 'arguments': json.dumps({
            'document': record.document.model_dump(mode='json')}, ensure_ascii=False)}}]}]
    unchanged = copy.deepcopy(history)
    projected_history = refs.messages(history)
    historical_args = json.loads(projected_history[0]['tool_calls'][0]['function']['arguments'])
    assert 'reference_analysis' in historical_args['document']
    assert 'presentation' in historical_args['document']['items'][0]
    assert 'prompt' in historical_args['document']['items'][0]
    assert history == unchanged
    # Read proof remains server-observed; making a handle is not a read.
    before = r.task_execution_state
    update = {'base': selected, 'updates': [{'item_index': 3, 'item': {'options': {'aspect_ratio': '1:1', 'resolution': '2K'}}}],
              'continue_execution': False}
    public_save = refs.tools(content_tools.model_tools([co.save_schema(r)], revise_saved=True, saved_text_available=True))[0]['function']
    Draft202012Validator(public_save['parameters']).validate(update)
    p.content_visible_tools = {co.SAVE: content_tools.model_tools([co.save_schema(r)], revise_saved=True,
                                                                saved_text_available=True)[0]['function']}
    rr.freeze(r, q, {'kind': 'content', 'basis': 'explicit_reference',
        'artifact_id': row['artifact_id'], 'revision': 1, 'item_indices': [3]})
    await execute(r, p, h, q, [call(co.SAVE, update, 'wire-update-options')])
    result = json.loads(p.messages[-1]['content'])
    assert result['ok'], result
    latest, = co.catalog(r.task_execution_state)
    old_document = co.load(before, ref(row, 3))[1]
    document = co.load(r.task_execution_state, ref(latest, 3))[1]
    assert latest['revision'] == 2 and latest['object_key'] == row['object_key']
    assert document.items[:2] == old_document.items[:2]
    assert document.items[2].body == old_document.items[2].body
    assert document.items[2].display_prefix == old_document.items[2].display_prefix
    assert document.items[2].materials == old_document.items[2].materials
    args, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, ref(latest, 3))
    assert args['prompt'] == old_document.items[2].body
    h._execute_tool_calls.assert_not_awaited()
    # A second edit reads the new version and sends only structured sections.
    rr.freeze(r, q, {'kind': 'content', 'basis': 'explicit_reference',
        'artifact_id': latest['artifact_id'], 'revision': 2, 'item_indices': [3]})
    refs = for_runtime(r)
    with pytest.raises(ContentReferenceError):
        refs.decode(selected)
    receipt = refs.encode(await read(r, p, h, q, latest, 3))
    presentation = copy.deepcopy(receipt['item']['presentation'])
    presentation['visual_settings'] += '\n保持主体原位置，留白更干净。'
    prompt = copy.deepcopy(receipt['item']['prompt'])
    prompt['positive'] += '\n保持主体原位置，留白更干净。'
    before_second = r.task_execution_state
    await execute(r, p, h, q, [call(co.SAVE, {'base': receipt['content_source'], 'updates': [
        {'item_index': 3, 'item': {'presentation': presentation, 'prompt': prompt}}],
        'continue_execution': False}, 'wire-sections')])
    result = json.loads(p.messages[-1]['content'])
    assert result['ok'], result
    final, = co.catalog(r.task_execution_state)
    final_document = co.load(r.task_execution_state, ref(final, 3))[1]
    assert final['revision'] == 3 and final['object_key'] == row['object_key']
    assert final_document.items[:2] == co.load(before_second, ref(latest, 3))[1].items[:2]
    assert not presentation_issues(final_document, 'reference-prompts-v1')
    assert final_document.items[2].body.count('## 完整生图提示词') == 1
    assert final_document.items[2].body.count('## 负面提示词') == 1
    assert final_document.items[2].options == document.items[2].options
    h._execute_tool_calls.assert_not_awaited()


def test_revision_prompt_is_rendered_with_existing_title_without_resending_presentation():
    value = {'base': {'artifact_id': 'real', 'item_index': 3}, 'updates': [{'item_index': 3,
        'item': {'prompt': {'positive': '原有灯塔图案保持不变，只把桌布换成浅蓝。', 'negative': '不要改变文字与其他物体。'}}}]}
    decoded = decode_save(value, lambda *_: '方案3｜灯塔')
    patch = decoded['updates'][0]['item']
    assert set(patch) == {'body'}
    assert patch['body'] == '## 完整生图提示词\n\n原有灯塔图案保持不变，只把桌布换成浅蓝。\n\n## 负面提示词\n\n不要改变文字与其他物体。'


@pytest.mark.asyncio
async def test_typo_and_unread_draft_are_rejected_before_saving_or_dispatching():
    from services.task_execution.content_revision import KEY
    r, p, h, q, row = await registered()
    # This draft was delivered by an earlier turn, as in the customer failure.
    r.task_execution_state = r.task_execution_state.model_copy(update={'artifacts': tuple(
        a.model_copy(update={'source_task_id': uuid4()}) for a in r.task_execution_state.artifacts)})
    r.checkpoint_base.pop(KEY, None)
    r.checkpoint_base[rr.CONTEXT] = {'input_message_id': str(uuid4()), 'requests': [], 'image_reference_version': 1}
    rr.freeze(r, q, {'kind': 'content', 'basis': 'explicit_reference',
        'artifact_id': row['artifact_id'], 'revision': 1, 'item_indices': [3]})
    refs = for_runtime(r)
    selected = refs.encode({'artifact_id': row['artifact_id'], 'item_index': 3})
    before = r.task_execution_state
    args = {'base': {**selected, 'content_ref': selected['content_ref'] + 'x'},
            'updates': [{'item_index': 3, 'item': {'body': '尚未读取，不能保存。'}}]}
    await execute(r, p, h, q, [call(co.SAVE, args, 'typo')])
    result = json.loads(p.messages[-1]['content'])
    assert result['code'] == 'CONTENT_REFERENCE_UNKNOWN'
    assert result['issues'][0]['path'] == '$.base.content_ref'
    assert result['submission_state'] == 'not_dispatched'
    assert r.task_execution_state == before
    args['base'] = {**selected, 'item_index': 1}
    await execute(r, p, h, q, [call(co.SAVE, args, 'wrong-item')])
    result = json.loads(p.messages[-1]['content'])
    assert result['code'] == 'REQUEST_TARGET_CONTENT_MISMATCH'
    assert result['issues'][0]['path'] == '$.base.item_index'
    assert result['recovery']['expected_target']['content_ref'] == selected['content_ref']
    assert r.task_execution_state == before
    args['base'] = selected
    await execute(r, p, h, q, [call(co.SAVE, args, 'unread')])
    result = json.loads(p.messages[-1]['content'])
    assert result['code'] == 'CONTENT_READ_REQUIRED'
    assert r.task_execution_state == before
    h._execute_tool_calls.assert_not_awaited()


def test_nullable_noncontent_targets_and_confirmation_do_not_require_selecting_a_draft():
    refs, _ = refs_with_drafts()
    assert refs.decode({'target': {'kind': 'image', 'content_ref': None}})['target']['artifact_id'] is None
    assert refs.decode({'confirmation': {'tool': 'generate_image', 'content_ref': None}})['confirmation']['artifact_id'] is None
    with pytest.raises(ContentReferenceError):
        refs.decode({'base': {'content_ref': None, 'item_index': 3}})


def test_selection_and_read_examples_use_the_same_draft_identity_protocol():
    refs, groups = refs_with_drafts()
    function = refs.tools([rr.selection_schema()])[0]['function']
    examples = function['parameters']['examples']
    for example in examples:
        Draft202012Validator(function['parameters']).validate(example)
        target = example['request_context']['target']
        assert 'artifact_id' not in target and target['content_ref'] in refs.contents.available()
        assert refs.decode(example)['request_context']['target']['artifact_id'] in {g['artifact_id'] for g in groups}
    assert json.dumps(examples, ensure_ascii=False) in function['description']
    assert '"artifact_id"' not in function['description']


@pytest.mark.asyncio
@pytest.mark.parametrize('restored', [False, True])
async def test_public_reference_schema_is_enforced_before_native_save(restored):
    r, p, h, q, row = await registered()
    row = configure_reference(r, row)
    r.checkpoint_base[rr.CONTEXT] = {'input_message_id': str(uuid4()), 'requests': [], 'image_reference_version': 1}
    rr.freeze(r, q, {'kind': 'content', 'basis': 'explicit_reference',
        'artifact_id': row['artifact_id'], 'revision': 1, 'item_indices': [3]})
    p.content_visible_tools = {co.SAVE: content_tools.model_tools([co.save_schema(r)], revise_saved=True,
                                                                saved_text_available=True)[0]['function']}
    refs = for_runtime(r)
    native_shape = {'base': refs.encode({'artifact_id': row['artifact_id'], 'item_index': 3}),
        'updates': [{'item_index': 3, 'item': {'body': '## 完整生图提示词\n正文完整。\n\n## 负面提示词\n禁止水印。'}}]}
    before = r.task_execution_state
    from services.handlers.chat.execution_engine import _execute_tools
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    await _execute_tools(runtime=r, prepared=p, handler=h, request=q,
        calls=[call(co.SAVE, native_shape, 'wrong-wire-shape')], blocks=[], turn=0, turn_text='',
        next_model_round=1, previewed_call_ids=set(), cancellation_event=r.cancellation_event,
        sink=CollectingExecutionSink(), native_content_restored=restored)
    result = json.loads(p.messages[-1]['content'])
    if restored:
        # Host-restored canonical calls are not fresh model JSON, but must
        # still pass the original server-observed read and scope checks.
        assert result['code'] == 'CONTENT_READ_REQUIRED'
    else:
        assert result['code'] == 'CONTENT_PRESENTATION_ARGUMENTS_INVALID'
        assert any(i['path'] == '$.updates[0].item.body' for i in result['issues'])
        patch = result['parameters']['properties']['updates']['items']['properties']['item']
        assert 'prompt' in patch['properties'] and 'body' not in patch['properties']
    assert r.task_execution_state == before
    h._execute_tool_calls.assert_not_awaited()
