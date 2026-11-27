"""Live customer failures: empty delivery, lost historical upload, receipt drift."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from services.handlers.chat import content_tools
from services.task_execution import content_objects as co
from tests.semantic_fixtures import semantic_request
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_model_bridge import call


def test_acceptance_audit_distinguishes_handoff_from_real_rejections():
    from tests.manual_two_bug_customer_acceptance import wire_errors
    handoff = {'ok': False, 'accepted': False, 'status': 'resuming', 'content_followup': True}
    invalid = {**handoff, 'code': 'MODEL_TOOL_ARGUMENTS_INVALID'}
    failed = {'ok': False, 'code': 'CONTENT_SOURCE_IMAGE_UNAVAILABLE'}
    wire = [{'kind': 'formal_model_request', 'body': {'input': [
        {'type': 'function_call_output', 'call_id': str(i), 'output': json.dumps(value)}
        for i, value in enumerate([handoff, invalid, failed])]}}]
    assert [entry['result']['code'] for entry in wire_errors(wire)] == [
        'MODEL_TOOL_ARGUMENTS_INVALID', 'CONTENT_SOURCE_IMAGE_UNAVAILABLE']


@pytest.mark.parametrize('reference', [False, True])
def test_first_save_examples_match_final_projected_schema_and_presentation(reference):
    from services.task_execution.image_reference_catalog import ImageReferences
    from services.task_execution.methods import REFERENCE_PROMPTS_PRESENTATION, valid_reference_prompts_presentation
    tool = copy.deepcopy(co.SCHEMA)
    tool['function']['parameters'] = co.save_parameters(require_presentation=reference)
    tool['function']['parameters']['examples'] = co.revision_examples(reference)
    if reference:
        tool['function']['description'] = REFERENCE_PROMPTS_PRESENTATION + tool['function']['description']
    projected = content_tools.model_tools([tool], first_delivery=True)
    final = ImageReferences({'input_message_id': str(uuid4())}).tools(projected)[0]['function']
    assert 'base' not in final['parameters']['properties']
    examples = final['parameters']['examples']
    # The appended prose JSON must use the very same new-document contract.
    described = final['description'].split('新建参数的对象结构示例（内容须按实际要求填写，素材另按目录绑定）：')[1]
    example, _ = json.JSONDecoder().raw_decode(described)
    for value in [*examples, example]:
        Draft202012Validator(final['parameters']).validate(value)
        parsed = co.SaveContent.model_validate(value)
        assert parsed.base is None and not parsed.continue_execution
        if reference:
            assert valid_reference_prompts_presentation(parsed.document)


@pytest.mark.asyncio
async def test_explanation_cannot_finish_three_missing_drafts_and_bounded_stop_is_honest():
    r, p, h, q = case()
    p.content_controls_available = True
    r.checkpoint_base['_request_context_v1'] = {'version': 1, 'requests': [], 'input_message_id': str(uuid4())}
    q = semantic_request(q, 'write', media={'tool': 'generate_image', 'wait_for': [1, 2, 3]})
    for i, text in enumerate(['test', '已经整理好了']):
        await execute(r, p, h, q, [call('respond_without_execution', {'purpose': 'explanation', 'text': text}, f'bad-{i}')])
        result = json.loads(p.messages[-1]['content'])
        assert not result['ok'] and result['delivered_items'] == 0
        assert result['stopped'] is (i == 1)
    assert '尚未完整保存' in h._tool_result_stop_reason
    assert not co.objects(r.task_execution_state)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.parametrize('intent,purpose', [('explain', 'explanation'), ('write', 'clarification'), ('edit', 'explanation')])
def test_delivery_gate_preserves_explanations_clarifications_and_image_work(intent, purpose):
    from services.task_execution.text_delivery import check
    r, _, _, q = case()
    assert check(r, semantic_request(q, intent), {'purpose': purpose, 'text': '请说明需要哪种效果'}) is None


def historical_context(r):
    selected = {'message_id': str(uuid4()), 'content_index': 1}
    other = {'message_id': str(uuid4()), 'content_index': 1}
    r.checkpoint_base['_request_context_v1'] = {'requests': [
        {'images': [{'name': '原图.jpg', 'reference': selected}, {'name': '无关新图.jpg', 'reference': other}]}]}
    return selected


@pytest.mark.asyncio
async def test_reasking_without_upload_saves_selected_historical_source_and_each_size():
    r, p, h, q = case()
    selected = historical_context(r)
    q = replace(q, content=[], context_anchor=SimpleNamespace(input_message_id=str(uuid4())))
    q = semantic_request(q, 'write', materials=[{'reference': selected, 'role': '本子原图', 'usage': 'input'}],
        size_rules=[{'scope': 'items', 'item_indices': [1, 2], 'size': {'aspect_ratio': '3:2', 'resolution': '1K'}},
                    {'scope': 'items', 'item_indices': [3], 'size': {'aspect_ratio': '3:4', 'resolution': '2K'}}])
    await execute(r, p, h, q, [call(co.SAVE, {'document': {'title': '三套灯塔方案', 'items': [
        {'title': str(i), 'body': '参考原图保留本子外观，封面改成灯塔。'} for i in range(1, 4)]},
        'confirmation': {'tool': 'generate_image', 'wait_for': [1, 2, 3]}}, 'save-reasked')])
    assert json.loads(p.messages[-1]['content'])['ok']
    artifact = co.objects(r.task_execution_state)[-1]
    record = co.ContentRecord.model_validate_json(artifact.inline_text)
    assert len(record.source_images) == 1 and record.source_images[0].reference.message_id == selected['message_id']
    assert [i.options for i in record.document.items] == [
        {'aspect_ratio': '3:2', 'resolution': '1K'}, {'aspect_ratio': '3:2', 'resolution': '1K'},
        {'aspect_ratio': '3:4', 'resolution': '2K'}]
    native, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, {'artifact_id': artifact.id, 'item_index': 3})
    assert native['mode'] == 'image_to_image' and native['references'][0]['message_id'] == selected['message_id']
    assert not r.task_execution_state.operations
    h._execute_tool_calls.assert_not_awaited()


def test_unknown_reference_is_rejected_instead_of_choosing_a_nearby_image():
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
    r, _, _, q = case()
    historical_context(r)
    frame = ImageRequestContext(materials=[co.Material(reference=co.MaterialReference(
        message_id=str(uuid4()), content_index=1), role='未核验图片', usage='input')])
    with pytest.raises(ValueError, match='CONTENT_SOURCE_IMAGE_UNAVAILABLE'):
        co.registration_source_images(q, r, semantic_context=frame)


@pytest.mark.asyncio
async def test_size_binding_does_not_require_any_picture():
    r, p, h, q = case()
    q = semantic_request(q, 'write', size={'aspect_ratio': '3:2', 'resolution': '2K'})
    await execute(r, p, h, q, [call(co.SAVE, {'document': {'title': '新方案', 'items': [
        {'title': '文生图', 'body': '画一座蓝白灯塔。'}]}}, 'text-only-plan')])
    row, = co.catalog(r.task_execution_state)
    assert row['items'][0]['options'] == {'aspect_ratio': '3:2', 'resolution': '2K'}


@pytest.mark.asyncio
async def test_missing_selected_reference_blocks_saved_execution_instead_of_text_to_image():
    from tests.test_content_objects import registered, ref
    from services.task_execution.saved_image_revision import check
    r, _, _, q, row = await registered()
    q = semantic_request(q, 'execute_saved', materials=[{'reference': {'message_id': str(uuid4()), 'content_index': 1},
        'role': '本次明确选图', 'usage': 'input'}])
    attempt = call('generate_image', {'content_source': ref(row)}, 'dispatch')
    failure = check(r, q, [attempt], r.task_execution_state)['dispatch']
    assert failure['code'] == 'CONTENT_IMAGE_REVISION_REQUIRED'
    assert failure['required_changes']['materials'][0]['usage'] == 'input'
