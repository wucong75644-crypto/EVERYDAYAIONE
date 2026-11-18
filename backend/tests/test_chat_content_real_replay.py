"""Opt-in replay of existing records and originals. No provider or production writes."""
import base64
import copy
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from services.agent.agent_result import AgentResult
from services.agent.conversation_tool_mixin import ConversationToolMixin
from services.file_executor import FileExecutor
from services.handlers.chat import content_tools
from services.handlers.chat_image_request import ChatImageInputResolver
from services.handlers.chat_tool_result_mixin import ChatToolResultMixin, ToolResultContext
from services.task_execution.bootstrap import TaskBootstrap
from services.task_execution.contracts import TaskState
from services.task_execution.image_resources import create_resolver
from services.tools.catalog import build_capability_catalog
from services.tools import ToolCall
from services.tools.policy import ToolPolicy
from services.tools.result import ToolResult
from tests.test_model_directed_loop import case, execute
from tests.test_task_execution_model_bridge import call


@pytest.fixture
def real_replay(monkeypatch, tmp_path, mock_db):
    export = os.getenv('CHAT_CONTENT_REAL_REPLAY')
    if not export:
        pytest.skip('Requires private six-item record and original reference export')
    data = json.loads(Path(export).read_text())
    task = copy.deepcopy(data['task'])
    rows = copy.deepcopy(data['messages'])
    quoted = json.loads(Path(export).with_name('quoted-source.json').read_text())
    if quoted['message']:
        rows.append(quoted['message'])
    mock_db.set_table_data('messages', rows)
    mock_db.set_table_data('tasks', [task])
    mock_db.set_table_data('conversations', [quoted['conversation']] if quoted['conversation'] else [])
    from core.config import get_settings
    monkeypatch.setattr(get_settings(), 'file_workspace_root', str(tmp_path))
    monkeypatch.setattr(get_settings(), 'chat_image_async_enabled', True)
    files = FileExecutor(str(tmp_path), task['user_id'], task['org_id'])
    for original in data['originals']:
        path = files.resolve_safe_path(original['workspace_path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(original['raw_b64']))
    saved = []

    async def save(_point, payload):
        saved.append(copy.deepcopy(payload))
        return {'outcome': 'saved'}

    runtime, prepared, handler, request = case(save)
    runtime.task_bootstrap = TypeAdapter(TaskBootstrap).validate_python(data['checkpoint']['_task_bootstrap']['input'])
    runtime.task_id = task['id']
    runtime.conversation_id = task['conversation_id']
    prepared.execution_context = replace(prepared.execution_context,
        actor_user_id=task['user_id'], workspace_owner_id=task['user_id'], org_id=task['org_id'],
        conversation_id=task['conversation_id'], task_id=task['id'])
    request = replace(request, user_id=task['user_id'], conversation_id=task['conversation_id'], task_id=task['id'])
    prepared.content_controls_available = True
    handler.db = mock_db
    handler._actor_runtime = runtime
    runtime.task_operation_store = SimpleNamespace(registry=build_capability_catalog())
    runtime.task_image_resource_resolver = create_resolver(handler, request, prepared.execution_context)
    owner = SimpleNamespace(db=mock_db, user_id=task['user_id'], workspace_user_id=task['user_id'],
        org_id=task['org_id'], conversation_id=task['conversation_id'], context_scope='user',
        task_id=task['id'], personal_context_allowed=True, execution_mode='interactive')
    generation = []
    history_outputs = []
    monkeypatch.setattr(ChatToolResultMixin, '_send_tool_result', AsyncMock())
    monkeypatch.setattr(ChatToolResultMixin, '_finish_tool_step', AsyncMock())
    monkeypatch.setattr(ChatToolResultMixin, '_audit_tool_result', Mock())

    async def accept(calls, *_args, **_kwargs):
        results = []
        for raw in calls:
            args = json.loads(raw['arguments'])
            if raw['name'] == 'get_conversation_context':
                output = ConversationToolMixin._exact_image_history(owner, args)
                native_call = ToolCall(raw['id'], raw['name'], args)
                decision = ToolPolicy(build_capability_catalog()).decide(
                    raw['name'], prepared.execution_context, args)
                assert decision.outcome == 'allow', decision.reason
                result = ToolResult.wrap(output, call=native_call, context=prepared.execution_context, decision=decision)
                delivery = ToolResultContext(task_id=request.task_id, conversation_id=request.conversation_id,
                    message_id=request.message_id, user_id=request.user_id, tool_name=raw['name'],
                    tool_call_id=raw['id'], turn=1, args=args, elapsed_ms=0)
                delivered = await ChatToolResultMixin._process_unified_result(handler, raw, result, delivery)
                model_content = delivered[1].model_content('chat')
                assert model_content == output
                history_outputs.append(model_content)
                results.append(delivered)
                continue
            else:
                assert raw['name'] == 'generate_image'
                generation.append(copy.deepcopy(args))
                output = '本地生图受理替身，未调用供应商'
            results.append((raw, AgentResult(output), False, output))
        return results

    handler._execute_tool_calls.side_effect = accept
    monkeypatch.setattr(content_tools, '_check_identity', lambda *_: None)
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context', AsyncMock())
    originals = []
    for message in data['checkpoint']['messages']:
        originals.extend(copy.deepcopy(raw) for raw in message.get('tool_calls', [])
                         if ':round:2:' in raw['id'] and raw['function']['name'] == 'generate_image')
    assert len(originals) == data.get('expected_count', 6)
    arguments = [json.loads(raw['function']['arguments']) for raw in originals]
    resolver_calls = Mock()
    # Keep Python descriptor binding when counting full original decoding.
    original_resolve = ChatImageInputResolver.resolve

    def resolve(native, references):
        resolver_calls(native, references)
        return original_resolve(native, references)

    monkeypatch.setattr(ChatImageInputResolver, 'resolve', resolve)
    return SimpleNamespace(data=data, task=task, rows=rows, db=mock_db, files=files, owner=owner,
        runtime=runtime, prepared=prepared, handler=handler, request=request, saved=saved,
        arguments=arguments, generation=generation, history_outputs=history_outputs,
        resolver_calls=resolver_calls)


def explicit_canvas(replay):
    """Simulate the user's size clarification without changing source prompts or image bytes."""
    current = next(row for row in replay.rows if row['id'] == replay.task['input_message_id'])
    parts = json.loads(current['content']) if isinstance(current['content'], str) else copy.deepcopy(current['content'])
    parts.append({'type': 'text', 'text': '输出画布比例使用1:1。'})
    current['content'] = parts
    replay.db.set_table_data('messages', replay.rows)


async def select(replay, arguments, entrypoint):
    texts = [args['prompt'] for args in arguments]
    if entrypoint in {'new', 'new_selector'}:
        ids = list(dict.fromkeys(args['source_prompt']['message_id'] for args in arguments))
        args = {'text_exacts': texts}
        if len(ids) == 1:
            args['message_id'] = ids[0]
        await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [
            call(content_tools.READ, args, 'select-batch')])
        return
    # Actual online Skill used the legacy reader and hid the new control tools.
    replay.prepared.content_controls_available = False
    ids = list(dict.fromkeys(args['source_prompt']['message_id'] for args in arguments))
    calls = [call('get_conversation_context', {'message_ids': ids, 'text_exacts': texts}, 'select-batch')]
    if entrypoint == 'legacy_parallel':
        calls = [call('get_conversation_context', {'message_ids': ids, 'text_exact': text}, f'select-{index}')
                 for index, text in enumerate(texts)]
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, calls)


