"""Model proposal -> canonical saved body -> delivery -> next-turn source."""
import copy
import json
from dataclasses import replace

import pytest

from services.adapters.types import StreamChunk
from services.conversation_commands import SafePoint
from services.handlers.chat.outcome_builder import build_content_parts
from services.handlers.chat_context.history_outcomes import project_completed_assistant
from services.task_execution import content_objects as co
from services.task_execution.content_delivery import KEY, pending_response, stage_response
from tests.test_content_objects import case, document, model_save_chunk, run_content_loop, ref


@pytest.mark.asyncio
async def test_explicit_intro_saves_displays_and_reuses_every_body_once():
    saved = []
    async def checkpoint(point, payload):
        saved.append((point, copy.deepcopy(payload)))
        return {'outcome': 'saved'}
    r, p, h, q = case(checkpoint)
    draft = document() | {'display_prefix': '下面是三条完整提示词。'}
    async def stream(**kwargs):
        yield StreamChunk(content=draft['display_prefix'])
        yield model_save_chunk(draft)
    from services.agent.observability.chat_lifecycle import capture_chat_lifecycle
    timings = []
    with capture_chat_lifecycle(timings):
        _, blocks, totals = await run_content_loop(r, p, h, q, stream)
    assert {'content.proposal_saved', 'content.registration.end', 'content.published'} <= {row['phase'] for row in timings}
    part, = build_content_parts(blocks, fallback_text=totals.text)
    canonical = co.render(co.ContentDocument.model_validate(draft))
    assert part.text == canonical == totals.text
    assert all(canonical.count(item['body']) == 1 for item in draft['items'])
    row, = co.catalog(r.task_execution_state)
    assert part.content_artifact_id == row['artifact_id']
    for i, item in enumerate(draft['items'], 1):
        native, _ = co.compile_input(r.task_execution_state, 'generate_image', {}, ref(row, i))
        assert native['prompt'] == item['body']
        assert native['references'] == [{'message_id': 'original', 'content_index': 1, 'role': '原产品'}]
    proposed = next(value for point, value in saved if point == SafePoint.BEFORE_TOOL)
    assert proposed[KEY]['text'] == draft['display_prefix']
    assert not any(m.get('tool_calls') for m in proposed['messages'])
    assert saved[-1][1][KEY]['phase'] == 'committed'
    history = project_completed_assistant([part.model_dump(mode='json')])
    assert '已登记并展示正文' in history and row['artifact_id'] in history
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('thinking_on_repair', [False, True])
async def test_intro_without_explicit_prefix_gets_one_protocol_repair_not_silent_success(thinking_on_repair):
    r, p, h, q = case()
    draft, rounds = document(), []
    intro = '下面是三条完整提示词。'
    async def stream(**kwargs):
        rounds.append(copy.deepcopy(kwargs['messages']))
        if len(rounds) == 1:
            yield StreamChunk(content=intro)
            yield model_save_chunk(draft)
        else:
            assert co.DRAFT_CONFLICT in json.dumps(kwargs['messages'], ensure_ascii=False)
            if thinking_on_repair:
                yield StreamChunk(thinking_content='核对原文和前言。')
            yield model_save_chunk(draft | {'display_prefix': intro})
    _, blocks, totals = await run_content_loop(r, p, h, q, stream)
    assert len(rounds) == 2
    assert len(co.catalog(r.task_execution_state)) == 1
    assert all(any(item['body'] in b.get('text', '') for b in blocks) for item in draft['items'])
    assert not any(b.get('content_incomplete') for b in blocks)
    assert len([b for b in blocks if b.get('type') == 'text']) == 1
    assert totals.text.count(intro) == 1
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_prose_after_failed_save_cannot_complete_delivery_even_without_task_state():
    r, p, h, q = case()
    rounds = 0
    async def stream(**kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            yield StreamChunk(content='五条提示词已经保存。')
            yield model_save_chunk(document())
        else:
            yield StreamChunk(content='已完成，直接使用即可。')
    _, blocks, totals = await run_content_loop(r, p, h, q, stream)
    assert rounds == 2 and not co.catalog(r.task_execution_state)
    assert blocks[-1]['content_incomplete'] is True
    parts = build_content_parts(blocks, fallback_text=totals.text)
    history = project_completed_assistant([x.model_dump(mode='json') for x in parts])
    assert '正文未完整交付' in history
    assert '已登记并展示正文' not in history


@pytest.mark.asyncio
async def test_saved_proposal_recovers_without_resampling_or_unpaired_history():
    r, p, h, q = case()
    from services.handlers.chat.execution_engine import _build_replay_context
    call = {'id': 'stable-save', 'name': co.SAVE, 'arguments': json.dumps({'document': document()})}
    await stage_response(r, q, p, [], 0, 0, '', [call], 'tool_calls', _build_replay_context)
    original = copy.deepcopy(p.messages)
    fresh_r, fresh_p, fresh_h, _ = case()
    fresh_r.task_bootstrap = r.task_bootstrap
    fresh_r.task_id, fresh_r.conversation_id, fresh_r.turn_id = r.task_id, r.conversation_id, r.turn_id
    fresh_r.checkpoint_base = json.loads(json.dumps(r.checkpoint_base))
    fresh_p.messages = original
    fresh_p.execution_context = p.execution_context
    r, p, h = fresh_r, fresh_p, fresh_h
    async def no_model(**kwargs):
        pytest.fail('Pure pending registration must resume through current validators')
        yield
    _, blocks, _ = await run_content_loop(r, p, h, q, no_model)
    assert co.catalog(r.task_execution_state) and blocks[-1]['content_artifact_id']
    assert p.messages[:len(original)] == original
    assert r.checkpoint_base[KEY]['phase'] == 'committed'
    assert pending_response(r, q) is None
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_input_and_business_batch_are_never_automatically_replayed():
    from types import SimpleNamespace
    from services.handlers.chat.execution_engine import _build_replay_context
    r, p, h, q = case()
    call = {'id': 'save', 'name': co.SAVE, 'arguments': json.dumps({'document': document()})}
    await stage_response(r, q, p, [], 0, 0, '', [call], None, _build_replay_context)
    changed = replace(q, context_anchor=SimpleNamespace(input_message_id='new', base_revision=1))
    assert pending_response(r, changed) is None
    assert r.checkpoint_base[KEY]['phase'] == 'superseded'
    await stage_response(r, q, p, [], 0, 0, '', [call, {'id': 'business', 'name': 'generate_image', 'arguments': '{}'}], None, _build_replay_context)
    assert pending_response(r, q) is None
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_actual_actor_events_match_the_frontend_replay_fixture():
    from pathlib import Path
    from services.handlers.chat.actor_sink import ActorWebSink
    from tests.test_chat_actor_sink import _DB, _WebSocket, _delivery, _DeliveryStore
    fixture = json.loads((Path(__file__).parent / 'fixtures/content_delivery_wire.json').read_text())
    r, p, h, q = case()
    events = _DeliveryStore()
    sink = ActorWebSink(_DB([]), _delivery(), r.cancellation_event, _WebSocket(), delivery_store=events)
    await sink.start()
    async def stream(**kwargs):
        yield StreamChunk(content=fixture['proposal']['display_prefix'])
        yield model_save_chunk(fixture['proposal'])
    _, blocks, _ = await run_content_loop(r, p, h, q, stream, sink=sink)
    observed = []
    for event, payload in events.events:
        if event == 'message_chunk' or (event == 'content_block_add' and payload['block'].get('content_artifact_id')):
            value = copy.deepcopy(payload)
            if event == 'content_block_add':
                value['block']['content_artifact_id'] = 'canonical-artifact'
            observed.append({'event_type': event, 'payload': value, 'delivery_seq': len(observed) + 1})
    assert observed == fixture['events']
    assert sink._text == blocks[-1]['text']
    assert sink._blocks[-1]['text'] == blocks[-1]['text']
    assert len([b for b in sink._blocks if b.get('type') == 'text']) == 1
    part, = build_content_parts(blocks, fallback_text='')
    assert part.text == fixture['events'][-1]['payload']['block']['text']


@pytest.mark.asyncio
@pytest.mark.parametrize('after_confirmed_save', [False, True])
async def test_journal_and_artifact_follow_the_existing_checkpoint_commit_fence(after_confirmed_save):
    from services.conversation_commands import CommandType, ConversationCommand
    from services.conversation_state import ConversationPauseRequested
    async def checkpoint(point, payload):
        if point == SafePoint.AFTER_TOOL:
            if not after_confirmed_save:
                raise RuntimeError('checkpoint rejected')
            r.push(ConversationCommand('pause', CommandType.PAUSE, r.conversation_id, r.task_id, r.turn_id))
        return {'outcome': 'saved'}
    r, p, h, q = case(checkpoint)
    original = copy.deepcopy(p.messages)
    async def stream(**kwargs):
        yield model_save_chunk(document())
    error = ConversationPauseRequested if after_confirmed_save else RuntimeError
    with pytest.raises(error):
        await run_content_loop(r, p, h, q, stream)
    if after_confirmed_save:
        assert co.catalog(r.task_execution_state)
        assert r.checkpoint_base[KEY]['phase'] == 'committed'
        assert r.checkpoint_base['content_blocks'][-1]['content_artifact_id']
        assert len([m for m in p.messages if m.get('tool_call_id')]) == 1
    else:
        assert not co.catalog(r.task_execution_state)
        assert r.checkpoint_base[KEY]['phase'] == 'proposed'
        assert p.messages == original or all(not m.get('tool_calls') for m in p.messages)
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_input_after_rejected_draft_does_not_inherit_old_visible_constraint():
    from types import SimpleNamespace
    from services.handlers.chat.execution_engine import _build_replay_context
    from services.task_execution.content_delivery import finish_response
    r, p, h, q = case()
    call = {'id': 'old-save', 'name': co.SAVE, 'arguments': json.dumps({'document': document()})}
    await stage_response(r, q, p, [], 0, 0, '旧的可见原文', [call], None, _build_replay_context)
    finish_response(r, None, {'ok': False, 'code': co.DRAFT_CONFLICT}, request=q)
    # Authenticated subsequent input, not a model-provided substitute.
    changed = replace(q, context_anchor=SimpleNamespace(input_message_id='new-input', base_revision=1))
    new_draft = document() | {'display_prefix': '新要求对应的前言。'}
    new_call = {**call, 'id': 'new-save', 'arguments': json.dumps({'document': new_draft})}
    await stage_response(r, changed, p, [], 1, 1, '', [new_call], None, _build_replay_context)
    assert 'visible_draft' not in r.checkpoint_base[KEY]
    proposal = pending_response(r, changed)
    assert proposal and proposal['calls'][0]['id'] == 'new-save'
    assert pending_response(r, q) is None


@pytest.mark.asyncio
async def test_text_only_new_content_mistake_gets_specific_repair_without_execution_grant():
    from tests.test_task_execution_model_bridge import call
    from tests.test_model_directed_loop import execute
    r, p, h, q = case()
    await execute(r, p, h, q, [call(co.SAVE, {'document': document(), 'new_content': True}, 'invalid-new')])
    failure = json.loads(p.messages[-1]['content'])
    assert failure['ok'] is False
    assert '删除 new_content' in failure['message']
    assert '不要改成 continue_execution=true' in failure['message']
    assert not co.catalog(r.task_execution_state)
    h._execute_tool_calls.assert_not_awaited()


def test_advertised_new_content_dependency_matches_runtime_validation():
    import jsonschema
    parameters = co.save_parameters()
    invalid = {'document': document(), 'new_content': True}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, parameters)
    jsonschema.validate({'document': document()}, parameters)
    jsonschema.validate({'document': document(), 'new_content': None}, parameters)
    jsonschema.validate({**invalid, 'continue_execution': True}, parameters)
    revision = {'base': {'artifact_id': 'saved', 'item_index': 1},
                'updates': [{'item_index': 1, 'item': {'body': '修订正文'}}], 'continue_execution': True}
    jsonschema.validate(revision, parameters)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**revision, 'new_content': True}, parameters)
    with pytest.raises(ValueError, match='CONTENT_NEW_SOURCE_INVALID'):
        co.SaveContent.model_validate(invalid)
