"""Compare provider-visible tool schemas with local argument admission."""
import copy
from dataclasses import replace
import json
from unittest.mock import AsyncMock, Mock

from jsonschema import Draft202012Validator
import pytest
from pydantic import ValidationError

from schemas.message import TextPart
from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
from services.adapters.dashscope.responses import response_tools
from services.adapters.types import StreamChunk
from services.handlers.chat import content_tools
from services.skills.runtime import ACTIVATE_SKILL_SCHEMA, DEACTIVATE_SKILL_SCHEMA
from services.task_execution import bootstrap, content_objects, controller, direct_loop, requirement_changes
from services.task_execution.inline_image_action import InlineRequest, SingleImageEdit
from services.tools.argument_validation import _issues, _local_schema_reference
from services.tools import build_legacy_catalog
from services.tools.catalog import build_capability_catalog
from services.tools.model_contracts import offered_argument_error, project_model_tool
from tests.test_content_objects import run_content_loop
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_model_bridge import call


async def image_wire_contracts(monkeypatch, text, *, single_image_edit=True):
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), 'chat_inline_image_enabled', True)
    monkeypatch.setattr(get_settings(), 'chat_single_image_edit_enabled', single_image_edit)
    runtime, prepared, handler, request = case()
    request = replace(request, content=[TextPart(text=text)])
    prepared.messages = [{'role': 'user', 'content': text}]
    prepared.core_tools = [build_legacy_catalog().require('generate_image').to_schema()]
    sent = []

    async def stream(**kwargs):
        sent.append(copy.deepcopy(kwargs['tools']))
        yield StreamChunk(content='请选择参考素材', finish_reason='stop')

    await run_content_loop(runtime, prepared, handler, request, stream)
    tools, = sent
    adapter = DashScopeChatAdapter(api_key='test', model='qwen3.8-flash')
    body = adapter._request_body([], stream=True, reasoning_effort=None, thinking_mode=None, tools=tools)
    chat = {tool['function']['name']: tool['function']['parameters'] for tool in body['tools']}
    responses = {tool['name']: tool['parameters'] for tool in response_tools(tools, ())}
    assert chat == responses
    return chat


@pytest.fixture
async def wire_contracts(monkeypatch):
    return await image_wire_contracts(monkeypatch, '参考这张图的配色重新生成一张')


@pytest.mark.parametrize(('text', 'enabled', 'simple'), [
    ('把你刚生成的那张图的桌面颜色调浅一点', True, True),
    ('把你刚生成的那张图的桌面颜色调浅一点', False, False),
    ('把你生成的第二张图的桌面颜色调浅一点', True, True),
    ('把你刚生成的那张图改成竖版', True, False),
    ('把你刚生成的那张图调浅，并另外生成一张新图', True, False),
    ('把你刚生成的那张图调浅，同时生成一段视频', True, False),
])
async def test_single_edit_publishes_flat_contract_only_for_supported_requests(monkeypatch, text, enabled, simple):
    contracts = await image_wire_contracts(monkeypatch, text, single_image_edit=enabled)
    parameters = contracts['generate_image']
    if not simple:
        assert 'edit_instruction' not in parameters['properties']
        return

    arguments = {
        'source_message_id': '4825ec68-a21c-49d0-b941-f3668ea7c228',
        'source_content_index': 0,
        'edit_instruction': '桌面调浅，保留福字主体',
    }
    assert set(parameters['properties']) == set(parameters['required']) == set(arguments)
    assert '$defs' not in parameters
    validator = Draft202012Validator(parameters)
    assert validator.is_valid(arguments)
    assert offered_argument_error('generate_image', arguments, parameters) is None
    edit = SingleImageEdit.model_validate(arguments)
    assert edit.content_item().body == arguments['edit_instruction']
    for invalid in ({**arguments, 'source_content_index': index} for index in (-1, '0', True)):
        assert not validator.is_valid(invalid)
        assert offered_argument_error('generate_image', invalid, parameters)
        with pytest.raises(ValidationError):
            SingleImageEdit.model_validate(invalid)
    assert offered_argument_error('generate_image', {**arguments, 'draft': {}}, parameters)