def mapped_args(replay, arguments):
    from tests.test_chat_content_bundle import real_mapping
    mapping, _ = real_mapping(replay, arguments)
    return {**{key: copy.deepcopy(value) for key, value in arguments.items() if key not in {'prompt', 'source_prompt'}},
            'chat_input': mapping}


def complete_prompt(replay, arguments):
    from tests.test_chat_content_bundle import real_mapping
    return real_mapping(replay, arguments)[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('entrypoint', ['new', 'legacy', 'legacy_parallel', 'direct', 'new_selector', 'legacy_selector'])
@pytest.mark.parametrize('count', [1, 6])
async def test_real_single_and_all_preserve_original_text_and_reference(real_replay, entrypoint, count, record_property):
    replay = real_replay
    arguments = replay.arguments if count == 6 else replay.arguments[1:2]
    if entrypoint != 'direct':
        await select(replay, arguments, entrypoint)
        assert len(content_tools.checkpoint_state(replay.runtime)['selected_sources']) == count
        returned = replay.history_outputs if replay.history_outputs else [replay.prepared.messages[-1]['content']]
        record_property('history_read_count', len(returned))
        record_property('history_return_chars', sum(len(output) for output in returned))
        # Restore the real safe-point payload, including all selected fragments.
        replay.runtime.checkpoint_base[content_tools.STATE] = copy.deepcopy(replay.saved[-1][content_tools.STATE])
        if '_task_execution' in replay.saved[-1]:
            replay.runtime.task_execution_state = TaskState.model_validate(replay.saved[-1]['_task_execution'])
    calls = [call('generate_image', mapped_args(replay, original), f'image-{index}')
             for index, original in enumerate(arguments)]
    replay.prepared.content_controls_available = True
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, calls)
    assert len(replay.generation) == count, str(replay.prepared.messages[-1])[:900]
    assert replay.resolver_calls.call_count == 1
    record_property('reference_decodes_in_batch', replay.resolver_calls.call_count)
    record_property('dispatch_count', len(replay.generation))
    record_property('target_size', 'verified selected source 1:1')
    original_bytes = base64.b64decode(replay.data['originals'][0]['raw_b64'])
    recipes = [json.loads(artifact.inline_text) for artifact in replay.runtime.task_execution_state.artifacts
               if 'image.direct.recipe' in artifact.validations]
    assert len(recipes) == count
    for expected, actual, recipe in zip(arguments, replay.generation, recipes):
        complete = complete_prompt(replay, expected)
        assert actual['prompt'] == complete
        assert recipe['normalized']['prompt'] == complete
        assert actual['mode'] == expected['mode'] == 'image_to_image'
        assert actual['references'] == expected['references']
        assert recipe['resolved'][0]['content_sha256'] == hashlib.sha256(original_bytes).hexdigest()
        assert recipe['normalized']['source_prompt']['handoff']['version'] == 2
    if entrypoint == 'legacy':
        assert len(replay.history_outputs) == 1
        from services.agent.tool_result_envelope import SOURCE_SELECTION_BUDGET
        assert len(replay.history_outputs[0]) <= SOURCE_SELECTION_BUDGET
    # Native preparation for actual acceptance still rereads and verifies originals.
    first = replay.generation[0]
    await replay.runtime.task_image_resource_resolver.prepare_request(
        first, replay.runtime.task_execution_state, calls[0]['id'])
    assert replay.resolver_calls.call_count == 2


