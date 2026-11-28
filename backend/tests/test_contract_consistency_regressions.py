"""Failure-path contracts from the customer's actual conversation sequence."""
import copy
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from schemas.message import TextPart
from services.task_execution import content_objects as co, request_resolution as rr
from services.task_execution import content_revision, item_confirmation
from services.task_execution.image_reference_catalog import ImageReferences, for_runtime
from services.task_execution.reference_prompt_format import encode_item, MODEL_GUIDANCE
from services.task_execution.inline_image_action import SingleImageEdit
from services.handlers.chat import content_tools
from services.tools.catalog import build_tool_catalog
from services.tools.model_contracts import project_model_tool
from services.tools.argument_validation import _issues
from tests.test_content_objects import registered, document, ref
from tests.test_content_revision_contract import configure_reference, read
from tests.test_model_directed_loop import case, execute
from tests.model_update_fixtures import call, model_arguments
from tests.semantic_fixtures import semantic_request
from tests.test_reference_prompt_wire_contract import public_first_tool


@pytest.mark.asyncio
async def test_customer_approval_requires_revision_then_partial_sections_preserve_old_text(monkeypatch):
    r, p, h, q = case()
    p.content_controls_available = True
    await execute(r, p, h, q, [call(co.SAVE, {'document': document(),
        'confirmation': {'tool': 'generate_image', 'wait_all': True}}, 'waiting')])
    old = configure_reference(r, co.catalog(r.task_execution_state)[0])
    original_state = r.task_execution_state
    original = co.load(original_state, ref(old, 3))[1]
    r.checkpoint_base[rr.CONTEXT] = {'input_message_id': str(uuid4()), 'requests': [], 'image_reference_version': 1}
    q = semantic_request(q, 'execute_saved', size={'aspect_ratio': '1:1', 'resolution': '1K'},
        media={'tool': 'generate_image', 'approved': [3], 'confirmation_only': False,
               'artifact_id': old['artifact_id'], 'revision': 1})
    q = replace(q, content=[TextPart(text='第三个做成一张图，正方形，一千就行。')])
    rr.freeze(r, q, {'kind': 'content', 'basis': 'explicit_reference',
        'artifact_id': old['artifact_id'], 'revision': 1, 'item_indices': [3]})
    refs = for_runtime(r)
    await execute(r, p, h, q, [call('generate_image', {'content_source': refs.encode(
        {'artifact_id': old['artifact_id'], 'item_index': 3})}, 'premature')])
    result = json.loads(p.messages[-1]['content'])
    assert result['code'] == 'CONTENT_IMAGE_REVISION_REQUIRED'
    assert '已批准' in result['message'] and '不是等待' in result['message']
    refs = for_runtime(r)
    refs.reference_format = True
    visible_rejection = json.loads(refs.text(json.dumps(result, ensure_ascii=False)))
    assert '修订prompt、presentation' in visible_rejection['message']
    assert '新artifact_id' not in visible_rejection['message']
    assert result['recovery']['steps'] == ['read_saved_item', 'save_revision', 'execute_saved_revision']
    assert r.task_execution_state == original_state
    h._execute_tool_calls.assert_not_awaited()
    monkeypatch.setattr(content_tools, 'check_source_scope', lambda *args: None)
    visible = refs.encode(await read(r, p, h, q, old, 3))
    old_sections = encode_item(original.items[2].model_dump(mode='json'))
    delta = {'base': visible['content_source'], 'updates': [{'item_index': 3, 'item': {
        'options': {**original.items[2].options, 'aspect_ratio': '1:1', 'resolution': '1K'},
        'presentation': {'visual_settings': '正方形1:1、1K，其他视觉细节保留。'},
        'prompt': {'positive': old_sections['prompt']['positive'] + '\n输出正方形1:1，1K。'}}}],
        'continue_execution': True}
    p.content_visible_tools = {co.SAVE: content_tools.model_tools([co.save_schema(r)],
        revise_saved=True, saved_text_available=True)[0]['function']}
    public = refs.schema({'function': p.content_visible_tools[co.SAVE]})['function']
    delta = model_arguments(delta)
    Draft202012Validator(public['parameters']).validate(delta)
    await execute(r, p, h, q, [call(co.SAVE, delta, 'patch')])
    result = json.loads(p.messages[-1]['content'])
    assert result['ok'], result
    assert result['approved_execution']['item_indices'] == [3]
    latest, = co.catalog(r.task_execution_state)
    updated = co.load(r.task_execution_state, ref(latest, 3))[1]
    sections = encode_item(updated.items[2].model_dump(mode='json'))
    assert latest['revision'] == 2 and latest['object_key'] == old['object_key']
    assert updated.items[:2] == original.items[:2]
    assert sections['presentation']['scheme_content'] == old_sections['presentation']['scheme_content']
    assert sections['presentation']['reference_usage'] == old_sections['presentation']['reference_usage']
    assert sections['prompt']['negative'] == old_sections['prompt']['negative']
    assert updated.items[2].materials == original.items[2].materials
    approval = item_confirmation.directive(q, r)
    assert approval.confirmation_only and approval.artifact_id == latest['artifact_id']
    assert item_confirmation.check(call('generate_image', {'content_source': ref(latest, 3)}),
        r.task_execution_state, '', semantic=approval) is None
    assert item_confirmation.check(call('generate_image', {'content_source': ref(latest, 1)}),
        r.task_execution_state, '', semantic=approval)[0] == 'CONTENT_CONFIRMATION_DIFFERENT_ITEM'


