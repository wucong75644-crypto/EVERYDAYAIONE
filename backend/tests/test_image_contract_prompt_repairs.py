"""Model-visible contracts retain their shape through real result assembly."""
import copy
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from services.handlers.chat import content_tools
from services.handlers.chat.tool_loop import apply_tool_results
from services.handlers.tool_loop_context import ToolLoopContext, append_context_metadata
from services.task_execution import content_objects as co, request_resolution as rr
from services.task_execution.image_reference_catalog import ImageReferences, for_runtime
from services.task_execution.inline_image_action import SingleImageEdit, EDIT_GUIDANCE
from tests.test_image_reference_catalog import snapshot
from tests.test_model_directed_loop import execute
from tests.test_request_catalog import catalog_case
from tests.test_task_execution_model_bridge import call


def edit_rejection():
    return {'code': 'MODEL_TOOL_ARGUMENTS_INVALID', 'message': EDIT_GUIDANCE + co.MATERIAL_GUIDANCE,
        'allowed_fields': ['source_message_id', 'source_content_index', 'edit_instruction', 'materials'],
        'issues': [{'path': '$.source_content_index', 'reason': '缺少必填字段'},
                   {'path': '$.materials[0].reference', 'reason': '类型必须为 object'}]}


@pytest.mark.parametrize('multimodal', [False, True])
def test_real_result_assembly_projects_error_body_before_platform_facts_without_mutating_history(multimodal):
    refs = ImageReferences(snapshot())
    body = json.dumps(edit_rejection(), ensure_ascii=False)
    # This is the result assembly used by the execution engine, including its
    # batch metadata. Multimodal results preserve their non-text blocks too.
    if multimodal:
        body = [{'type': 'text', 'text': body}, {'type': 'image_url', 'image_url': {'url': 'https://test.invalid/image.png'}}]
    messages = []
    if multimodal:
        messages = [{'role': 'tool', 'tool_call_id': 'bad-edit',
            'content': append_context_metadata(body, '本批校验失败')}]
    else:
        apply_tool_results(tool_results=[({'id': 'bad-edit', 'name': 'generate_image'}, body, True, '校验失败')],
            messages=messages, content_blocks=[], start_times={}, tool_context=ToolLoopContext())
    original = copy.deepcopy(messages)
    projected = refs.messages(messages)[0]['content']
    error_text = projected[0]['text'] if multimodal else projected.split('\n')[0]
    error = json.loads(error_text)
    assert error['allowed_fields'] == ['edit_instruction', 'materials', 'image_ref']
    assert error['issues'][0]['path'] == '$.image_ref'
    assert error['issues'][1]['path'] == '$.materials[0].reference'
    assert 'source_message_id' not in error['message']
    assert 'reference:{"image_ref"' in error['message']
    assert 'platform_tool_context' in json.dumps(projected)
    assert messages == original
    if multimodal:
        assert projected[1] == original[0]['content'][1]


def test_multiple_platform_suffixes_are_compatible_but_authored_prose_and_video_receipts_unchanged():
    refs = ImageReferences(snapshot())
    body = json.dumps(edit_rejection(), ensure_ascii=False)
    compound = append_context_metadata(append_context_metadata(body, '批次一'), '批次二')
    assert 'source_message_id' not in json.loads(refs.text(compound).split('\n')[0])['allowed_fields']
    prose = '正文必须保留 source_message_id\n' + json.dumps({'source_message_id': '正文例子'})
    public = refs.messages([{'role': 'user', 'content': prose}, {'role': 'assistant', 'content': prose}])
    assert public[0]['content'] == public[1]['content'] == prose
    video = append_context_metadata(json.dumps({'status': 'submitted', 'task_id': 'video-task'}), '视频已受理')
    assert json.loads(refs.text(video).split('\n')[0]) == {'status': 'submitted', 'task_id': 'video-task'}


def test_nested_material_example_validates_and_string_reference_is_rejected():
    refs = ImageReferences(snapshot())
    tool = refs.schema({'type': 'function', 'function': {'name': 'generate_image',
        'description': EDIT_GUIDANCE, 'parameters': SingleImageEdit.model_json_schema()}})['function']
    description = tool['description']
    extra = json.loads(description.split('完整结构示例：')[1].split('。示例')[0])
    args = {'image_ref': list(refs.references)[-1], 'edit_instruction': '只改颜色', **extra}
    validator = Draft202012Validator(tool['parameters'])
    validator.validate(args)
    args['materials'][0]['reference'] = args['materials'][0]['reference']['image_ref']
    assert not validator.is_valid(args)


def test_content_repair_does_not_teach_image_fields_and_focus_has_action_semantics():
    refs = ImageReferences(snapshot())
    parameters = rr.selection_schema()['function']['parameters']
    args = {'intent': 'execute_saved', 'request_context': {'focus': 'selected_target',
        'target': {'kind': 'content', 'basis': 'explicit_reference', 'item_indices': [1]}}}
    result = refs.selection_repair({}, args, parameters)
    assert 'artifact_id' in result['message'] and 'focus=selected_target' in result['message']
    assert 'image目标' not in result['message']
    assert 'execute_saved' in parameters['properties']['request_context']['properties']['focus']['description']


