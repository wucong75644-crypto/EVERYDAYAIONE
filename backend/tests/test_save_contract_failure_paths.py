"""Counterexamples at the actual public boundary, without business writes."""
import copy
import json
from uuid import uuid4
import pytest
from jsonschema import Draft202012Validator
from services.handlers.chat import content_tools
from services.task_execution import content_objects as co, request_resolution as rr
from services.task_execution.image_reference_catalog import ImageReferences
from services.task_execution.reference_prompt_format import FormatArgumentsError, decode_save, save_recovery_fields
from services.tools.argument_validation import _issues
from tests.test_model_directed_loop import case, execute
from tests.semantic_fixtures import semantic_request


def contract(reference=False, **flags):
    refs = ImageReferences({'input_message_id': str(uuid4()), 'saved_groups': [
        {'artifact_id': 'real-draft', 'revision': 1, 'items': [{'item_index': 3, 'title': '蓝白灯塔'}]}]})
    refs.reference_format = reference
    tool = copy.deepcopy(co.SCHEMA)
    tool['function']['parameters'] = co.save_parameters(require_presentation=reference)
    tool['function']['description'] = '保存用户所选内容。'
    native = content_tools.model_tools([tool], **flags)[0]['function']
    public = refs.tools([{'type': 'function', 'function': native}])[0]['function']
    return refs, native, public


@pytest.mark.parametrize('reference', [False, True])
@pytest.mark.parametrize('flags', [{}, {'revise_saved': True}, {'reuse_source': True, 'selected_saved': True}])
@pytest.mark.parametrize('item', [{}, {'title': None}, {'materials': None}, {'options': None}])
def test_invalid_patch_rejected_by_public_and_native(reference, flags, item):
    refs, _, public = contract(reference, **flags)
    value = {'base': refs.encode({'artifact_id': 'real-draft', 'item_index': 3}),
             'updates': [{'item_index': 3, 'item': item}]}
    for updates in (value['updates'], []):
        value['updates'] = updates
        assert not Draft202012Validator(public['parameters']).is_valid(value)
        assert _issues(value, public['parameters'], image_hints=False)
        with pytest.raises(ValueError):
            co.SaveContent.model_validate(refs.decode_call(co.SAVE, value))


def test_text_recovery_keeps_message_locator_and_execution_constraints():
    refs, native, public = contract()
    params = public['parameters']
    source = params['properties']['message_source']
    assert source['required'] == ['message_id'] and 'image_ref' not in source['properties']
    value = {'message_source': {'message_id': str(uuid4()), 'content_index': 1}, 'continue_execution': True}
    Draft202012Validator(params).validate(value)
    assert not _issues(value, params, image_hints=False)
    assert co.SaveContent.model_validate(refs.decode_call(co.SAVE, value)).message_source
    required, _ = save_recovery_fields(params, {'message_source'})
    assert set(required['message_source']) <= source['properties'].keys()
    for extra in ({'continue_execution': False}, {'show_content': True}):
        bad = {**value, **extra}
        assert not Draft202012Validator(params).is_valid(bad)
        assert _issues(bad, params, image_hints=False)
        with pytest.raises(ValueError):
            co.SaveContent.model_validate(refs.decode_call(co.SAVE, bad))


@pytest.mark.parametrize('confirmation', [{'wait_for': [1]},
    {'tool': 'generate_image', 'wait_for': [1, 1]}, {'tool': 'generate_image', 'confirmation_only': True}])
def test_confirmation_rejections_match(confirmation):
    refs, _, public = contract()
    value = {'document': {'title': '方案', 'items': [{'title': '第三个', 'body': '正文'}]}, 'confirmation': confirmation}
    assert not Draft202012Validator(public['parameters']).is_valid(value)
    assert _issues(value, public['parameters'], image_hints=False)
    with pytest.raises(ValueError):
        co.SaveContent.model_validate(refs.decode_call(co.SAVE, value))