@pytest.mark.asyncio
async def test_unread_partial_update_is_not_merged_or_saved():
    r, p, h, q, old = await registered()
    old = configure_reference(r, old)
    r.checkpoint_base.pop(content_revision.KEY, None)
    r.checkpoint_base[rr.CONTEXT] = {'input_message_id': str(uuid4()), 'requests': [], 'image_reference_version': 1}
    rr.freeze(r, q, {'kind': 'content', 'basis': 'explicit_reference',
        'artifact_id': old['artifact_id'], 'revision': 1, 'item_indices': [3]})
    before = r.task_execution_state
    args = {'base': for_runtime(r).encode({'artifact_id': old['artifact_id'], 'item_index': 3}),
        'updates': [{'item_index': 3, 'item': {'presentation': {'visual_settings': '改成浅蓝。'}}}]}
    await execute(r, p, h, q, [call(co.SAVE, args, 'unread-patch')])
    assert json.loads(p.messages[-1]['content'])['code'] == 'CONTENT_READ_REQUIRED'
    assert r.task_execution_state == before
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.parametrize('loc', [[], ['base'], ['document', 'items']])
def test_fresh_json_error_cannot_teach_unavailable_revision_fields(loc):
    refs = ImageReferences({'input_message_id': str(uuid4())})
    offered = public_first_tool(refs)
    result = refs.save_repair({'message': '参数错误。', 'issues': [{'loc': loc}]}, offered)
    assert set(result['parameters']['properties']).isdisjoint({'base', 'updates'})
    assert result['allowed_forms'] == [['document']]
    assert 'document' in result['required_fields']
    assert 'base' not in result['required_fields'] and 'updates[]' not in result['required_fields']
    for example in result['parameters']['examples']:
        Draft202012Validator(result['parameters']).validate(example)


@pytest.mark.parametrize('key', ['resource_ref', 'file_id', 'asset_id'])
def test_resource_backed_edit_roundtrips_with_one_public_image_ref(key):
    refs = ImageReferences({'input_message_id': str(uuid4()), '_delivered_image_changes': [
        {'name': '追加图.jpg', 'reference': {key: 'registered-resource'}}]})
    handle = next(iter(refs.references))
    arguments = {'image_ref': handle, 'edit_instruction': '背景换成浅蓝色，本子别改。'}
    schema = refs.schema(SingleImageEdit.model_json_schema())
    assert 'source_reference' not in schema['properties']
    Draft202012Validator(schema).validate(arguments)
    decoded = refs.decode_call('generate_image', arguments)
    edit = SingleImageEdit.model_validate(decoded)
    assert edit.content_item().materials[0].reference.model_dump(exclude_none=True) == {key: 'registered-resource'}
    assert refs.encode(decoded) == arguments
    # Message-only view/target contracts must not advertise unsupported resources.
    from services.task_execution.content_objects import ImageMessageReference
    view = refs.schema(ImageMessageReference.model_json_schema())
    assert not Draft202012Validator(view).is_valid({'image_ref': handle})
    from services.handlers.chat.content_tools import READ_SCHEMA
    public_read = refs.schema(READ_SCHEMA)['function']
    assert handle not in public_read['description']
    public_target = refs.schema(rr.selection_schema())['function']
    for example in public_target['parameters'].get('examples', []):
        Draft202012Validator(public_target['parameters']).validate(example)


@pytest.mark.parametrize(('name', 'args', 'valid'), [
    ('file_delete', {}, False), ('file_delete', {'files': []}, False),
    ('file_delete', {'file_ids': 'wrong'}, False), ('file_delete', {'file_ids': ['wrong']}, False),
    ('file_delete', {'file_ids': 'fid_a3f2b1c9'}, True),
    ('file_delete', {'file_ids': [], 'files': ['客户文件.csv']}, True),
    ('file_analyze', {}, False), ('file_analyze', {'file_id': 'wrong'}, False),
    ('file_analyze', {'file_id': 'fid_a3f2b1c9'}, True),
    ('file_analyze', {'file_id': 'fid_a3f2b1c9', 'path': '客户文件.csv'}, True),
])
def test_file_selector_model_and_runtime_shape_checks_agree(name, args, valid):
    native = build_tool_catalog().require(name).to_schema()
    public = project_model_tool(native)['function']['parameters']
    assert Draft202012Validator(public).is_valid(args) is valid
    assert (not _issues(args, public, image_hints=False)) is valid
    assert native['function']['parameters']['properties'] == public['properties']