@pytest.mark.asyncio
async def test_real_original_wrong_hashes_stop_before_dispatch(real_replay):
    replay = real_replay
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [
        call('generate_image', args, f'original-{index}') for index, args in enumerate(replay.arguments)])
    assert replay.generation == []
    assert 'IMAGE_SOURCE_PROMPT_CHANGED' in str(replay.prepared.messages)
    assert '停止提交' in replay.handler._tool_result_stop_reason


@pytest.mark.asyncio
@pytest.mark.parametrize('entrypoint', ['new', 'legacy'])
@pytest.mark.parametrize('count', [1, 6])
async def test_real_revision_selects_latest_second_item(real_replay, entrypoint, count):
    replay = real_replay
    second = replay.arguments[1]
    previous_second = copy.deepcopy(second)
    revised = second['prompt'] + '\n本轮修订：封面文字保持清晰。'
    message = {'id': str(uuid4()), 'conversation_id': replay.task['conversation_id'],
        'org_id': replay.task['org_id'], 'role': 'assistant', 'status': 'completed',
        'message_kind': 'conversation', 'context_revision': replay.task['base_context_revision'] + 1,
        'content': [{'type': 'text', 'text': revised}]}
    replay.task['base_context_revision'] += 1
    catalog = replay.task['request_params'].get('_image_sources_v1')
    if catalog is not None:
        catalog['base_revision'] = replay.task['base_context_revision']
    replay.rows.append(message)
    replay.db.set_table_data('messages', replay.rows)
    replay.db.set_table_data('tasks', [replay.task])
    second['prompt'] = revised
    second['source_prompt'] = {'message_id': message['id'], 'content_index': 0}
    arguments = replay.arguments if count == 6 else [second]
    await select(replay, arguments, entrypoint)
    replay.prepared.content_controls_available = True
    updated_args = []
    for args in arguments:
        if args is not second:
            updated_args.append(mapped_args(replay, args))
            continue
        revised_args = mapped_args(replay, previous_second)
        mapping = revised_args['chat_input']
        old_source = previous_second['source_prompt']
        old_row = next(row for row in replay.rows if row['id'] == old_source['message_id'])
        old_parts = json.loads(old_row['content']) if isinstance(old_row['content'], str) else old_row['content']
        old_text = old_parts[old_source['content_index']]['text']
        a = old_text.index(previous_second['prompt']); b = a + len(previous_second['prompt'])
        split = []
        for part in mapping['parts']:
            if part['action'] == 'text' and part['start'] <= a < b <= part['end']:
                if part['start'] < a:
                    split.append({**part, 'end': a})
                split.append({'source': 0, 'start': a, 'end': b, 'action': 'superseded',
                              'target': 'prompt', 'reason': '本轮只替换正文，其他方案要求继承'})
                if b < part['end']:
                    split.append({**part, 'start': b})
            else:
                split.append(part)
        replacement = len(split)
        for part in split:
            if part['action'] == 'superseded': part['superseded_by'] = replacement
        mapping['sources'].append({'message_id': message['id'], 'content_index': 0})
        split.append({'source': 1, 'start': 0, 'end': len(revised), 'action': 'text', 'target': 'prompt'})
        mapping['parts'] = split
        updated_args.append(revised_args)
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [
        call('generate_image', args, f'revised-{index}') for index, args in enumerate(updated_args)])
    assert len(replay.generation) == count
    updated = replay.generation[1 if count == 6 else 0]['prompt']
    assert revised in updated and '负面提示词' in updated
    assert '统一保留内容' in updated
    for old, actual in zip(arguments, replay.generation):
        if old is not second: assert actual['prompt'] == complete_prompt(replay, old)
    assert replay.resolver_calls.call_count == 1


