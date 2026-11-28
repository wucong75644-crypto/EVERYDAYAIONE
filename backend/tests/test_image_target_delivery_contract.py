"""Failures observed with ordinary customer edit/display requests."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from services.handlers.chat import content_tools
from services.task_execution import request_resolution as rr
from services.task_execution.content_delivery import input_revision
from services.task_execution.image_reference_catalog import ImageReferences, for_runtime
from tests.test_image_reference_catalog import snapshot
from tests.test_model_directed_loop import execute
from tests.test_request_catalog import catalog_case
from tests.test_task_execution_model_bridge import call


def test_target_example_and_field_descriptions_match_offered_short_contract():
    refs = ImageReferences(snapshot())
    tool = refs.schema(rr.selection_schema())['function']
    examples = tool['parameters']['examples']
    assert examples and any(e['request_context']['target']['kind'] == 'image' for e in examples)
    for example in examples:
        Draft202012Validator(tool['parameters']).validate(example)
        assert json.dumps(example, ensure_ascii=False) in tool['description']
    assert 'image_ref必须按用户实际指代选择，不能抄示例' in tool['description']
    field = tool['parameters']['$defs']['Target']['properties']['source_message_id']
    assert 'image目标使用image_ref' in field['description']
    assert 'target_fields.source_message_id' not in field['description']


def test_short_projection_only_replaces_host_metadata_keeps_authored_prose_and_legacy_view():
    from services.handlers.chat_context.history_loader import _append_image_locators
    refs = ImageReferences(snapshot())
    ref = next(iter(refs.references.values()))
    authored = '用户正文中的 source_message_id 和 content_index 必须逐字保留。'
    messages = [{'role': 'assistant', 'content': authored}]
    _append_image_locators(messages, {'id': ref['message_id'], 'role': 'assistant',
        'content': [{'type': 'image', 'workspace_path': '测试/灯塔.png'}],
        '_trusted_image_sources': [{'name': '灯塔.png', **ref, 'reference': ref}]})
    original = copy.deepcopy(messages)
    public = refs.messages(messages)
    assert messages == original
    assert public[0]['content'].startswith(authored)
    assert '对话图片目录' in public[0]['content'] and '对话图片定位' not in public[0]['content']
    assert ref['message_id'] not in public[0]['content']
    assert '_image_locator_projection' not in public[0]
    assert '对话图片定位' in messages[0]['content']  # Legacy surface remains available.


@pytest.mark.asyncio
@pytest.mark.parametrize('target,expected', [
    ({'kind': 'image'}, 'basis'),
    ({'kind': 'image', 'basis': 'explicit_user_reference'}, 'basis'),
    ({'kind': 'image', 'basis': 'explicit_reference', 'image_refs': ['wrong']}, 'image_refs'),
])
async def test_real_control_rejection_names_wrong_field_without_silently_repairing(monkeypatch, target, expected):
    r, p, h, q, _, _ = catalog_case(monkeypatch)
    rr.context(r)['image_reference_version'] = 1
    p.content_controls_available = True
    args = {'intent': 'edit', 'request_context': {'focus': 'selected_target', 'target': target}}
    if 'image_refs' not in target:
        target['image_ref'] = next(iter(for_runtime(r).references))
    original = copy.deepcopy(args)
    await execute(r, p, h, q, [call(rr.SELECT, args)])
    result = json.loads(p.messages[-1]['content'])
    assert result['code'] == 'MODEL_TOOL_ARGUMENTS_INVALID'
    assert any(i['path'] == '$.request_context.target.' + expected for i in result['issues'])
    assert 'image_ref' in result['parameters']['$defs']['Target']['properties']
    assert args == original and rr.binding(r) is None
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.parametrize('variation', ['same', 'new_input', 'different_image', 'edit', 'other_task', 'extra_tool'])
def test_display_receipt_is_scoped_and_never_cancels_new_edits_or_other_work(monkeypatch, variation):
    r, _, _, q, _, _ = catalog_case(monkeypatch)
    rr.context(r)['image_reference_version'] = 1
    r.workflow_projection = {'intent': 'explain'}
    refs = for_runtime(r)
    handle, ref = next(iter(refs.references.items()))
    r.checkpoint_base['_displayed_image_delivery_v1'] = {'input_revision': input_revision(r, q),
        'owner_task_id': str(r.task_id), 'source_message_id': ref['message_id'], 'source_content_index': ref['content_index']}
    calls = [call(content_tools.READ, {'show_image': {'image_ref': handle}})]
    if variation == 'new_input':
        q = replace(q, context_anchor=SimpleNamespace(input_message_id='another', base_revision=1))
    elif variation == 'different_image':
        calls = [call(content_tools.READ, {'show_image': {'message_id': 'another', 'content_index': 0}})]
    elif variation == 'edit':
        r.workflow_projection = {'intent': 'edit'}
    elif variation == 'other_task':
        r.checkpoint_base['_displayed_image_delivery_v1']['owner_task_id'] = 'another'
    elif variation == 'extra_tool':
        calls.append(call('web_search', {'query': '其他要求'}))
    assert content_tools.repeats_delivered_image(r, q, calls) is (variation == 'same')


@pytest.mark.asyncio
async def test_real_loop_delivers_history_once_then_finishes_repeated_display_without_error(monkeypatch):
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    r, p, h, q, rows, _ = catalog_case(monkeypatch)
    r.skill_coordination = True
    rr.context(r)['image_reference_version'] = 1
    ref = rows[1]['images'][0]['reference']
    rr.freeze(r, q, {'kind': 'image', 'basis': 'explicit_reference',
                     'source_message_id': ref['message_id'], 'content_index': ref['content_index']})
    r.workflow_projection = {'intent': 'explain'}
    h.org_id = None
    h.db = object()
    image = {'type': 'image', 'url': 'https://test.invalid/existing.png',
             'source_message_id': ref['message_id'], 'source_content_index': ref['content_index']}
    monkeypatch.setattr(content_tools, 'show_image', lambda *args: image)
    rounds = []
    async def model(*args, **kwargs):
        rounds.append(kwargs['model_round'])
        assert len(rounds) <= 2
        return '', '', [call(content_tools.READ, {'show_image': for_runtime(r).encode(ref)}, str(len(rounds)))], set()
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', model)
    blocks = []
    await _run_loop(handler=h, request=q, prepared=p, runtime=r, cancellation_event=r.cancellation_event,
                    sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=blocks)
    assert len(rounds) == 2
    assert sum(b.get('type') == 'image' for b in blocks) == 1
    assert not any('重复' in b.get('text', '') for b in blocks)
    assert sum(m.get('role') == 'tool' for m in p.messages) == 1
    h._execute_tool_calls.assert_not_awaited()


def test_edit_example_uses_only_offered_fields_and_repair_uses_short_identity():
    from services.task_execution.inline_image_action import SingleImageEdit, EDIT_GUIDANCE
    refs = ImageReferences(snapshot())
    tool = refs.schema({'type': 'function', 'function': {'name': 'generate_image',
        'description': EDIT_GUIDANCE, 'parameters': SingleImageEdit.model_json_schema()}})['function']
    example = json.loads(tool['description'].split('替用户选图）：')[1].split('。底图')[0])
    Draft202012Validator(tool['parameters']).validate(example)
    native = refs.decode(example)
    SingleImageEdit.model_validate(native)
    repair = json.loads(refs.text(json.dumps({'message': EDIT_GUIDANCE})))
    assert 'source_message_id' not in repair['message']
    assert 'image_ref' in repair['message']


def test_private_history_projection_never_reaches_legacy_provider_protocols():
    from services.adapters.chat_protocol import chat_messages
    from services.adapters.openrouter.chat_adapter import OpenRouterChatAdapter
    from services.adapters.dashscope.responses import response_input
    messages = [{'role': 'assistant', 'content': '历史说明', '_image_locator_projection': {'text': '后台注释', 'sources': []}}]
    for rendered in (chat_messages(messages), OpenRouterChatAdapter._chat_messages(messages),
                     response_input(messages, 'qwen3.8-flash')):
        assert '_image_locator_projection' not in json.dumps(rendered)
        assert rendered[0]['content'] == '历史说明'
    assert '_image_locator_projection' in messages[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('waiting', [False, True])
async def test_edit_without_saved_draft_has_one_execution_correction_but_never_bypasses_wait(monkeypatch, waiting):
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    from core.config import get_settings
    settings = get_settings().model_copy(update={'chat_single_image_edit_enabled': True})
    monkeypatch.setattr('core.config.get_settings', lambda: settings)
    r, p, h, q, rows, _ = catalog_case(monkeypatch)
    r.skill_coordination = True
    rr.context(r)['image_reference_version'] = 1
    ref = rows[1]['images'][0]['reference']
    rr.freeze(r, q, {'kind': 'image', 'basis': 'explicit_reference',
                     'source_message_id': ref['message_id'], 'content_index': ref['content_index']})
    r.workflow_projection = {'intent': 'edit', 'request_context': {
        'media': {'tool': 'generate_image', 'wait_all': waiting}}}
    h.org_id = None
    rounds = []
    async def model(current, *args, **kwargs):
        rounds.append(current.allow_image_call_repair)
        current.image_call_repair_needed = current.allow_image_call_repair
        assert len(rounds) <= 2
        return '尚未执行', '', [], set()
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', model)
    await _run_loop(handler=h, request=q, prepared=p, runtime=r, cancellation_event=r.cancellation_event,
                    sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=[])
    assert rounds == ([False] if waiting else [True, False])
    h._execute_tool_calls.assert_not_awaited()