@pytest.mark.parametrize('field', ['prompt', 'presentation', 'reference_analysis'])
def test_rendered_total_reports_public_path_without_truncating(field):
    refs, _, public = contract(True, first_delivery=True)
    value = copy.deepcopy(public['parameters']['examples'][0])
    if field == 'reference_analysis':
        value['document'][field] = '文' * 12000
    elif field == 'prompt':
        value['document']['items'][0][field] = {'positive': '文' * 12000, 'negative': '字' * 9000}
    else:
        value['document']['items'][0][field] = dict.fromkeys(('scheme_content', 'visual_settings', 'reference_usage'), '文' * 5000)
    unchanged = copy.deepcopy(value)
    with pytest.raises(FormatArgumentsError) as caught:
        decode_save(value, lambda *_: '方案3')
    assert caught.value.issues[0]['path'].endswith('.' + field)
    assert value == unchanged and '合计' in public['description']


@pytest.mark.asyncio
@pytest.mark.parametrize('raw', [
    '{"document":{"title":"一","items":[{"title":"一","body":"原文"}]},"document":{"title":"二","items":[{"title":"二","body":"新文"}]}}',
    '{"document":{"title":"中文","items":[{"title":"一","body":"保留{文字}"}]',
    '{"document":{"title":"一","items":[{"title":"一","body":"文","options":{"x":NaN}}]}}',
])
@pytest.mark.parametrize('with_catalog', [False, True])
async def test_invalid_json_never_reaches_save_or_business_io(raw, with_catalog):
    r, p, h, q = case()
    p.content_controls_available = True
    if with_catalog:
        r.checkpoint_base[rr.CONTEXT] = {'input_message_id': str(uuid4()), 'requests': [], 'image_reference_version': 1}
    target = {'kind': 'current', 'basis': 'current_input'}
    q = semantic_request(q, 'write', target=target)
    rr.freeze(r, q, target)
    p.content_visible_tools = {co.SAVE: content_tools.model_tools([co.save_schema(r)], writing_new=True)[0]['function']}
    for index in range(3):
        await execute(r, p, h, q, [{'name': co.SAVE, 'arguments': raw, 'id': f'bad-{index}'}])
        result = json.loads(p.messages[-1]['content'])
        assert result['ok'] is False and result['code'] == co.INVALID_OBJECT
        assert result['submission_state'] == 'not_dispatched'
        assert result['issues'][0]['path'].startswith('$')
        assert not co.catalog(r.task_execution_state)
    assert co.save_repair_exhausted(p.messages)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.parametrize('reference', [False, True])
def test_selected_saved_has_only_revision_form_and_matching_examples(reference):
    refs, _, public = contract(reference, reuse_source=True, selected_saved=True)
    params = public['parameters']
    assert 'message_source' not in params['properties'] and params['required'] == ['base', 'updates']
    for value in params['examples']:
        Draft202012Validator(params).validate(value)
        assert not _issues(value, params, image_hints=False)
        co.SaveContent.model_validate(refs.decode_call(co.SAVE, value))


@pytest.mark.parametrize('reference', [False, True])
@pytest.mark.parametrize('flags', [{}, {'first_delivery': True}, {'writing_new': True},
    {'revise_saved': True}, {'reuse_source': True}, {'reuse_source': True, 'selected_saved': True}])
@pytest.mark.parametrize('protocol', ['chat', 'responses'])
def test_final_protocol_contracts_examples_and_repair_are_consistent(reference, flags, protocol):
    from services.adapters.dashscope.responses import response_tools
    refs, native, function = contract(reference, **flags)
    if protocol == 'responses':
        function = response_tools([{'type': 'function', 'function': function}], ())[0]
    parameters = function['parameters']
    Draft202012Validator.check_schema(parameters)
    assert 'x-content-profile' not in json.dumps(parameters)
    assert 'x-reference-kind' not in json.dumps(parameters)
    for example in parameters.get('examples', ()):
        Draft202012Validator(parameters).validate(example)
        assert not _issues(example, parameters, image_hints=False)
        co.SaveContent.model_validate(refs.decode_call(co.SAVE, example))
    # Public error advice must name fields that are present in its own contract.
    repair = refs.save_repair({'message': '校验失败', 'issues': [{'loc': ['message_source', 'items']}]}, native)
    for path, fields in repair['required_fields'].items():
        node = repair['parameters']
        for part in path.split('.'):
            array = part.endswith('[]')
            node = node['properties'][part.removesuffix('[]')]
            if array:
                if 'items' not in node:
                    node = next(branch for branch in node['anyOf'] if 'items' in branch)
                node = node['items']
        assert set(fields) <= node['properties'].keys()