SOURCE = {'message_id': 'saved-message', 'content_index': 1}


@pytest.mark.parametrize(('reference', 'valid'), [
    ({'resource_ref': 'opaque'}, True), ({'asset_id': 'saved'}, True),
    ({'file_id': 'selected'}, True), (SOURCE, True),
    ({**SOURCE, 'resource_ref': None, 'asset_id': None}, True),
    ({}, False), ({'file_id': 'selected', 'asset_id': 'saved'}, False),
    ({'file_id': 'selected', 'message_id': 'other'}, False),
    ({'file_id': 'selected', 'content_index': 1}, False),
    ({'message_id': 'saved-message'}, False), ({'content_index': 1}, False),
    ({'file_id': ''}, False), ({**SOURCE, 'content_index': True}, False),
    ({**SOURCE, 'content_index': -1}, False),
])
async def test_image_and_save_admit_same_material_reference(wire_contracts, reference, valid):
    item = {'title': '新图', 'body': '按参考图的构图生成新图', 'materials': [
        {'reference': reference, 'role': '构图参考', 'usage': 'input'},
    ]}
    for name, arguments, model in (
        ('generate_image', {'draft': item}, InlineRequest),
        ('save_task_text', {'document': {'title': '新图', 'items': [item]}}, content_objects.SaveContent),
    ):
        parameters = wire_contracts[name]
        assert Draft202012Validator(parameters).is_valid(arguments) is valid
        assert (offered_argument_error(name, arguments, parameters) is None) is valid
        if valid:
            model.model_validate(arguments)
        else:
            with pytest.raises(ValidationError):
                model.model_validate(arguments)


@pytest.mark.parametrize(('args', 'valid'), [
    ({}, True),
    ({'message_id': 'context'}, True),
    ({'list_images': True, 'message_id': 'context'}, True),
    ({'text_exact': '原文', 'message_id': 'context'}, True),
    ({'text_exacts': ['图一', '图二']}, True),
    ({'image_task_id': 'task'}, True),
    ({'image_task_status': 'task'}, True),
    ({'content_source': {'artifact_id': 'text', 'item_index': 1}}, True),
    ({'show_image': SOURCE}, True),
    ({'view_image': SOURCE}, True),
    ({'view_image': SOURCE, 'image_task_id': 'task'}, False),
    ({'show_image': SOURCE, 'view_image': SOURCE}, False),
    ({'list_images': False}, False),
    ({'list_images': True, 'text_exact': '原文'}, False),
    ({'text_exact': '原文', 'text_exacts': ['原文']}, False),
    ({'text_exacts': ['图一', '图一']}, False),
    ({'text_exact': ''}, False),
    ({'view_image': {'message_id': 'context'}}, False),
])
async def test_read_modes_match_actual_dispatch(wire_contracts, monkeypatch, args, valid):
    runtime, prepared, handler, request = case()
    prepared.content_controls_available = True
    handler.db = None  # External readers are mocked; validation and routing are live.
    prepared.content_visible_tools = {
        content_tools.READ: {'parameters': wire_contracts[content_tools.READ]},
    }
    calls = {
        'read_saved_item': AsyncMock(return_value={}),
        'read_image_view': Mock(return_value=({}, 'https://images.invalid/one.png')),
        'show_image': Mock(return_value={'type': 'image', 'source_message_id': 'saved-message',
                                        'source_content_index': 1}),
        'read_image_task_status': Mock(return_value={}),
        'read_image_recipe': Mock(return_value={}),
        'source_text': Mock(return_value={}),
    }
    for name, mocked in calls.items():
        monkeypatch.setattr(content_tools, name, mocked)

    assert Draft202012Validator(wire_contracts[content_tools.READ]).is_valid(args) is valid
    await execute(runtime, prepared, handler, request, [call(content_tools.READ, args, 'read-case')])
    reply = next(m['content'] for m in prepared.messages if m.get('tool_call_id') == 'read-case')
    result = json.loads(reply)
    assert result['ok'] is valid
    assert sum(mocked.call_count for mocked in calls.values()) == int(valid)
    if not valid:
        assert result['submission_state'] == 'not_dispatched'
        assert result['recovery']['action'] == 'repair_arguments'
        assert content_tools.READ_MODE_GUIDANCE in result['message']
    handler._execute_tool_calls.assert_not_awaited()


