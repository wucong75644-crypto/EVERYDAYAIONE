"""Image readiness must agree across transport, directories and native selection."""
import copy
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from services.task_execution import image_history as history, request_resolution as rr
from services.task_execution.image_reference_catalog import for_runtime
from tests.test_request_resolution import setup_case
from tests.test_image_history import case


def current_image():
    runtime, prepared, handler, request, rows = setup_case()
    snapshot = rr.context(runtime)
    snapshot.update(image_reference_version=1, image_provenance_version=1)
    ref = {'message_id': snapshot['input_message_id'], 'content_index': 1}
    rows[0]['images'] = [{'name': '本条上传.png', 'reference': ref}]
    target = {'kind': 'image', 'basis': 'explicit_reference',
              'source_message_id': ref['message_id'], 'content_index': 1}
    return runtime, request, ref, target


def test_recovered_index_does_not_change_current_upload_selection_permission():
    runtime, request, ref, target = current_image()
    rr.freeze(runtime, request, target)
    runtime.checkpoint_base[history.KEY] = {'scope': history.scope(runtime), 'images': [
        {'source_message_id': ref['message_id'], 'content_index': 1, 'reference': ref, 'source_sha256': 'a' * 64}]}
    assert not history.requires_view(runtime, ref)
    rr.freeze(runtime, request, target)


def test_query_and_cache_next_actions_follow_each_candidate_readiness(monkeypatch):
    runtime, handler, request, context, _ = case(monkeypatch, images=2)
    reply = history.page(handler, request, context, runtime, {'source': 'generated', 'limit': 2})
    assert 'tool' not in reply['next_step']  # The host has not selected a candidate.
    for image in reply['images']:
        assert image['requires_view_before_selection']
        assert image['next_step']['tool'] == 'read_conversation_text'
        assert 'selection_arguments' not in image
    ref = for_runtime(runtime).decode(reply['images'][0]['reference'])
    history.mark_viewed(runtime, ref)
    cached = history.page(handler, request, context, runtime, {'source': 'generated', 'limit': 2})
    assert [image['next_step']['tool'] for image in cached['images']] == [rr.SELECT, 'read_conversation_text']


def test_uploaded_directory_flags_match_native_readiness():
    runtime, _, _, _, rows = setup_case()
    rr.context(runtime).update(image_reference_version=1, image_provenance_version=1, catalog_version=2)
    context = rr.model_context(runtime)
    image = next(row['images'][0] for row in context['recent_requests'] if row.get('images'))
    assert image['requires_view_before_selection'] is True
    assert image['next_step']['tool'] == 'read_conversation_text'


def test_only_ready_image_selections_are_offered_but_all_candidates_remain_viewable():
    runtime, _, _, _, rows = setup_case()
    rr.context(runtime).update(image_reference_version=1, image_provenance_version=1)
    refs = for_runtime(runtime)
    schema = refs.schema(rr.selection_schema(runtime))['function']['parameters']
    ref = rows[1]['images'][0]['reference']
    args = {'intent': 'explain', 'request_context': {'focus': 'selected_target',
        'image_action': 'display_existing', 'target': {'kind': 'image', 'basis': 'explicit_reference',
            **refs.encode(ref)}}}
    assert not Draft202012Validator(schema).is_valid(args)
    history.mark_viewed(runtime, ref)
    offered = for_runtime(runtime).schema(rr.selection_schema(runtime))['function']['parameters']
    Draft202012Validator(offered).validate(args)


def old_image():
    runtime, prepared, handler, request, rows = setup_case()
    rr.context(runtime).update(image_reference_version=1, image_provenance_version=1, catalog_version=2)
    return runtime, prepared, handler, request, rows[1]['images'][0]['reference']


def visual_message(ref, *, attached=True, declared=True):
    from services.task_execution.image_visual_input import SOURCES
    url = 'https://images.example.test/known-image.png'
    message = {'role': 'user', 'content': [{'type': 'text', 'text': '最早那张原图发我看看，不用重新做'}]}
    if attached:
        message['content'].append({'type': 'image_url', 'image_url': {'url': url}})
    if declared:
        message[SOURCES] = [{'reference': ref, 'url': url}]
    return message