@pytest.mark.asyncio
async def test_real_wrong_hashes_still_fail_after_target_size_is_explicit(real_replay):
    replay = real_replay
    explicit_canvas(replay)
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [
        call('generate_image', {**args, 'size_requirement': {'mode': 'explicit'}}, f'wrong-hash-{index}')
        for index, args in enumerate(replay.arguments)])
    assert replay.generation == []
    assert 'IMAGE_SOURCE_PROMPT_CHANGED' in str(replay.prepared.messages)
    assert '停止提交' in replay.handler._tool_result_stop_reason


@pytest.mark.asyncio
async def test_real_partial_failure_preserves_successful_siblings(real_replay):
    replay = real_replay
    arguments = copy.deepcopy(replay.arguments)
    for index, args in enumerate(arguments):
        arguments[index] = mapped_args(replay, args)
        if index == 1:
            arguments[index]['chat_input']['sources'][0]['sha256'] = '0' * 64
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [
        call('generate_image', args, f'partial-{index}') for index, args in enumerate(arguments)])
    assert len(replay.generation) == 5
    assert [args['prompt'] for args in replay.generation] == [complete_prompt(replay, args) for index, args in enumerate(replay.arguments) if index != 1]
    assert all(args['mode'] == 'image_to_image' and args['references'] for args in replay.generation)
    assert '停止提交' in replay.handler._tool_result_stop_reason
    assert replay.resolver_calls.call_count == 1


