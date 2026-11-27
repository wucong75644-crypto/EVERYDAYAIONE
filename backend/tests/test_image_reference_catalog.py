"""Short selections retain exact, version-scoped native image identities."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from services.task_execution import request_resolution as rr
from services.task_execution.image_reference_catalog import ImageReferences, for_runtime
from services.task_execution.contracts import TaskError
from services.task_execution.content_objects import MaterialReference
from services.task_execution.inline_image_action import SingleImageEdit
from services.handlers.chat.content_tools import READ_SCHEMA
from tests.test_request_catalog import catalog_case
from tests.test_model_directed_loop import execute
from tests.test_task_execution_model_bridge import call


def snapshot():
    a, b, result = [str(uuid4()) for _ in range(3)]
    return {'input_message_id': b, 'image_reference_version': 1, 'requests': [
        {'message_id': b, 'turn_number': 2, 'images': [{'name': 'B', 'reference': {'message_id': b, 'content_index': 3}}]},
        {'message_id': a, 'turn_number': 1, 'images': [{'name': 'A', 'reference': {'message_id': a, 'content_index': 1}}]}],
        'image_results': [{'source_message_id': result, 'content_index': 0, 'generated_for_message_id': a, 'generated_for_turn': 1}]}


def test_current_history_and_result_handles_roundtrip_without_renumbering_content():
    source = snapshot(); refs = ImageReferences(source)
    visible = refs.encode(source)
    assert visible['requests'][0]['turn_number'] == 2
    for row, projected in zip(source['requests'], visible['requests']):
        reference = projected['images'][0]['reference']
        assert set(reference) == {'image_ref'}
        assert refs.decode(reference) == row['images'][0]['reference']
    generated = visible['image_results'][0]
    canonical = refs.decode({'kind': 'image', 'basis': 'explicit_reference', 'image_ref': generated['image_ref']})
    assert canonical['source_message_id'] == source['image_results'][0]['source_message_id']
    assert canonical['content_index'] == 0
    assert generated['generated_for_turn'] == 1


@pytest.mark.parametrize('change', ['new_input', 'new_image', 'edited_input'])
def test_stale_handle_never_rebinds_same_number_to_another_input(change):
    old = snapshot(); old_refs = ImageReferences(old); handle = next(iter(old_refs.references))
    new = copy.deepcopy(old)
    if change == 'new_input': new['input_message_id'] = str(uuid4())
    elif change == 'new_image': new['requests'][0]['images'][0]['reference']['content_index'] = 9
    else: new['requests'][0]['source_sha256'] = 'changed'
    with pytest.raises(TaskError, match='IMAGE_REFERENCE_UNKNOWN_OR_STALE'):
        ImageReferences(new).decode({'image_ref': handle})


@pytest.mark.parametrize('selector', ['message_id', 'content_index', 'source_message_id', 'source_content_index', 'file_id', 'asset_id', 'resource_ref'])
def test_conflicting_second_identity_is_not_silently_ignored(selector):
    refs = ImageReferences(snapshot()); handle = next(iter(refs.references))
    with pytest.raises(TaskError, match='IMAGE_REFERENCE_IDENTITY_CONFLICT'):
        refs.decode({'image_ref': handle, selector: 0 if 'index' in selector else str(uuid4())})


@pytest.mark.parametrize('schema,argument', [
    (SingleImageEdit.model_json_schema(), {'edit_instruction': '保留文字，只改背景'}),
    (MaterialReference.model_json_schema(), {}),
])
def test_public_edit_and_material_schemas_accept_short_reference_and_restore_native(schema, argument):
    refs = ImageReferences(snapshot()); handle = next(iter(refs.references))
    public = refs.schema(schema); value = {**argument, 'image_ref': handle}
    Draft202012Validator.check_schema(public)
    Draft202012Validator(public).validate(value)
    native = refs.decode(value)
    (SingleImageEdit if argument else MaterialReference).model_validate(native)
    assert 'image_ref' not in native
    assert 'source_content_index' not in public['properties'] and 'content_index' not in public['properties']


@pytest.mark.parametrize('reference', [{}, {'image_ref': None}, {'asset_id': None}, {'message_id': 'old', 'content_index': 1}])
def test_short_material_schema_rejects_empty_null_and_legacy_image_locators(reference):
    refs = ImageReferences(snapshot())
    assert not Draft202012Validator(refs.schema(MaterialReference.model_json_schema())).is_valid(reference)


@pytest.mark.parametrize('key', ['resource_ref', 'file_id', 'asset_id'])
def test_non_image_material_alternatives_keep_their_original_required_identity(key):
    refs = ImageReferences(snapshot()); validator = Draft202012Validator(refs.schema(MaterialReference.model_json_schema()))
    assert validator.is_valid({key: 'verified-file'})
    assert not validator.is_valid({key: 'verified-file', 'image_ref': next(iter(refs.references))})


def test_read_and_correction_use_same_handle_without_changing_request_or_saved_identity(monkeypatch):
    r, _, _, _, rows, _ = catalog_case(monkeypatch)
    rr.context(r)['image_reference_version'] = 1
    refs = for_runtime(r); handle = next(iter(refs.references))
    public = refs.schema(rr.selection_schema(r))['function']['parameters']
    value = {'intent': 'edit', 'request_context': {'focus': 'current_request',
             'target': {'kind': 'image', 'basis': 'active_request', 'image_ref': handle}}}
    Draft202012Validator(public).validate(value)
    read = refs.schema(READ_SCHEMA)['function']['parameters']
    Draft202012Validator(read).validate({'show_image': {'image_ref': handle}})
    projected = rr.model_context(r)
    assert projected['recent_requests'][0]['message_id'] == rows[0]['message_id']
    assert refs.decode(projected['saved_groups'][0])['artifact_id'] == 'old-a'
    assert projected['saved_groups'][0]['source_request']['turn_number'] == 1
    assert projected['focused_request']['images'][0]['reference'] == {'image_ref': handle}


def test_checkpoint_without_surface_version_retains_original_uuid_contract():
    value = snapshot(); value.pop('image_reference_version')
    runtime = SimpleNamespace(checkpoint_base={rr.CONTEXT: value})
    assert for_runtime(runtime) is None
    assert value['requests'][0]['images'][0]['reference']['content_index'] == 3


def test_provider_save_schema_and_embedded_upload_example_use_same_contract():
    from services.task_execution import content_objects as co
    from services.handlers.chat.content_tools import model_tools
    from services.adapters.dashscope.responses import response_tools
    source = snapshot(); refs = ImageReferences(source)
    identity = source['requests'][0]['images'][0]['reference']
    image = SimpleNamespace(reference=MaterialReference.model_validate(identity))
    tools = model_tools([co.SCHEMA], first_delivery=True, current_images=[image], image_reference_catalog=refs)
    tool, = response_tools([refs.schema(t) for t in tools], ())
    example = json.loads(tool['description'].split('（仅按用户意图选择）：')[-1].split(' 新建参数')[0])
    assert example == [refs.encode(identity)]
    assert identity['message_id'] not in tool['description']
    assert 'content_index' not in tool['description']
    item = tool['parameters']['properties']['document']['properties']['items']['items']
    assert {'title', 'body'} <= set(item['required'])
    assert item['additionalProperties'] is False
    assert 'name' not in item['properties'] and 'heading' not in item['properties']
    Draft202012Validator.check_schema(tool['parameters'])
    authored = json.loads(tool['description'].split('素材另按目录绑定）：')[1].split('。items')[0])
    Draft202012Validator(tool['parameters']).validate(authored)
    authored['confirmation'] = {'tool': 'generate_image', 'wait_for': [1]}
    Draft202012Validator(tool['parameters']).validate(authored)


def test_history_recovery_repair_keeps_line_ranges_instead_of_new_document_advice():
    from services.task_execution import content_objects as co
    refs = ImageReferences(snapshot())
    result = refs.save_repair({'message': '校验失败。', 'issues': [
        {'loc': ['message_source', 'items', 0, 'first_line'], 'type': 'missing'}]}, co.SCHEMA['function'])
    assert result['required_fields'] == {'message_source': ['message_id'],
                                       'message_source.items[]': ['title', 'first_line', 'last_line']}
    assert 'document.items' not in result['message']
    assert '原操作形式' in result['message']


@pytest.mark.asyncio
@pytest.mark.parametrize('wrong_title', ['name', 'heading'])
async def test_save_repair_returns_exact_short_schema_without_renaming_invalid_fields(monkeypatch, wrong_title):
    from services.task_execution import content_objects as co
    r, p, h, q, rows, _ = catalog_case(monkeypatch)
    rr.context(r)['image_reference_version'] = 1
    rr.freeze(r, q, {'kind': 'request', 'basis': 'active_request', 'source_message_id': rows[1]['message_id']})
    p.content_controls_available = True
    p.content_visible_tools = {co.SAVE: co.SCHEMA['function']}
    refs = for_runtime(r)
    text = '完整原文，保持所有文字与限制。'
    args = {'document': {'title': '方案', 'items': [{wrong_title: '错误字段', 'body': text,
        'materials': [{'reference': refs.encode(rows[1]['images'][0]['reference']), 'role': '底图', 'usage': 'input'}]}]}}
    original = copy.deepcopy(args)
    await execute(r, p, h, q, [call(co.SAVE, args)])
    result = json.loads(refs.text(p.messages[-1]['content']))
    assert result['code'] == co.INVALID_OBJECT and result['ok'] is False
    assert result['parameters'] == refs.schema(co.SCHEMA['function']['parameters'])
    assert result['required_fields']['document.items[]'] == ['title', 'body']
    assert result['required_fields']['document.items[].materials[]'] == ['reference', 'role', 'usage']
    material = result['parameters']['properties']['document']['properties']['items']['items']['properties']['materials']['items']
    assert 'image_ref' in material['properties']['reference']['properties']
    assert args == original and r.task_execution_state is None
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.parametrize('value', [0, 2, 4, 6, None, '2'])
def test_inlining_preserves_reference_sibling_constraints(value):
    schema = {'$defs': {'Number': {'type': 'integer', 'minimum': 1}},
              '$ref': '#/$defs/Number', 'maximum': 4}
    assert Draft202012Validator(schema).is_valid(value) == Draft202012Validator(ImageReferences.inline_definitions(schema)).is_valid(value)


def test_delivered_additional_images_are_short_selectable_and_new_input_invalidates_old_handles():
    source = snapshot()
    event = SimpleNamespace(event_id=uuid4(), message_sha256='a' * 64, delivered=True, owner_task_id='current',
        attachments=[SimpleNamespace(kind='image', resource_ref='signed-append-reference', workspace_path='追加/B.png')])
    runtime = SimpleNamespace(task_id='current', checkpoint_base={rr.CONTEXT: source},
                              task_user_changes=SimpleNamespace(items=[event]))
    refs = for_runtime(runtime)
    extra = next(c for c in refs.candidates if c['provenance'] == 'delivered_attachment')
    assert extra['name'] == 'B.png'
    assert refs.decode({'reference': {'image_ref': extra['image_ref']}}) == {'reference': {'resource_ref': 'signed-append-reference'}}
    assert 'signed-append-reference' not in json.dumps(refs.candidates)
    event.message_sha256 = 'b' * 64
    with pytest.raises(TaskError, match='IMAGE_REFERENCE_UNKNOWN_OR_STALE'):
        for_runtime(runtime).decode({'image_ref': extra['image_ref']})


def test_alias_does_not_authorize_foreign_or_undelivered_attachments():
    source = snapshot()
    event = SimpleNamespace(event_id=uuid4(), message_sha256='a' * 64, delivered=False, owner_task_id='current',
        attachments=[SimpleNamespace(kind='image', resource_ref='unavailable', workspace_path='X.png')])
    runtime = SimpleNamespace(task_id='current', checkpoint_base={rr.CONTEXT: source}, task_user_changes=SimpleNamespace(items=[event]))
    before = for_runtime(runtime)
    assert len(before.references) == 3
    event.delivered = True; event.owner_task_id = 'foreign'
    assert for_runtime(runtime).references == before.references


def test_canvas_short_reference_uses_actual_current_image_order_and_rejects_history():
    refs = ImageReferences(snapshot()); handles = list(refs.references)
    assert refs.decode({'canvas_ref': handles[0]}) == {'canvas_image': 1}
    with pytest.raises(TaskError, match='IMAGE_CANVAS_REFERENCE_NOT_CURRENT'):
        refs.decode({'canvas_ref': handles[1]})
    with pytest.raises(TaskError, match='IMAGE_REFERENCE_IDENTITY_CONFLICT'):
        refs.decode({'canvas_ref': handles[0], 'canvas_image': 2})
    assert refs.decode({'canvas_ref': None}) == {}
    assert refs.decode({'image_ref': None, 'file_id': 'file-owned'}) == {'file_id': 'file-owned'}


@pytest.mark.asyncio
@pytest.mark.parametrize('short,repair_succeeds', [(False, False), (True, False), (True, True)])
async def test_only_new_short_schema_errors_get_one_bounded_read_only_repair(monkeypatch, short, repair_succeeds):
    from services.agent.image.ecommerce_planner import workflow
    failure = {'kind': 'unavailable', 'rejection': {'code': 'IMAGE_WORKFLOW_ARGUMENTS_INVALID', 'issues': [{'path': ['media']}]}}
    expected = {'kind': 'ordinary_image'} if repair_succeeds else failure
    model = AsyncMock(side_effect=[failure, expected])
    monkeypatch.setattr(workflow, 'classify_workflow', model)
    context = {'request_resolution': {'catalog_version': 2}}
    result = await workflow.classify_current_window('原始要求', context=context,
        image_references=ImageReferences(snapshot()) if short else None)
    assert result == expected
    assert model.await_count == (2 if short else 1)
    assert 'routing_parameter_correction' not in context
    if short:
        assert model.await_args.kwargs['context']['routing_parameter_correction'] == failure['rejection']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing_intent', 'conflicting_action'])
async def test_route_repairs_complete_json_and_action_without_guessing_user_words(monkeypatch, failure):
    from services.agent.image.ecommerce_planner import workflow
    from services.intent_router import IntentRouter
    source = snapshot(); source['catalog_version'] = 2
    good = {'kind': 'ordinary_image', 'mode': 'new', 'reuse_through_stage': 0,
        'intent': 'execute_saved', 'image_count': None, 'request_context': {
            'focus': 'selected_target', 'target': {'kind': 'content', 'basis': 'explicit_reference',
                'artifact_id': 'recipe', 'revision': 1, 'item_indices': [3], 'source_message_id': source['input_message_id']},
            'size_rules': [{'scope': 'items', 'item_indices': [3], 'size': {'aspect_ratio': '1:1', 'resolution': '1K'}}],
            'media': {'tool': 'generate_image', 'approved': [3], 'artifact_id': 'recipe', 'revision': 1}}}
    good['request_context'].update(size={}, platform=None, deliveries=[], materials=[], image_action=None)
    good['request_context']['media'].update(wait_for=[], wait_all=False, confirmation_only=False)
    bad = copy.deepcopy(good)
    if failure == 'missing_intent': bad.pop('intent')
    else: bad['intent'] = 'edit'
    calls = []
    async def transport(self, **kwargs):
        calls.append(kwargs)
        return {'choices': [{'message': {'tool_calls': [{'function': {'name': 'select_image_workflow',
            'arguments': json.dumps(refs.encode(bad if len(calls) == 1 else good))}}]}}]}
    monkeypatch.setattr(IntentRouter, 'call_tool_model', transport)
    groups = [{'artifact_id': 'recipe', 'revision': 1, 'confirmation_tool': 'generate_image',
               'items': [{'item_index': 3, 'awaiting_confirmation': True}]}]
    refs = ImageReferences({**source, 'saved_groups': groups})
    text = [{'type': 'text', 'text': '第三个做成一张图，正方形，一千就行。'}]
    result = await workflow.classify_current_window(text, context={'request_resolution': source, 'saved_groups': groups},
        image_references=refs)
    assert len(calls) == 2 and result.get('intent') == 'execute_saved', result
    assert result['request_context']['media']['approved'] == [3]
    assert json.loads(calls[0]['text'])['raw_user_input'] == json.loads(calls[1]['text'])['raw_user_input'] == text
    assert 'routing_parameter_correction' in json.loads(calls[1]['text'])['available_context']


def test_stored_canonical_calls_are_unchanged_and_duplicate_json_is_not_rewritten():
    # Decoding native checkpoint input is idempotent; strict raw JSON validation
    # stays upstream of alias conversion, including duplicate-key rejection.
    from services.tools.argument_validation import decode_tool_arguments
    refs = ImageReferences(snapshot()); canonical = next(iter(refs.references.values()))
    assert refs.decode(canonical) == canonical
    with pytest.raises(ValueError):
        decode_tool_arguments('{"image_ref":"first","image_ref":"second"}')


def test_wire_projection_does_not_modify_canonical_history_or_prompt_bodies():
    refs = ImageReferences(snapshot()); reference = next(iter(refs.references.values()))
    facts = {'reference': reference, 'body': json.dumps(reference)}
    messages = [{'role': 'system', 'content': 'Host facts\n' + json.dumps(facts)},
                {'role': 'user', 'content': json.dumps(facts)}]
    before = copy.deepcopy(messages); projected = refs.messages(messages)
    assert messages == before and projected[1] == before[1]
    result = json.loads(projected[0]['content'].split('\n')[-1])
    assert result['body'] == facts['body'] and set(result['reference']) == {'image_ref'}


def test_saved_options_are_data_even_when_they_resemble_a_schema():
    refs = ImageReferences(snapshot())
    value = {'document': {'options': {'properties': {'message_id': {'type': 'string'}, 'content_index': {'type': 'integer'}}}}}
    assert json.loads(refs.text(json.dumps(value))) == value


@pytest.mark.parametrize('plain_text', [False, True])
def test_only_verified_host_registered_text_drops_duplicate_model_save_protocol(plain_text):
    refs = ImageReferences(snapshot())
    tools = [{'type': 'function', 'function': {'name': name, 'parameters': {'type': 'object'}}}
             for name in ['save_task_text', 'read_conversation_text']]
    public = refs.tools(tools, plain_text_registration=plain_text)
    assert [t['function']['name'] for t in public] == (['read_conversation_text'] if plain_text else ['save_task_text', 'read_conversation_text'])
    assert tools[0]['function']['name'] == 'save_task_text'


@pytest.mark.asyncio
async def test_short_correction_reaches_existing_identity_guard_and_freezes_real_source(monkeypatch):
    from services.agent.image.ecommerce_planner.workflow import WorkflowBinding
    r, p, h, q, rows, _ = catalog_case(monkeypatch)
    rr.context(r)['image_reference_version'] = 1
    p.content_controls_available = True
    p.ecom_workflow = SimpleNamespace(value=WorkflowBinding(kind='ordinary_image', root_task_id=r.task_id,
        window_task_id=r.task_id, generation_run_id=str(uuid4()), intent='other'), save=AsyncMock())
    handle = next(iter(for_runtime(r).references))
    await execute(r, p, h, q, [call(rr.SELECT, {'intent': 'edit', 'request_context': {'focus': 'current_request',
        'target': {'kind': 'image', 'basis': 'active_request', 'image_ref': handle}}})])
    assert rr.binding(r)['target']['source_message_id'] == rows[1]['message_id']
    assert 'image_ref' not in rr.binding(r)['target']
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_short_reference_blocks_entire_batch_before_any_business_effect(monkeypatch):
    r, p, h, q, _, _ = catalog_case(monkeypatch)
    rr.context(r)['image_reference_version'] = 1
    blocks = await execute(r, p, h, q, [call('generate_image', {'image_ref': 'img_old_1', 'edit_instruction': '改背景'}),
        call('web_search', {'query': 'another operation'}, 'second')])
    assert all(b['status'] == 'error' for b in blocks)
    assert all(json.loads(b['output'])['submission_state'] == 'not_dispatched' for b in blocks)
    h._execute_tool_calls.assert_not_awaited()
    assert r.task_execution_state is None


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_reference', [False, True])
async def test_rejected_save_is_not_replayed_instead_of_asking_model_to_correct(monkeypatch, invalid_reference):
    from services.task_execution.content_delivery import KEY, input_revision, pending_response
    r, p, h, q, _, _ = catalog_case(monkeypatch)
    rr.context(r)['image_reference_version'] = 1
    p.content_controls_available = True
    args = {'document': {'title': '方案', 'items': [{'title': 'A', 'body': '完整正文'}]}}
    if invalid_reference:
        args['document']['items'][0]['materials'] = [{'reference': {'image_ref': 'expired'}, 'role': '底图', 'usage': 'input'}]
    save = call('save_task_text', args)
    r.checkpoint_base[KEY] = {'version': 1, 'phase': 'proposed', 'input_revision': input_revision(r, q),
                              'calls': [save], 'text': '', 'projection_source': 'tool'}
    assert pending_response(r, q) is not None
    await execute(r, p, h, q, [save])
    assert r.checkpoint_base[KEY]['phase'] == 'rejected'
    assert pending_response(r, q) is None
    assert json.loads(p.messages[-1]['content'])['ok'] is False
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('image_index', [0, 2])
async def test_router_short_reference_is_decoded_before_original_frame_validation(monkeypatch, image_index):
    from services.agent.image.ecommerce_planner.workflow import classify_workflow
    from services.intent_router import IntentRouter
    from core.config import get_settings
    source = snapshot(); refs = ImageReferences(source); handle = list(refs.references)[image_index]
    monkeypatch.setattr(get_settings(), 'intent_router_enabled', True)
    monkeypatch.setattr(get_settings(), 'dashscope_api_key', 'offline')
    answer = {'kind': 'ordinary_image', 'intent': 'edit', 'mode': 'new', 'reuse_through_stage': 0,
        'image_count': None, 'request_context': {'target': {'kind': 'image', 'basis': 'current_input', 'image_ref': handle},
            'focus': 'current_request', 'size': {}, 'materials': [{'reference': {'image_ref': handle}, 'role': '底图', 'usage': 'input'}]}}
    answer['request_context'].update(platform=None, deliveries=[], image_action=None,
        media={'tool': 'generate_image', 'wait_for': [], 'wait_all': False, 'approved': [], 'confirmation_only': False})
    # Preserve the existing supplier scalar/default normalization contract.
    answer['image_count'] = '1'
    answer['request_context'].pop('image_action')
    async def route(self, **kwargs):
        target = kwargs['tools'][0]['function']['parameters']['$defs']['Target']
        assert 'content_index' not in target['properties'] and 'image_ref' in target['properties']
        material = kwargs['tools'][0]['function']['parameters']['properties']['request_context']['properties']['materials']
        assert 'material_index' not in json.dumps(material)
        assert 'material_index' not in kwargs['text']
        return {'choices': [{'message': {'tool_calls': [{'function': {'name': 'select_image_workflow', 'arguments': json.dumps(answer)}}]}}]}
    monkeypatch.setattr(IntentRouter, 'call_tool_model', route)
    result = await classify_workflow([], context={'request_resolution': {'catalog_version': 2,
        'image_results': source['image_results']}}, image_references=refs)
    assert 'request_context' in result, result
    assert result['image_count'] is None  # Editing uses its existing single-image contract.
    target = result['request_context']['target']
    expected = refs.references[handle]
    assert target['source_message_id'] == expected['message_id'] and target['content_index'] == expected['content_index']
    assert result['request_context']['materials'][0]['reference']['message_id'] == expected['message_id']