@pytest.mark.parametrize('attached,declared', [(False, True), (True, False), (False, False)])
def test_text_or_removed_image_cannot_create_a_visual_receipt(attached, declared):
    from services.task_execution import image_visual_input as visual
    runtime, _, _, _, ref = old_image()
    message = visual_message(ref, attached=attached, declared=declared)
    token = visual.prepare(runtime, [message])
    assert history.requires_view(runtime, ref, model_view=True)
    visual.commit(runtime, token)
    assert history.requires_view(runtime, ref)


def test_pending_input_is_only_a_model_offer_and_commit_is_scope_bound():
    from services.task_execution import image_visual_input as visual
    runtime, _, _, _, ref = old_image()
    token = visual.prepare(runtime, [visual_message(ref)])
    assert not history.requires_view(runtime, ref, model_view=True)
    assert history.requires_view(runtime, ref)  # No permission before successful response.
    rr.context(runtime)['input_message_id'] = str(__import__('uuid').uuid4())
    visual.commit(runtime, token)
    assert history.requires_view(runtime, ref)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'exception', 'unfinished', 'cancelled'])
async def test_actual_sampling_commits_only_successful_attached_images(failure):
    import asyncio
    import json
    from services.adapters.types import StreamChunk, ToolCallDelta
    from services.handlers.chat.execution_engine import _read_turn
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    from services.task_execution.direct_loop import context_message
    from tests.semantic_fixtures import semantic_request
    runtime, prepared, _, request, ref = old_image()
    request = semantic_request(request, 'explain', image_action='display_existing',
                               target={'kind': 'ambiguous', 'basis': 'ambiguous'})
    prepared.messages = [visual_message(ref), context_message(runtime)]
    received = []
    cancel = asyncio.Event()
    async def stream(**kwargs):
        received.append(kwargs)
        assert history.requires_view(runtime, ref)
        serialized = json.dumps(kwargs['messages'])
        assert '_visual_input_sources' not in serialized and '_host_context_kind' not in serialized
        schema = next(t['function']['parameters'] for t in kwargs['tools'] if t.get('function', {}).get('name') == rr.SELECT)
        args = {'intent': 'explain', 'request_context': {'focus': 'selected_target', 'image_action': 'display_existing',
            'target': {'kind': 'image', 'basis': 'explicit_reference', **for_runtime(runtime).encode(ref)}}}
        Draft202012Validator(schema).validate(args)
        if failure == 'exception':
            raise RuntimeError('simulated provider failure')
        if failure == 'cancelled':
            cancel.set()
        yield StreamChunk(content='', finish_reason=None if failure == 'unfinished' else 'tool_calls',
            tool_calls=[ToolCallDelta(index=0, id='choose-visible', name=rr.SELECT,
                                      arguments_delta=json.dumps(args))])
    prepared.stream_kwargs = {}
    prepared.model_gateway = SimpleNamespace(stream_chat=stream)
    kwargs = dict(prepared=prepared, tools=[rr.selection_schema(runtime)], cancellation_event=cancel,
        sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=[], runtime=runtime, request=request)
    if failure == 'exception':
        with pytest.raises(RuntimeError, match='simulated provider'):
            await _read_turn(**kwargs)
    elif failure == 'cancelled':
        with pytest.raises(asyncio.CancelledError):
            await _read_turn(**kwargs)
    else:
        await _read_turn(**kwargs)
    assert bool(history.was_viewed(runtime, ref)) is (failure is None)
    assert not getattr(runtime, 'pending_image_inputs', None)
    assert len(received) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('intent,action', [('explain', 'display_existing'), ('explain', 'analyze'), ('edit', None), ('explain', None)])
async def test_router_recovery_keeps_action_and_uses_current_candidate_next_step(intent, action):
    from unittest.mock import AsyncMock
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
    from services.task_execution import image_visual_input as visual
    runtime, _, _, request, ref = old_image()
    route = EcommerceWorkflow.__new__(EcommerceWorkflow)
    route.runtime, route.request, route.previous = runtime, request, None
    route.allow_server_planning = False
    route.route = {'kind': 'ordinary_image', 'mode': 'new', 'intent': intent, 'request_context': {
        'focus': 'selected_target', 'image_action': action, 'target': {'kind': 'image',
            'basis': 'explicit_reference', 'source_message_id': ref['message_id'], 'content_index': ref['content_index']}}}
    route.save = AsyncMock()
    await route.identify()
    rejected = rr.binding(runtime)['rejection']
    assert route.value.intent == intent
    assert route.value.request_context.image_action == action
    assert rejected['candidate_target']['source_message_id'] == ref['message_id']
    assert rejected['recovery']['tool'] == 'read_conversation_text'
    visual.prepare(runtime, [visual_message(ref)])
    current = rr.native_context(runtime)['resolution']['rejection']['recovery']
    assert current['tool'] == rr.SELECT
    assert current['arguments']['intent'] == intent
    assert current['arguments']['request_context']['image_action'] == action
    assert not history.was_viewed(runtime, ref)