@pytest.mark.asyncio
async def test_real_selection_change_after_checkpoint_stops_before_dispatch(real_replay):
    replay = real_replay
    explicit_canvas(replay)
    await select(replay, replay.arguments, 'new')
    frozen_inputs = [mapped_args(replay, args) for args in replay.arguments]
    for args in frozen_inputs:
        source = args['chat_input']['sources'][0]
        row = next(row for row in replay.rows if row['id'] == source['message_id'])
        parts = json.loads(row['content']) if isinstance(row['content'], str) else row['content']
        source['sha256'] = hashlib.sha256(parts[source['content_index']]['text'].encode()).hexdigest()
    source_id = replay.arguments[0]['source_prompt']['message_id']
    original = next(row for row in replay.rows if row['id'] == source_id)
    original['content'] = [{'type': 'text', 'text': '原文已经变更'}]
    replay.db.set_table_data('messages', replay.rows)
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [
        call('generate_image', args, f'changed-{index}')
        for index, args in enumerate(frozen_inputs)])
    assert replay.generation == []
    assert '停止提交' in replay.handler._tool_result_stop_reason


@pytest.mark.asyncio
async def test_real_batch_cache_rechecks_deleted_reference(real_replay):
    from services.task_execution.model_directed import record_calls
    from services.tools import ToolCall
    replay = real_replay
    explicit_canvas(replay)
    original = mapped_args(replay, replay.arguments[0])
    args, _ = content_tools.bind_mapping(replay.db, replay.task['id'], replay.prepared.execution_context,
        'generate_image', {key: value for key, value in original.items() if key != 'chat_input'}, original['chat_input'])
    calls = [ToolCall('cache-first', 'generate_image', args), ToolCall('cache-second', 'generate_image', args)]
    state = record_calls(None, replay.runtime.task_bootstrap, calls, build_capability_catalog())
    prepare = replay.runtime.task_image_resource_resolver.prepare_batch()
    await prepare(args, state, 'cache-first', binding_required=False)
    path = replay.files.resolve_safe_path(replay.data['originals'][0]['workspace_path'])
    path.unlink()
    with pytest.raises((ValueError, OSError, PermissionError)):
        await prepare(args, state, 'cache-second', binding_required=False)
    assert replay.resolver_calls.call_count == 1
    assert replay.generation == []


@pytest.mark.asyncio
@pytest.mark.parametrize('entrypoint', ['new', 'legacy'])
@pytest.mark.parametrize('status', ['completed', 'interrupted'])
async def test_real_frozen_legacy_text_uses_existing_history_proof(real_replay, entrypoint, status):
    replay = real_replay
    explicit_canvas(replay)
    source = next(row for row in replay.rows if row['id'] == replay.arguments[0]['source_prompt']['message_id'])
    source['context_revision'] = None
    source['status'] = status
    catalog = replay.task['request_params']['_image_sources_v1']
    catalog['messages'].append(copy.deepcopy(source))
    replay.db.set_table_data('messages', replay.rows)
    replay.db.set_table_data('tasks', [replay.task])
    await select(replay, replay.arguments, entrypoint)
    replay.prepared.content_controls_available = True
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [
        call('generate_image', mapped_args(replay, args), f'legacy-text-{index}')
        for index, args in enumerate(replay.arguments)])
    assert [args['prompt'] for args in replay.generation] == [complete_prompt(replay, args) for args in replay.arguments]


@pytest.mark.asyncio
async def test_real_failure_stops_model_loop_and_stays_stopped_after_restore(real_replay, monkeypatch):
    from services.handlers.chat.execution_engine import _run_loop
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from services.handlers.chat.stream_session import StreamTotals
    replay = real_replay
    calls = [call('generate_image', args, f'loop-{index}') for index, args in enumerate(replay.arguments)]
    sample = AsyncMock(return_value=('', '', calls, set()))
    monkeypatch.setattr('services.handlers.chat.execution_engine._read_turn', sample)
    await _run_loop(handler=replay.handler, request=replay.request, prepared=replay.prepared,
        cancellation_event=replay.runtime.cancellation_event, sink=CollectingExecutionSink(),
        totals=StreamTotals(), blocks=[], runtime=replay.runtime)
    sample.assert_awaited_once()
    assert replay.generation == []
    assert replay.saved[-1][content_tools.STATE]['stop_message']
    replay.runtime.checkpoint_base[content_tools.STATE] = copy.deepcopy(replay.saved[-1][content_tools.STATE])
    sample.reset_mock()
    totals = StreamTotals()
    await _run_loop(handler=replay.handler, request=replay.request, prepared=replay.prepared,
        cancellation_event=replay.runtime.cancellation_event, sink=CollectingExecutionSink(),
        totals=totals, blocks=[], runtime=replay.runtime)
    sample.assert_not_awaited()
    assert 'IMAGE_SOURCE_PROMPT_CHANGED' in totals.text
    assert replay.generation == []


