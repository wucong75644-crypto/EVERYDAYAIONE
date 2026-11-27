"""Actual cross-turn workflow, transport contracts and native host deliveries."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from services.task_execution import request_resolution as rr
from services.task_execution.contracts import TaskError
from services.task_execution.reference_boundary import (
    ModelReferenceBoundary, NativeContext, ModelContext, NativeArguments,
    NativeToolCall, REFERENCE_GUIDANCE, for_runtime,
)
from services.task_execution.image_reference_catalog import ImageReferences
from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow, classify_workflow
from tests.test_content_objects import registered
from tests.test_content_revision_contract import configure_reference


async def cross_turn():
    r, p, h, q, group = await registered()
    group = configure_reference(r, group)
    old = str(r.task_bootstrap.input_message_id)
    new = str(uuid4())
    rows = [
        {'message_id': new, 'root_task_id': str(uuid4()), 'turn_number': 2,
         'text_excerpt': '第三个做成一张图，正方形，一千就行。', 'images': [],
         'has_execution_state': False, 'resolution': None},
        {'message_id': old, 'root_task_id': str(r.task_execution_state.root_task_id), 'turn_number': 1,
         'text_excerpt': '帮我想三个蓝白灯塔封面方案，先看方案，不出图。',
         'images': [{'name': '手账主体.jpg', 'reference': {'message_id': old, 'content_index': 1}}],
         'has_execution_state': True, 'resolution': None},
    ]
    r.checkpoint_base[rr.CONTEXT] = {'input_message_id': new, 'catalog_version': 2,
        'image_reference_version': 1, 'requests': rows}
    q = replace(q, context_anchor=SimpleNamespace(input_message_id=new, base_revision=8))
    w = object.__new__(EcommerceWorkflow)
    w.runtime, w.request, w.input = r, q, [{'type': 'text', 'text': rows[0]['text_excerpt']}]
    return w, p, h, group, old


@pytest.mark.asyncio
@pytest.mark.parametrize('target_kind', ['content', 'image'])
async def test_real_saved_draft_to_next_turn_router_uses_only_wire_selectors(monkeypatch, target_kind):
    w, _, _, group, old = await cross_turn()
    r = w.runtime
    before_state = r.task_execution_state.model_dump(mode='json')
    before_checkpoint = copy.deepcopy(r.checkpoint_base)
    context = w.action_context()  # Real saved objects and the real request catalog; no mocked groups.
    assert isinstance(context, NativeContext)
    assert context['saved_groups'][0]['artifact_id'] == group['artifact_id']
    assert context['request_resolution']['saved_groups'][0]['artifact_id'] == group['artifact_id']
    seen = []

    async def route(self, **kwargs):
        wire = json.loads(kwargs['text'])['available_context']
        assert 'material_index' not in json.dumps(wire)
        assert '复制reference.message_id' not in json.dumps(wire)
        selected = wire['saved_groups'][0]
        assert 'content_ref' in selected and 'artifact_id' not in selected
        assert selected['source_request']['message_id'] == old
        assert wire['request_resolution']['current_turn']['turn_number'] == 2
        image = wire['request_resolution']['focused_request']['images'][0]['reference']
        assert set(image) == {'image_ref'}
        assert REFERENCE_GUIDANCE in kwargs['system_prompt']
        target = ({'kind': 'content', 'basis': 'explicit_reference', 'content_ref': selected['content_ref'],
                   'revision': 1, 'item_indices': [3], 'source_message_id': old}
                  if target_kind == 'content' else
                  {'kind': 'image', 'basis': 'explicit_reference', 'image_ref': image['image_ref']})
        answer = {'kind': 'ordinary_image', 'mode': 'new', 'reuse_through_stage': 0,
            'intent': 'execute_saved' if target_kind == 'content' else 'edit',
            'image_count': 1 if target_kind == 'content' else None,
            'request_context': {'target': target, 'focus': 'selected_target',
                'size': {'aspect_ratio': '1:1', 'resolution': '1K'}, 'platform': None,
                'media': {'tool': 'generate_image', 'wait_for': [], 'wait_all': False,
                          'approved': [], 'confirmation_only': False},
                'deliveries': [], 'materials': None, 'image_action': None}}
        Draft202012Validator(kwargs['tools'][0]['function']['parameters']).validate(answer)
        seen.append(answer)
        return {'choices': [{'message': {'tool_calls': [{'function': {
            'name': 'select_image_workflow', 'arguments': json.dumps(answer)}}]}}]}

    monkeypatch.setattr('services.intent_router.IntentRouter.call_tool_model', route)
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(
        intent_router_enabled=True, dashscope_api_key='offline', intent_router_model='offline', intent_router_timeout=5))
    result = await classify_workflow(w.action_input(), context=context, image_references=for_runtime(r).catalog)
    assert len(seen) == 1  # The old regression crashed before reaching this model boundary.
    native = result['request_context']['target']
    assert native['source_message_id'] == old
    if target_kind == 'content':
        assert native['artifact_id'] == group['artifact_id'] and native['item_indices'] == [3]
    else:
        assert native['content_index'] == 1
    assert result['request_context']['size'] == {'aspect_ratio': '1:1', 'resolution': '1K'}
    assert r.task_execution_state.model_dump(mode='json') == before_state
    assert r.checkpoint_base == before_checkpoint
    assert context['saved_groups'][0]['artifact_id'] == group['artifact_id']


@pytest.mark.asyncio
async def test_router_and_main_model_use_same_catalog_schema_and_examples():
    from services.task_execution.direct_loop import context_message, schemas
    w, _, _, group, _ = await cross_turn()
    boundary = for_runtime(w.runtime)
    wire = boundary.context(w.action_context())
    messages, tools = boundary.model_input([context_message(w.runtime)], schemas(w.runtime))
    text = '\n'.join(m['content'] for m in messages if isinstance(m.get('content'), str))
    assert wire['saved_groups'][0]['content_ref'] in text
    assert REFERENCE_GUIDANCE in text
    assert group['artifact_id'] not in text
    examples = 0
    for tool in tools:
        parameters = tool['function']['parameters']
        for example in parameters.get('examples', ()):
            Draft202012Validator(parameters).validate(example)
            assert isinstance(boundary.arguments(example, tool=tool['function']['name']), NativeArguments)
            examples += 1
    assert examples > 0


@pytest.mark.parametrize('value', [
    ModelContext({'saved_groups': []}),
    {'saved_groups': [{'content_ref': 'doc_wrong'}]},
    {'request_resolution': {'images': [{'reference': {'image_ref': 'img_wrong'}}]}},
    {'request_context': {'canvas_ref': 'img_wrong'}},
])
def test_native_context_rejects_model_view_and_short_selectors(value):
    with pytest.raises(TaskError, match='REFERENCE_'):
        NativeContext(value)


def test_mutation_and_double_decoding_are_rejected_without_rebinding():
    catalog = ImageReferences({'input_message_id': str(uuid4()), 'requests': []})
    boundary = ModelReferenceBoundary(catalog)
    context = NativeContext({'saved_groups': []})
    context['saved_groups'].append({'content_ref': 'doc_wrong'})
    with pytest.raises(TaskError, match='SHORT_SELECTOR_IN_NATIVE'):
        boundary.context(context)
    decoded = boundary.arguments({'some_field': 'preserved'})
    with pytest.raises(TaskError, match='ARGUMENTS_ALREADY_PROJECTED'):
        boundary.arguments(decoded)
    with pytest.raises(TaskError, match='NATIVE_ARGUMENTS_REQUIRED'):
        NativeToolCall(name='read_conversation_text', arguments={'show_image': {}}, call_id='bad')
    native_call = NativeToolCall(name='read_conversation_text', arguments=NativeArguments({}), call_id='host')
    native_call['arguments'] = json.dumps({'show_image': {'image_ref': 'img_wrong'}})
    with pytest.raises(TaskError, match='SHORT_SELECTOR_IN_NATIVE'):
        boundary.native_call(native_call)
    with pytest.raises(TaskError, match='SELECTOR_KIND_CONFLICT'):
        boundary.arguments({'content_ref': 'doc_wrong', 'image_ref': 'img_wrong'})


def test_correction_evidence_is_not_an_executable_selector():
    rejected = {'target': {'image_ref': 'img_wrong'}}
    context = NativeContext({'routing_parameter_correction': {'rejected_arguments': rejected},
                             'saved_groups': []})
    wire = ModelReferenceBoundary(None).context(context)
    assert wire['routing_parameter_correction']['rejected_arguments'] == rejected
    with pytest.raises(TaskError, match='SHORT_SELECTOR_IN_NATIVE'):
        NativeArguments({'routing_parameter_correction': {'rejected_arguments': rejected}})


@pytest.mark.asyncio
async def test_host_display_dispatches_native_without_short_reference_roundtrip(monkeypatch):
    from services.handlers.chat import existing_image_delivery as display
    from tests.test_existing_image_delivery import scenario
    from tests.test_model_directed_loop import execute
    r, p, h, q, ref, _ = scenario(monkeypatch)
    p.content_controls_available = True
    p.content_visible_tool_names = {'read_conversation_text'}
    def no_decode(*args, **kwargs):
        raise AssertionError('Host display must not decode model selectors')
    monkeypatch.setattr(ImageReferences, 'decode_call', no_decode)
    blocks = []
    async def dispatch(calls):
        assert all(isinstance(call, NativeToolCall) for call in calls)
        assert json.loads(calls[0]['arguments']) == {'show_image': ref}
        await execute(r, p, h, q, calls, blocks)
    async def fail(text):
        pytest.fail(text)
    assert await display.deliver(r, q, p, blocks, execute=dispatch, finish=fail)
    assert display.complete(r, q, blocks)
    assert len([b for b in blocks if b.get('type') == 'image']) == 1
    h._execute_tool_calls.assert_not_awaited()