@pytest.mark.parametrize('protocol', ['chat', 'responses'])
def test_current_selection_examples_and_view_calls_match_final_schemas(protocol):
    from services.adapters.dashscope.responses import response_tools
    from services.handlers.chat.content_tools import READ_SCHEMA
    from services.tools.argument_validation import _issues
    runtime, _, _, _, ref = old_image()
    for ready in (False, True):
        if ready:
            history.mark_viewed(runtime, ref)
        refs = for_runtime(runtime)
        tools = refs.tools([rr.selection_schema(runtime), READ_SCHEMA])
        functions = response_tools(tools, ()) if protocol == 'responses' else [t['function'] for t in tools]
        select = next(f for f in functions if f['name'] == rr.SELECT)
        read = next(f for f in functions if f['name'] == 'read_conversation_text')
        Draft202012Validator.check_schema(select['parameters'])
        for example in select['parameters']['examples']:
            Draft202012Validator(select['parameters']).validate(example)
            assert not _issues(example, select['parameters'], image_hints=False)
        view_call = refs.encode({'view_image': ref})
        Draft202012Validator(read['parameters']).validate(view_call)
        assert not _issues(view_call, read['parameters'], image_hints=False)
        assert len(refs.candidates) == 1
        assert refs.candidates[0]['next_step']['tool'] == (rr.SELECT if ready else 'read_conversation_text')


def test_redisplay_alias_and_original_share_the_same_visual_receipt():
    from uuid import uuid4
    from services.task_execution import image_visual_input as visual
    runtime, _, _, _, ref = old_image()
    alias = {'message_id': str(uuid4()), 'content_index': 0}
    rr.context(runtime)['image_results'] = [{'source_message_id': ref['message_id'], 'content_index': ref['content_index'],
        'displayed_in': [alias]}]
    token = visual.prepare(runtime, [visual_message(alias)])
    assert token['references'] == [ref]
    visual.commit(runtime, token)
    assert history.was_viewed(runtime, alias) and history.was_viewed(runtime, ref)


def test_unknown_identity_cannot_be_attested_from_a_host_url_declaration():
    from uuid import uuid4
    from services.task_execution import image_visual_input as visual
    runtime, _, _, _, ref = old_image()
    unknown = {**ref, 'message_id': str(uuid4())}
    token = visual.prepare(runtime, [visual_message(unknown)])
    assert not token['references']
    visual.commit(runtime, token)
    assert not history.was_viewed(runtime, unknown)


def test_recent_upload_is_in_display_recovery_without_history_pagination():
    import json
    from services.handlers.chat.existing_image_delivery import recovery_context
    from tests.semantic_fixtures import semantic_request
    runtime, _, _, request, ref = old_image()
    request = semantic_request(request, 'explain', image_action='display_existing',
                               target={'kind': 'ambiguous', 'basis': 'ambiguous'})
    result = recovery_context(runtime, request, [])
    facts = json.loads(result.split('\n', 1)[1])
    assert len(facts['candidates']) == 1
    assert facts['candidates'][0]['next_step']['tool'] == 'read_conversation_text'
    assert facts['candidates'][0]['requires_view_before_selection']


@pytest.mark.parametrize('intent,action', [('explain', 'analyze'), ('explain', 'display_existing'), ('edit', None), ('write', None)])
def test_candidate_next_call_and_json_examples_preserve_current_action(intent, action):
    runtime, _, _, request, ref = old_image()
    runtime.workflow_projection = {'intent': intent, 'request_context': {'image_action': action}}
    history.mark_viewed(runtime, ref)
    context = rr.model_context(runtime)
    image = next(row['images'][0] for row in context['recent_requests'] if row.get('images'))
    args = image['next_step']['arguments']
    assert args['intent'] == intent
    assert args['request_context']['image_action'] == action
    if intent == 'write':
        assert args['request_context']['focus'] == 'current_request'
    rr.freeze(runtime, request, {'kind': 'image', 'basis': 'explicit_reference',
        'source_message_id': ref['message_id'], 'content_index': ref['content_index']})
    function = for_runtime(runtime).schema(rr.selection_schema(runtime))['function']
    image_examples = [e for e in function['parameters']['examples'] if e['request_context']['target']['kind'] == 'image']
    assert image_examples
    assert all(e['intent'] == intent and e['request_context']['image_action'] == action for e in image_examples)
    for example in function['parameters']['examples']:
        Draft202012Validator(function['parameters']).validate(example)