@pytest.mark.asyncio
async def test_user_reference_canvas_requirement_overrides_selected_plan(real_replay):
    replay = real_replay
    current = next(row for row in replay.rows if row['id'] == replay.task['input_message_id'])
    current['content'] = [{'type': 'text', 'text': '按这六个方案生成图片，保持原图比例。'}]
    replay.db.set_table_data('messages', replay.rows)
    args = copy.deepcopy(replay.arguments[0])
    args['source_prompt'].pop('sha256')
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [call('generate_image', mapped_args(replay, args), 'keep-canvas')])
    assert replay.generation == []
    assert 'IMAGE_ASPECT_RATIO_UNSUPPORTED' in str(replay.prepared.messages)
    assert '80:79' in replay.handler._tool_result_stop_reason


@pytest.mark.asyncio
async def test_published_baseline_replays_unsupported_canvas_before_source_check(real_replay):
    import ast
    import subprocess
    from core.config import get_settings
    from services.handlers import chat_image_preparation
    from services.task_execution.image_resources import native_resolver
    replay = real_replay
    published = subprocess.run(['git', 'show', '259130580afdfda464c90ba0139b05de768ae828:backend/services/handlers/chat_image_preparation.py'],
                               check=True, capture_output=True, text=True).stdout
    tree = ast.parse(published)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'prepare_input')
    namespace = dict(vars(chat_image_preparation))
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<published-prepare-input>', 'exec'), namespace)
    native = native_resolver(replay.handler, replay.request, replay.prepared.execution_context)
    with pytest.raises(ValueError, match='IMAGE_ASPECT_RATIO_UNSUPPORTED'):
        namespace['prepare_input'](native, replay.arguments[0], scoped=None, workflow=None,
                                   settings=get_settings(), entry_checked=True)
    assert replay.generation == []


@pytest.mark.asyncio
async def test_selected_exact_pixels_cannot_silently_become_a_resolution_tier(real_replay):
    replay = real_replay
    args = copy.deepcopy(replay.arguments[0])
    original = args['prompt']
    args['prompt'] += '\n输出尺寸1254×1254像素。'
    args['source_prompt'].pop('sha256')
    row = next(row for row in replay.rows if row['id'] == args['source_prompt']['message_id'])
    parts = json.loads(row['content']) if isinstance(row['content'], str) else row['content']
    index = args['source_prompt']['content_index']
    parts[index]['text'] = parts[index]['text'].replace(original, args['prompt'])
    row['content'] = parts
    replay.db.set_table_data('messages', replay.rows)
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [call('generate_image', mapped_args(replay, args), 'exact-pixels')])
    assert replay.generation == []
    assert 'IMAGE_EXACT_SIZE_UNSUPPORTED' in replay.handler._tool_result_stop_reason


@pytest.mark.asyncio
async def test_history_discovery_does_not_force_a_second_read_for_verified_native_input(real_replay):
    replay = real_replay
    message_id = replay.arguments[0]['source_prompt']['message_id']
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [
        call(content_tools.READ, {'message_id': message_id}, 'discover-history')])
    assert not content_tools.checkpoint_state(replay.runtime).get('source_selection_failed')
    arguments = copy.deepcopy(replay.arguments)
    for args in arguments:
        args['source_prompt'].pop('sha256')
    await execute(replay.runtime, replay.prepared, replay.handler, replay.request, [
        call('generate_image', mapped_args(replay, args), f'from-history-{index}') for index, args in enumerate(arguments)])
    assert [args['prompt'] for args in replay.generation] == [complete_prompt(replay, args) for args in arguments]
    assert replay.resolver_calls.call_count == 1