def test_nested_schema_reference_carries_root_and_reports_the_field():
    parameters = {
        'type': 'object', 'properties': {'items': {'$ref': '#/$defs/Items'}},
        '$defs': {
            'Items': {'type': 'array', 'items': {'$ref': '#/$defs/Record~1v1'}},
            'Record/v1': {'type': 'object', 'properties': {'usage': {'enum': ['input', 'analysis']}},
                          'required': ['usage'], 'additionalProperties': False},
        },
    }
    assert _issues({'items': [{'usage': 'input'}]}, parameters) == []
    issues = _issues({'items': [{'usage': 'description'}]}, parameters)
    assert [issue['path'] for issue in issues] == ['$.items[0].usage']
    for reference in ('#/$defs/Missing', 'https://tools.invalid/remote'):
        broken = {'$ref': reference}
        assert _issues({}, broken) == [{'path': '$', 'reason': '工具合同引用无效'}]
    assert _issues({}, {'$ref': '#'})


def test_registry_and_dynamic_tool_definitions_are_closed_over_local_schema_references():
    specs = build_capability_catalog().specs()
    registered = [spec.to_schema(variant) for spec in specs if spec.schema is not None
                  for variant in (None, *spec.schema_variants)]
    controls = [{'type': 'function', 'function': {
        'name': name, 'description': controller.CONTROL_DESCRIPTIONS[name], 'parameters': model.model_json_schema(),
    }} for name, model in controller.CONTROL_INPUTS.items()]
    main = [content_objects.SCHEMA, *content_tools.SCHEMAS, direct_loop.PLAN_SCHEMA, direct_loop.RESUME_SCHEMA,
            ACTIVATE_SKILL_SCHEMA, DEACTIVATE_SKILL_SCHEMA]
    projections = [
        content_tools.model_tools([*registered, *main], **scenario)
        for scenario in (
            {'first_delivery': True, 'inline_image': True}, {'reuse_source': True, 'selectable_source_images': True},
            {'server_image_plan': True}, {},
        )
    ]
    legacy = [*controls, bootstrap.START_TASK_SCHEMA, requirement_changes.SCHEMA]
    style = {'type': 'function', 'function': {**content_objects.SCHEMA['function'],
             'parameters': content_objects.save_parameters(require_presentation=True)}}

    def check_refs(root, node):
        if isinstance(node, dict):
            if '$ref' in node:
                _local_schema_reference(root, node['$ref'])
            for value in node.values():
                check_refs(root, value)
        elif isinstance(node, list):
            for value in node:
                check_refs(root, value)

    for tool in [*registered, *main, *legacy, style, *(t for group in projections for t in group)]:
        function = project_model_tool(tool)['function']
        assert function['description'].strip(), function['name']
        parameters = function['parameters']
        Draft202012Validator.check_schema(parameters)
        check_refs(parameters, parameters)


@pytest.mark.parametrize('options,valid', [
    ({}, True),
    ({'size_requirement': {'mode': 'inherit_reference'}}, True),
    ({'aspect_ratio': '3:4', 'resolution': '1K'}, True),
    ({'aspect_ratio': '1024'}, False),
    ({'width': 1024, 'height': 1024}, False),
    ({'size_requirement': {'mode': 'explicit', 'width': 1024}}, False),
])
async def test_image_draft_and_reference_skill_publish_same_size_fields(wire_contracts, options, valid):
    item = {'title': '新图', 'body': '生成商品图', 'display_prefix': '展示', 'options': options}
    save = content_objects.save_parameters(require_presentation=True)
    inline = wire_contracts['generate_image']
    assert Draft202012Validator(save).is_valid({
        'document': {'title': '方案', 'display_prefix': '展示', 'items': [item]},
    }) is valid
    args = {'draft': {key: value for key, value in item.items() if key != 'display_prefix'}}
    assert Draft202012Validator(inline).is_valid(args) is valid
    assert (offered_argument_error('generate_image', args, inline) is None) is valid
