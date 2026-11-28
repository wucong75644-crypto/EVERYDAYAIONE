"""One public edit contract, private legacy admission, and no ambiguous dispatch."""
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from jsonschema import Draft202012Validator
import pytest

from schemas.message import TextPart
from services.handlers.chat import content_tools, tool_argument_repairs
from services.task_execution import content_objects as content, inline_image_action as inline
from services.tools import build_legacy_catalog
from services.tools.model_contracts import offered_argument_error
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_model_bridge import call


def offer(**kwargs):
    native = build_legacy_catalog().require('generate_image').to_schema()
    tool, = content_tools.model_tools([native], inline_image=True, generated_image_edit=True, **kwargs)
    return tool['function']


def arguments():
    return {'source_message_id': str(uuid4()), 'source_content_index': 0,
            'edit_instruction': '背景改黑，保留产品，不改变比例。'}


def test_repeated_base_reference_is_one_input_but_analysis_conflict_is_rejected():
    args = arguments()
    args['materials'] = [{'reference': {'message_id': args['source_message_id'], 'content_index': 0},
                          'role': '用于保留产品细节的编辑底图', 'usage': 'input'}]
    value = inline.SingleImageEdit.model_validate(args).content_item()
    assert len(value.materials) == 1 and value.body == args['edit_instruction']
    args['materials'][0]['usage'] = 'analysis'
    with pytest.raises(ValueError, match='CHAT_INLINE_IMAGE_SELECTION_CONFLICT'):
        inline.SingleImageEdit.model_validate(args).content_item()


@pytest.mark.parametrize('reference_count', [0, 1, 3])
def test_public_contract_and_admission_preserve_base_and_secondary_references(reference_count):
    args = arguments()
    args['materials'] = [{'reference': {'message_id': str(uuid4()), 'content_index': i},
                          'role': f'风格参考{i}', 'usage': 'input'} for i in range(reference_count)]
    schema = offer()['parameters']
    assert Draft202012Validator(schema).is_valid(args)
    assert offered_argument_error('generate_image', args, schema, input_type=inline.SingleImageEdit) is None
    item = inline.SingleImageEdit.model_validate(args).content_item()
    assert item.body == args['edit_instruction']
    assert item.materials[0].reference.message_id == args['source_message_id']
    assert item.materials[0].usage == 'input' and item.materials[0].role == '编辑底图'
    assert [m.model_dump(mode='json', exclude_none=True) for m in item.materials[1:]] == args['materials']


@pytest.mark.parametrize('variant', ['old-only', 'same-instruction', 'same-explicit-reference'])
def test_legacy_equal_arguments_are_accepted_privately_without_advertising_alias(variant):
    args = arguments()
    legacy = {'previous_result_edit': args['edit_instruction']}
    if variant != 'old-only':
        legacy['edit_instruction'] = args['edit_instruction']
    if variant == 'same-explicit-reference':
        legacy.update(args)
    schema = offer()['parameters']
    assert 'previous_result_edit' not in schema['properties']
    assert not Draft202012Validator(schema).is_valid(legacy)
    assert offered_argument_error('generate_image', legacy, schema) is None


@pytest.mark.parametrize('problem', ['different-instructions', 'partial-reference', 'unknown-field'])
async def test_conflicting_legacy_arguments_fail_before_any_business_io(problem):
    r, p, h, q = case()
    p.content_controls_available = True
    p.content_visible_tools = {'generate_image': offer()}
    args = {'previous_result_edit': '背景改黑'}
    if problem == 'different-instructions':
        args['edit_instruction'] = '背景改白'
    elif problem == 'partial-reference':
        args['source_message_id'] = str(uuid4())
    else:
        args['invented'] = True
    await execute(r, p, h, q, [call('generate_image', args, 'conflict')])
    result = json.loads(next(m['content'].splitlines()[0] for m in reversed(p.messages) if m['role'] == 'tool'))
    assert result['submission_state'] == 'not_dispatched'
    assert result['issues'] and 'previous_result_edit' not in result['allowed_fields']
    assert 'source_message_id' in result['message'] and 'edit_instruction' in result['message']
    assert not content.objects(r.task_execution_state)
    h._execute_tool_calls.assert_not_awaited()


async def test_conflicting_previous_and_explicit_image_never_selects_one_silently(monkeypatch):
    r, p, h, q = case()
    p.content_controls_available = True
    p.content_visible_tools = {'generate_image': offer()}
    args = {**arguments(), 'previous_result_edit': '背景改黑，保留产品，不改变比例。'}
    monkeypatch.setattr(inline, 'previous_result_reference', lambda *unused: {
        'message_id': str(uuid4()), 'content_index': 0})
    await execute(r, p, h, q, [call('generate_image', args, 'source-conflict')])
    result = json.loads(next(m['content'].splitlines()[0] for m in reversed(p.messages) if m['role'] == 'tool'))
    assert result['code'] == 'CHAT_INLINE_IMAGE_SELECTION_CONFLICT'
    assert result['submission_state'] == 'not_dispatched'
    assert result['recovery']['action'] == 'repair_arguments'
    assert not content.objects(r.task_execution_state)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.parametrize('source', ['selected-history', 'uploaded-original', 'previous-result',
                                    'resource_ref', 'file_id', 'asset_id'])
async def test_all_target_sources_preserve_same_instruction_and_reference_before_registration(monkeypatch, source):
    r, p, h, q = case()
    q = replace(q, content=[TextPart(text='把指定图片背景改黑')])
    args = arguments()
    selected = {'message_id': args['source_message_id'], 'content_index': 0}
    if source in {'resource_ref', 'file_id', 'asset_id'}:
        selected = {source: 'verified-resource'}
        args = {'source_reference': selected, 'edit_instruction': args['edit_instruction']}
    if source == 'previous-result':
        monkeypatch.setattr(inline, 'previous_result_reference', lambda *unused: selected)
        args = {'previous_result_edit': args['edit_instruction'], 'edit_instruction': args['edit_instruction']}
    resolve = Mock(return_value=[{'source': 'uploaded' if source == 'uploaded-original' else 'generated'}])
    monkeypatch.setattr('services.task_execution.image_resources.native_resolver',
                        lambda *unused: SimpleNamespace(resolve=resolve))
    captured = []
    class BeforeRegistration(Exception):
        pass
    def capture(*unused, **kwargs):
        captured.append(json.loads(unused[1]['arguments'])['document']['items'][0])
        raise BeforeRegistration
    monkeypatch.setattr(content, 'save', capture)
    with pytest.raises(BeforeRegistration):
        await inline.prepare(r, h, q, p, call('generate_image', args, 'edit'), args,
                             build_legacy_catalog(), version='test')
    assert captured[0]['body'] == '背景改黑，保留产品，不改变比例。'
    assert content.MaterialReference.model_validate(captured[0]['materials'][0]['reference']).model_dump(exclude_none=True) == selected
    assert resolve.call_args.args[0] == [{**selected, 'role': '编辑底图'}]
    h._execute_tool_calls.assert_not_awaited()


def test_regeneration_correction_only_mentions_the_public_edit_contract():
    _, guidance = tool_argument_repairs._SOURCE_RECOVERY['CONTENT_IMAGE_REGENERATION_UNCONFIRMED']
    assert 'previous_result_edit' not in guidance
    assert all(field in guidance for field in ('source_message_id', 'source_content_index', 'edit_instruction'))