@pytest.mark.asyncio
async def test_ambiguous_image_target_retains_edit_intent_without_choosing_an_image():
    from unittest.mock import AsyncMock
    from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
    runtime, _, _, request, _ = old_image()
    workflow = EcommerceWorkflow.__new__(EcommerceWorkflow)
    workflow.runtime, workflow.request, workflow.previous = runtime, request, None
    workflow.allow_server_planning = False
    workflow.route = {'kind': 'ordinary_image', 'mode': 'new', 'intent': 'edit', 'request_context': {
        'focus': 'selected_target', 'target': {'kind': 'ambiguous', 'basis': 'ambiguous'}}}
    workflow.save = AsyncMock()
    await workflow.identify()
    assert workflow.value.intent == 'edit'
    assert rr.binding(runtime)['status'] == 'ambiguous'
    assert rr.binding(runtime)['target']['kind'] == 'ambiguous'
    assert 'candidate_target' not in rr.binding(runtime).get('rejection', {})


@pytest.mark.asyncio
async def test_edit_cannot_skip_target_selection_after_viewing(monkeypatch):
    import json
    from services.handlers.chat.existing_image_delivery import offer
    from services.handlers.chat import content_tools
    from services.tools import build_legacy_catalog
    from tests.semantic_fixtures import semantic_request
    from tests.test_model_directed_loop import execute
    from tests.test_task_execution_model_bridge import call
    runtime, prepared, handler, request, ref = old_image()
    runtime.workflow_projection = {'intent': 'edit', 'request_context': {}}
    request = semantic_request(request, 'edit', target={'kind': 'ambiguous', 'basis': 'ambiguous'})
    rr.freeze(runtime, request, {'kind': 'ambiguous', 'basis': 'ambiguous'})
    tools = [build_legacy_catalog().require('generate_image').to_schema(), content_tools.READ_SCHEMA]
    assert all(t['function']['name'] != 'generate_image' for t in offer(runtime, request, tools))
    monkeypatch.setattr(content_tools, 'read_image_view', lambda *a: ({'reference': ref, 'visually_present': True}, 'https://images.example.test/view.png'))
    prepared.content_controls_available = True
    await execute(runtime, prepared, handler, request, [call(content_tools.READ, {'view_image': for_runtime(runtime).encode(ref)}, 'read-first')])
    receipt = next(json.loads(m['content']) for m in reversed(prepared.messages) if m.get('role') == 'tool')
    assert receipt['ok'] and receipt['next_step']['tool'] == rr.SELECT
    assert receipt['next_step']['arguments']['intent'] == 'edit'
    assert all(t['function']['name'] != 'generate_image' for t in offer(runtime, request, tools))
    rr.freeze(runtime, request, {'kind': 'image', 'basis': 'explicit_reference', 'source_message_id': ref['message_id'], 'content_index': ref['content_index']})
    assert any(t['function']['name'] == 'generate_image' for t in offer(runtime, request, tools))
    handler._execute_tool_calls.assert_not_awaited()


def test_unresolved_target_does_not_turn_first_ready_image_into_a_global_example():
    import json
    from services.handlers.chat.content_tools import READ_SCHEMA
    runtime, _, _, _, ref = old_image()
    now = rr.context(runtime)['input_message_id']
    rr.context(runtime)['requests'][0]['images'] = [{'name': '新上传的另一张', 'reference': {'message_id': now, 'content_index': 1}}]
    runtime.workflow_projection = {'intent': 'edit', 'request_context': {}}
    refs = for_runtime(runtime)
    assert refs.ready_image_refs
    functions = [tool['function'] for tool in refs.tools([rr.selection_schema(runtime), READ_SCHEMA])]
    select = next(f for f in functions if f['name'] == rr.SELECT)
    assert not [e for e in select['parameters']['examples'] if e['request_context']['target']['kind'] == 'image']
    assert '当前动作的完整图片目标JSON示例' not in select['description']
    candidate = next(c for c in refs.candidates if refs.references[c['image_ref']] == ref)
    assert candidate['next_step']['tool'] == 'read_conversation_text'