def test_text_locators_matching_an_image_are_not_rewritten_as_image_selectors():
    mid = str(uuid4())
    refs = ImageReferences({'input_message_id': mid, 'requests': [{'message_id': mid,
        'images': [{'reference': {'message_id': mid, 'content_index': 1}}]}]})
    source = {'message_id': mid, 'content_index': 1}
    value = {'message_source': source, 'source_prompt': source}
    assert refs.encode(value) == value
    unknown_text_schema = {'type': 'object', 'properties': {
        'message_id': {'type': 'string'}, 'content_index': {'type': 'integer'}}, 'required': ['message_id']}
    assert refs.schema(unknown_text_schema) == unknown_text_schema
    from services.tools import build_legacy_catalog
    tool = refs.schema(build_legacy_catalog().require('generate_image').to_schema())['function']
    assert 'message_id' in tool['parameters']['properties']['source_prompt']['properties']
    assert 'image_ref' not in tool['parameters']['properties']['source_prompt']['properties']


def test_document_with_updates_but_without_base_is_not_an_allowed_form():
    refs, _, function = contract()
    value = {'document': {'title': '方案', 'items': [{'title': '图一', 'body': '完整正文'}]},
             'updates': [{'item_index': 1, 'item': {'title': '不能混用修订'}}]}
    assert not Draft202012Validator(function['parameters']).is_valid(value)
    assert _issues(value, function['parameters'], image_hints=False)
    with pytest.raises(ValueError):
        co.SaveContent.model_validate(value)


def test_syntax_diagnostic_preserves_multibyte_offsets_and_ignores_prose_braces():
    from services.tools.argument_validation import decode_tool_arguments, argument_syntax_issue
    raw = '{"updates":[{"item":{"prompt":{"positive":"保留{本子}和[文字]","negative":"水印"}}]}'
    with pytest.raises(ValueError) as caught:
        decode_tool_arguments(raw)
    issue = argument_syntax_issue(raw, caught.value)
    assert issue['path'] == '$.updates[0]'
    assert issue['utf8_byte_offset'] == len(raw[:issue['character_offset']].encode('utf-8'))
    assert issue['utf8_byte_offset'] > issue['character_offset']


@pytest.mark.asyncio
async def test_syntax_rejection_keeps_independent_valid_call_and_original_wire_bytes():
    from tests.test_chat_tool_argument_repairs import setup
    from tests.test_task_execution_model_bridge import call
    from services.handlers.chat import tool_argument_repairs
    runtime, prepared, handler, request = setup()
    valid = call('web_search', {'query': '找一下蓝色的本子'}, 'valid-independent')
    bad = {'id': 'bad-duplicate', 'name': 'web_search', 'arguments': '{"query":"本子","query":"灯塔"}'}
    await execute(runtime, prepared, handler, request, [bad, valid])
    assert handler._execute_tool_calls.await_args.args[0] == [valid]
    assert list(tool_argument_repairs.read(runtime).counts.values()) == [1]
    assistant = [m for m in prepared.messages if m.get('tool_calls')]
    assert len(assistant) == 1
    result_ids = [m['tool_call_id'] for m in prepared.messages if m.get('role') == 'tool']
    assert result_ids.count(bad['id']) == result_ids.count(valid['id']) == 1