@pytest.mark.asyncio
async def test_real_focus_rejection_names_the_field_without_changing_selected_task(monkeypatch):
    r, p, h, q, _, _ = catalog_case(monkeypatch)
    p.content_controls_available = True
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
    p.ecom_workflow = SimpleNamespace(value=SimpleNamespace(kind='ordinary_image', request_context=ImageRequestContext()))
    # A semantic rejection from the real selection admission, not a schema error.
    target = {'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': 'existing',
              'revision': 1, 'item_indices': [1], 'source_message_id': rr.context(r)['input_message_id']}
    await execute(r, p, h, q, [call(rr.SELECT, {'intent': 'execute_saved',
        'request_context': {'focus': 'current_request', 'target': target}})])
    result = json.loads(p.messages[-1]['content'])
    assert result['code'] == 'REQUEST_TARGET_FOCUS_CONFLICT'
    assert result['issues'][0]['path'] == '$.request_context.focus'
    assert 'selected_target' in result['message']
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_revision_for_square_preserves_other_specs_inputs_and_other_items():
    from tests.test_content_objects import registered, ref
    from tests.semantic_fixtures import observed_revision
    r, p, h, q, row = await registered()
    before = r.task_execution_state
    _, original, _ = co.load(before, ref(row))
    selected = original.items[0]
    options = {**selected.options, 'aspect_ratio': '3:4'}
    await execute(r, p, h, q, [call(co.SAVE, {'base': ref(row), 'updates': [
        {'item_index': 1, 'item': {'body': '修改为竖版画布，产品结构不变。', 'options': options}}]}, 'portrait')])
    current, = co.catalog(r.task_execution_state)
    _, portrait, _ = co.load(r.task_execution_state, ref(current))
    assert current["revision"] == row["revision"] + 1
    observed_revision(r, q, current, [1])
    await execute(r, p, h, q, [call(co.SAVE, {'base': ref(current), 'updates': [
        {'item_index': 1, 'item': {'body': '正方形画布，产品结构不变。',
            'options': {**portrait.items[0].options, 'aspect_ratio': '1:1'}}}],
        'continue_execution': True}, 'square')])
    latest, = co.catalog(r.task_execution_state)
    native, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, ref(latest))
    _, revised, _ = co.load(r.task_execution_state, ref(latest))
    assert native['aspect_ratio'] == '1:1' and native['resolution'] == '1K' and native['output_format'] == 'png'
    assert revised.items[0].materials == selected.materials
    assert revised.items[1:] == original.items[1:]
    assert co.load(before, ref(row))[1] == original
    h._execute_tool_calls.assert_not_awaited()  # Saving never dispatches implicitly.


@pytest.mark.asyncio
async def test_size_revision_does_not_approve_waiting_image():
    from tests.test_model_directed_loop import case
    from tests.test_content_objects import document, ref
    from services.task_execution.item_confirmation import check
    r, p, h, q = case()
    p.content_controls_available = True
    await execute(r, p, h, q, [call(co.SAVE, {'document': document(),
        'confirmation': {'tool': 'generate_image', 'wait_for': [1]}}, 'waiting')])
    row, = co.catalog(r.task_execution_state)
    _, previous, _ = co.load(r.task_execution_state, ref(row))
    await execute(r, p, h, q, [call(co.SAVE, {'base': ref(row), 'updates': [
        {'item_index': 1, 'item': {'options': {**previous.items[0].options, 'aspect_ratio': '3:4'}}}],
        'continue_execution': True}, 'revise-waiting')])
    latest, = co.catalog(r.task_execution_state, for_model=True)
    assert latest['items'][0]['awaiting_confirmation']
    assert check(call('generate_image', {'content_source': {'artifact_id': latest['artifact_id'], 'item_index': 1}}),
        r.task_execution_state, '')[0] == 'CHAT_MEDIA_AWAITING_USER'
    h._execute_tool_calls.assert_not_awaited()


def test_saved_image_tools_explain_revision_without_changing_video_contract():
    from services.tools import build_legacy_catalog
    source = [build_legacy_catalog().require(name).to_schema() for name in ('generate_image', 'generate_video')]
    old_video, = content_tools.model_tools([source[1]], saved_text_available=True)
    tools = content_tools.model_tools([*source, co.SCHEMA], saved_text_available=True, reuse_source=True)
    offered = {t['function']['name']: t['function'] for t in tools}
    assert offered['generate_video'] == old_video['function']
    for name in ('generate_image', co.SAVE):
        assert co.IMAGE_REVISION_GUIDANCE in offered[name]['description']
    assert '用户未修改该条内容或规格时' in offered['generate_image']['description']
    refs = ImageReferences(snapshot())
    public = refs.tools(tools)
    for tool in public:
        Draft202012Validator.check_schema(tool['function']['parameters'])
    save = next(t['function'] for t in public if t['function']['name'] == co.SAVE)
    assert '否则省略以保留原标题' in str(save['parameters'])
