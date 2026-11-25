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


def test_query_plan_tool_and_forced_choice_reach_the_existing_model_transport():
    from services.agent.plan_builder import QUERY_PLAN_TOOL
    adapter=DashScopeChatAdapter(api_key='test',model='deepseek-v4-pro')
    choice={'type':'function','function':{'name':'submit_query_plan'}}
    body=adapter._request_body([],stream=True,reasoning_effort=None,thinking_mode='disabled',tools=[QUERY_PLAN_TOOL],tool_choice=choice)
    assert body['tools']==[QUERY_PLAN_TOOL] and body['tool_choice']==choice
    schema=QUERY_PLAN_TOOL['function']['parameters']
    valid={'steps':[{'domain':'trade','params':{'doc_type':'order','order_scope':'valid','shop_name':'原店铺','time_range':'2026-10-01 ~ 2026-10-07','query_type':'summary'}}]}
    assert Draft202012Validator(schema).is_valid(valid)
    missing=copy.deepcopy(valid);del missing['steps'][0]['params']['order_scope']
    assert not Draft202012Validator(schema).is_valid(missing)
    for field in ('time_range','query_type'):
        incomplete=copy.deepcopy(valid);del incomplete['steps'][0]['params'][field]
        assert not Draft202012Validator(schema).is_valid(incomplete)
    invalid=copy.deepcopy(valid);invalid['steps'][0]['params']['order_scope']='invented'
    assert not Draft202012Validator(schema).is_valid(invalid)
    ordinary=adapter._request_body([],stream=True,reasoning_effort=None,thinking_mode='disabled')
    assert 'tool_choice' not in ordinary


async def image_wire_contracts(monkeypatch, text, *, single_image_edit=True, intent=None):
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), 'chat_inline_image_enabled', True)
    monkeypatch.setattr(get_settings(), 'chat_single_image_edit_enabled', single_image_edit)
    runtime, prepared, handler, request = case()
    request = replace(request, content=[TextPart(text=text)])
    if intent is not None:
        from tests.semantic_fixtures import semantic_request
        request=semantic_request(request,intent)
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


@pytest.mark.parametrize(('text', 'enabled', 'simple','intent'), [
    ('把你刚生成的那张图的桌面颜色调浅一点', True, True,'edit'),
    ('把你刚生成的那张图的桌面颜色调浅一点', False, False,'edit'),
    ('把你生成的第二张图的桌面颜色调浅一点', True, True,'edit'),
    ('把你刚生成的那张图改成竖版', True, False,None),
    ('把你刚生成的那张图调浅，并另外生成一张新图', True, False,None),
    ('把你刚生成的那张图调浅，同时生成一段视频', True, False,None),
])
async def test_single_edit_publishes_flat_contract_only_for_supported_requests(monkeypatch, text, enabled, simple,intent):
    contracts = await image_wire_contracts(monkeypatch, text, single_image_edit=enabled,intent=intent)
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


@pytest.mark.parametrize('scenario',['unique','pending','ambiguous','explicit-multiple'])
def test_previous_result_source_is_the_receipt_output_and_not_its_old_input(monkeypatch,scenario):
    from types import SimpleNamespace
    from services.task_execution.inline_image_action import previous_result_reference
    task='00000000-0000-4000-8000-000000000001'
    other='00000000-0000-4000-8000-000000000002'
    operations=[SimpleNamespace(tool_name='generate_image',status='accepted',receipt_ref='task:'+task)]
    if scenario=='explicit-multiple':
        operations.append(SimpleNamespace(tool_name='generate_image',status='accepted',receipt_ref='task:'+other))
    state=SimpleNamespace(operations=operations)
    candidates=[({'id':'old-input','role':'assistant','status':'completed'},0,{'task_id':other}),
                ({'id':'new-output','role':'assistant','status':'running' if scenario=='pending' else 'completed'},
                 2,{'task_id':task})]
    if scenario=='ambiguous':
        candidates.append(({'id':'another-output','role':'assistant','status':'completed'},0,{'task_id':task}))
    native=SimpleNamespace(known_sources=lambda:candidates)
    monkeypatch.setattr('services.task_execution.image_resources.native_resolver',lambda *args:native)
    selected=previous_result_reference(state,object(),object(),object())
    assert selected==({'message_id':'new-output','content_index':2} if scenario=='unique' else None)


@pytest.mark.parametrize('previous',[False,True])
def test_previous_result_is_an_explicit_model_choice_not_a_user_keyword_rule(previous):
    from services.task_execution.inline_image_action import PreviousResultEdit, request_type, is_inline_call
    native=build_legacy_catalog().require('generate_image').to_schema()
    projected,=content_tools.model_tools([native],inline_image=True,previous_result=previous)
    schema=projected['function']['parameters']
    args={'previous_result_edit':'背景改为浅灰，保留主体。'}
    assert Draft202012Validator(schema).is_valid(args) is previous
    assert request_type(schema['properties'],args) is (PreviousResultEdit if previous else None)
    assert is_inline_call({'name':'generate_image','arguments':json.dumps(args)})
    assert Draft202012Validator(schema).is_valid({'content_source':{'artifact_id':'saved','item_index':1}})
    assert not Draft202012Validator(schema).is_valid({**args,'draft':{'title':'new','body':'new'}})


def test_new_planner_calls_require_explicit_count_but_native_legacy_schema_stays_compatible():
    native=build_legacy_catalog().require('plan_ecommerce_images').to_schema()
    before=copy.deepcopy(native)
    projected,=content_tools.model_tools([native])
    args={'references':[{'file_id':'real-file','role':'product'}]}
    assert Draft202012Validator(native['function']['parameters']).is_valid(args)
    schema=projected['function']['parameters']
    assert schema['properties']['image_count']==native['function']['parameters']['properties']['image_count']
    assert not Draft202012Validator(schema).is_valid(args)
    assert Draft202012Validator(schema).is_valid({**args,'image_count':3})
    assert Draft202012Validator(schema).is_valid({'continue_plan_id':'4825ec68-a21c-49d0-b941-f3668ea7c228'})
    assert native==before


def test_frozen_user_count_is_owned_by_server_and_is_not_a_second_model_argument():
    native=build_legacy_catalog().require('plan_ecommerce_images').to_schema()
    before=copy.deepcopy(native)
    projected,=content_tools.model_tools([native],planned_image_count=3)
    schema=projected['function']['parameters']
    assert 'image_count' not in schema['properties']
    assert Draft202012Validator(schema).is_valid({'references':[{'file_id':'original','role':'product'}]})
    assert not Draft202012Validator(schema).is_valid({'references':[{'file_id':'original','role':'product'}],'image_count':10})
    assert native==before