@pytest.mark.asyncio
async def test_dispatch_and_error_receipt_use_the_exact_current_model_contract():
    refs, native, public = contract(False, writing_new=True)
    runtime, prepared, handler, request = case()
    prepared.content_controls_available = True
    runtime.checkpoint_base[rr.CONTEXT] = {'input_message_id': str(uuid4()), 'requests': [], 'image_reference_version': 1}
    target = {'kind': 'current', 'basis': 'current_input'}
    request = semantic_request(request, 'write', target=target)
    rr.freeze(runtime, request, target)
    prepared.content_visible_tools = {co.SAVE: co.SCHEMA['function']}
    prepared.model_visible_tools = {co.SAVE: public}
    args = {'message_source': {'message_id': str(uuid4())}, 'continue_execution': True}
    await execute(runtime, prepared, handler, request, [{'id': 'not-offered', 'name': co.SAVE,
                                                      'arguments': json.dumps(args)}])
    result = json.loads(prepared.messages[-1]['content'])
    assert not result['ok'] and result['parameters'] == public['parameters']
    assert not co.catalog(runtime.task_execution_state)
    handler._execute_tool_calls.assert_not_awaited()


def test_material_update_contract_keeps_saved_inputs_without_rewriting_sections():
    from services.task_execution.reference_prompt_format import workflow_examples
    from services.task_execution.saved_image_revision import required_update_fields
    old = {'reference': {'image_ref': 'old'}, 'role': '底图', 'usage': 'input'}
    new = {'reference': {'image_ref': 'new'}, 'role': '配色参考', 'usage': 'input'}
    changes = {'materials': [new]}
    fields = required_update_fields(changes, has_presentation=True)
    assert fields == ['materials']
    workflow = {'items': [{'content_source': {'content_ref': 'draft', 'item_index': 3},
        'required_changes': changes, 'required_update_fields': fields, 'saved_materials': [old]}]}
    examples = [{'base': {}, 'updates': [{'item_index': 3, 'item': {'prompt': {'positive': '不能覆盖正文'}}}]}]
    sample, = workflow_examples(examples, workflow)
    assert sample['updates'][0]['item'] == {'materials': [old, new]}


def test_no_catalog_path_retains_native_revision_phase_without_private_tool_metadata():
    from services.task_execution.reference_boundary import ModelReferenceBoundary
    refs, native, _ = contract(False, revise_saved=True)
    native['_content_workflow'] = {'revision_required': True, 'state': {
        'stage': 'revision_required', 'read_calls': [], 'items': []}}
    boundary = ModelReferenceBoundary(None)
    messages = [{'role': 'user', 'content': '第三个改一下，先不用出图'}]
    unchanged, tools = boundary.model_input(messages, [{'type': 'function', 'function': native}])
    assert unchanged == messages
    visible = tools[0]['function']
    assert 'revision_required' in visible['description']
    assert not {'x-content-profile', 'x-reference-kind', '_content_workflow'} & set(json.dumps(visible).split('"'))
    assert 'body' in visible['parameters']['$defs']['ContentItemPatch']['properties']


@pytest.mark.asyncio
async def test_replayed_malformed_call_is_closed_without_duplicate_assistant_or_save():
    from services.handlers.chat.execution_engine import _execute_tools
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    runtime, prepared, handler, request = case()
    raw = '{"document":{"title":"未完成","items":['
    call = {'id': 'recover-invalid-json', 'name': co.SAVE, 'arguments': raw}
    prepared.messages.append({'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': call['id'], 'type': 'function', 'function': {'name': co.SAVE, 'arguments': raw}}]})
    await _execute_tools(runtime=runtime, prepared=prepared, handler=handler, request=request,
        calls=[call], blocks=[], turn=0, turn_text='', next_model_round=1,
        previewed_call_ids=set(), cancellation_event=runtime.cancellation_event,
        sink=CollectingExecutionSink(), recovery_only=True, assistant_already_present=True)
    assert len([m for m in prepared.messages if m.get('tool_calls')]) == 1
    assert json.loads(prepared.messages[-1]['content'])['submission_state'] == 'not_dispatched'
    assert not co.catalog(runtime.task_execution_state)
    handler._execute_tool_calls.assert_not_awaited()
