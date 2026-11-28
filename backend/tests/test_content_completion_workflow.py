"""Completion is a committed effect, including stops before task adoption."""
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from schemas.message import TextPart
from services.adapters.types import StreamChunk, ToolCallDelta
from services.handlers.chat import content_tools
from services.task_execution import content_objects as co, content_revision, request_resolution as rr
from services.task_execution.content_completion import phase, saved_revision
from services.task_execution.image_reference_catalog import for_runtime
from tests.semantic_fixtures import semantic_request
from tests.test_content_objects import registered, ref, run_content_loop
from tests.test_content_revision_contract import configure_reference
from tests.test_model_directed_loop import execute
from tests.test_saved_revision_workflow import customer_case, tools_for, workflow
from tests.model_update_fixtures import call, model_arguments


async def revision_case(reference=False, adopted=True, intent='write'):
    r, p, h, q, row = await registered()
    if reference:
        row = configure_reference(r, row)
    r.checkpoint_base.pop(content_revision.KEY, None)
    r.checkpoint_base[rr.CONTEXT] = {'input_message_id': str(uuid4()), 'requests': [], 'image_reference_version': 1}
    target = {'kind': 'content', 'basis': 'explicit_reference', 'artifact_id': row['artifact_id'],
              'revision': row['revision'], 'item_indices': [3]}
    q = semantic_request(q, intent, target=target, size={'resolution': '4K'})
    q = replace(q, content=[TextPart(text='第三个清楚点，改成四千，其他别改。先不用出图。')])
    rr.freeze(r, q, target)
    if not adopted:
        r.task_content_groups = rr.groups(r)
        r.task_execution_state = None
    p.messages = [{'role': 'user', 'content': q.content[0].text}]
    h.org_id = None
    h.db = None
    return r, p, h, q, row


@pytest.mark.asyncio
@pytest.mark.parametrize('adopted', [False, True])
@pytest.mark.parametrize('reference', [False, True])
async def test_bare_false_saved_claim_is_held_and_bounded_without_adoption(reference, adopted):
    r, p, h, q, row = await revision_case(reference, adopted)
    rounds = []
    async def stop(**kwargs):
        rounds.append(kwargs)
        yield StreamChunk(content='第三个已改成四千并保存好了。', finish_reason='stop')
    sink, blocks, totals = await run_content_loop(r, p, h, q, stop)
    assert len(rounds) == 2
    assert '保存好了' not in sink.text + totals.text
    assert '尚未保存成功' in totals.text
    assert phase(r, q)['stage'] == 'read_required'
    assert co.catalog(r.task_execution_state) == ([] if not adopted else [row])
    assert blocks[-1]['content_incomplete']
    r.checkpoint_base = json.loads(json.dumps(r.checkpoint_base))
    from services.task_execution.text_delivery import check
    assert check(r, q, {'purpose': 'explanation'})['stopped']
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('reference', [False, True])
async def test_actual_read_then_model_stop_then_save_advances_same_main_loop(monkeypatch, reference):
    r, p, h, q, row = await revision_case(reference)
    monkeypatch.setattr(content_tools, 'check_source_scope', lambda *args: None)
    before = co.load(r.task_execution_state, ref(row, 3))[1]
    p.core_tools = [content_tools.READ_SCHEMA, co.save_schema(r)]
    rounds = []
    async def stream(**kwargs):
        rounds.append(kwargs)
        functions = {t['function']['name']: t['function'] for t in kwargs['tools']}
        if len(rounds) == 1:
            args = workflow(functions[content_tools.READ])['read_calls'][0]['arguments']
            name = content_tools.READ
        elif len(rounds) == 2:
            assert phase(r, q)['stage'] == 'revision_required'
            assert 'revision_required' in functions[co.SAVE]['description']
            yield StreamChunk(content='已经保存好了。', finish_reason='stop')
            return
        elif len(rounds) == 3:
            name = co.SAVE
            item = {'options': {**before.items[2].options, 'resolution': '4K'}}
            if reference:
                from services.task_execution.reference_prompt_format import encode_item
                original = encode_item(before.items[2].model_dump(mode='json'))
                item.update(prompt={**original['prompt'], 'positive': original['prompt']['positive'] + '\n4K清晰度。'},
                            presentation={**original['presentation'], 'visual_settings': original['presentation']['visual_settings'] + '\n4K清晰度。'})
            else:
                item['body'] = before.items[2].body + '\n4K清晰度。'
            args = {'base': for_runtime(r).encode({'artifact_id': row['artifact_id'], 'item_index': 3}), 'updates': [
                {'item_index': 3, 'item': item}],
                'continue_execution': False}
        else:
            assert len(rounds) == 4 and phase(r, q) is None, p.messages[-2:]
            yield StreamChunk(content='第三个已保存，其他两个保留原样。', finish_reason='stop')
            return
        if name == co.SAVE:
            args = model_arguments(args)
        Draft202012Validator(functions[name]['parameters']).validate(args)
        yield StreamChunk(tool_calls=[ToolCallDelta(index=0, id=f'completion-{len(rounds)}', name=name,
                            arguments_delta=json.dumps(args, ensure_ascii=False))], finish_reason='tool_calls')
    sink, blocks, _ = await run_content_loop(r, p, h, q, stream)
    assert len(rounds) == 3
    assert '已经保存好了' not in sink.text
    latest, = co.catalog(r.task_execution_state)
    document = co.load(r.task_execution_state, ref(latest, 3))[1]
    assert document.items[:2] == before.items[:2]
    assert '4K清晰度。' in document.items[2].body
    assert document.items[2].options['resolution'] == '4K'
    assert latest['revision'] == row['revision'] + 1
    assert saved_revision(r, q, rr.binding(r)['target'])['artifact_id'] == latest['artifact_id']
    assert phase(r, q) is None
    assert not r.task_execution_state.operations


@pytest.mark.asyncio
@pytest.mark.parametrize('reference', [False, True])
async def test_final_tool_projection_preserves_required_phase_and_valid_examples(reference):
    r, p, h, q, row = await revision_case(reference)
    offered = tools_for(r, q)
    assert workflow(offered[content_tools.READ])['stage'] == 'read_required'
    from tests.test_content_revision_contract import read
    h.db = None
    # Real dispatch and read receipt, not a synthetic journal.
    from unittest.mock import patch
    with patch.object(content_tools, 'check_source_scope', lambda *args: None):
        await read(r, p, h, q, row, 3)
    offered = tools_for(r, q)
    function = offered[co.SAVE]
    assert workflow(function)['stage'] == 'revision_required'
    assert '4K' in function['description']
    for example in function['parameters']['examples']:
        Draft202012Validator(function['parameters']).validate(example)


@pytest.mark.asyncio
async def test_saved_proof_needs_committed_artifact_and_current_input():
    r, p, h, q, row = await revision_case()
    value = co.SaveContent.model_validate({'base': ref(row, 3), 'updates': [
        {'item_index': 3, 'item': {'options': {'resolution': '4K'}}}]})
    original = co.objects(r.task_execution_state)[0]
    fake = original.model_copy(update={'id': 'content-not-committed'})
    content_revision.record_save(r, q, value, fake)
    assert saved_revision(r, q, rr.binding(r)['target']) is None
    r.checkpoint_base[rr.CONTEXT]['input_message_id'] = str(uuid4())
    assert saved_revision(r, q, rr.binding(r)['target']) is None


@pytest.mark.asyncio
async def test_pending_revision_budget_stop_does_not_synthesize_success(monkeypatch):
    from services.handlers.chat.execution_engine import _apply_budget_stop
    from services.handlers.chat.stream_session import StreamTotals
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    from unittest.mock import AsyncMock
    r, p, h, q, row = await revision_case(adopted=False)
    synthesize = AsyncMock(return_value='已保存好了。')
    monkeypatch.setattr('services.agent.stop_policy.synthesize_wrap_up', synthesize)
    from types import SimpleNamespace
    p.budget = SimpleNamespace(stop_reason='model_rounds')
    blocks, totals, sink = [], StreamTotals(), CollectingExecutionSink()
    await _apply_budget_stop(p, totals, blocks, sink, runtime=r, request=q)
    synthesize.assert_not_awaited()
    assert blocks[0]['content_incomplete'] and '尚未保存成功' in totals.text


def test_new_writing_hides_execution_only_fields_and_materials_have_one_location():
    tools = content_tools.model_tools([co.SCHEMA], writing_new=True, saved_text_available=True)
    params = tools[0]['function']['parameters']
    assert not {'new_content', 'base', 'updates', 'message_source'} & params['properties'].keys()
    for example in params['examples']:
        Draft202012Validator(params).validate(example)
        assert not example.get('continue_execution')


@pytest.mark.asyncio
async def test_saved_revision_still_needs_actual_image_submission_after_model_stops(monkeypatch):
    from tests.test_content_revision_contract import read
    from services.tools import build_legacy_catalog
    r, p, h, q, row = await customer_case(size={'resolution': '2K'})
    h.org_id = None
    h.db = None
    monkeypatch.setattr(content_tools, 'check_source_scope', lambda *args: None)
    await read(r, p, h, q, row, 3)
    old = co.load(r.task_execution_state, ref(row, 3))[2]
    await execute(r, p, h, q, [call(co.SAVE, {'base': for_runtime(r).encode({
        'artifact_id': row['artifact_id'], 'item_index': 3}), 'updates': [{'item_index': 3, 'item': {
        'body': old.body + '\n2K清晰度。', 'options': {**old.options, 'resolution': '2K'}}}],
        'continue_execution': True}, 'saved-before-stop')])
    latest, = co.catalog(r.task_execution_state)
    pending = phase(r, q)
    assert pending['stage'] == 'execute_required'
    assert pending['content_source'] == {'artifact_id': latest['artifact_id'], 'item_index': 3}
    p.core_tools = [build_legacy_catalog().require('generate_image').to_schema()]
    p.messages = [{'role': 'user', 'content': '第三个做成一张图，正方形，一千就行。'}]
    rounds = []
    async def stop(**kwargs):
        rounds.append(kwargs)
        yield StreamChunk(content='图片已经提交了。', finish_reason='stop')
    _, blocks, totals = await run_content_loop(r, p, h, q, stop)
    assert len(rounds) == 2
    assert '已经提交' not in totals.text
    assert '尚未提交' in totals.text and blocks[-1]['content_incomplete']
    assert not r.task_execution_state.operations
    assert co.catalog(r.task_execution_state)[0]['artifact_id'] == latest['artifact_id']


@pytest.mark.asyncio
async def test_previous_drafts_cannot_satisfy_new_unsaved_text_request():
    r, p, h, q, row = await revision_case()
    target = {'kind': 'current', 'basis': 'current_input'}
    q = semantic_request(q, 'write', target=target,
                         media={'tool': 'generate_image', 'wait_for': [1, 2, 3]})
    rr.freeze(r, q, target)
    assert phase(r, q)['stage'] == 'author_required'
    async def stop(**kwargs):
        yield StreamChunk(content='三个绿叶方案都保存好了。', finish_reason='stop')
    _, blocks, totals = await run_content_loop(r, p, h, q, stop)
    assert '保存好了' not in totals.text and blocks[-1]['content_incomplete']
    assert co.catalog(r.task_execution_state) == [row]


@pytest.mark.asyncio
async def test_mixed_execute_keeps_unchanged_slot_and_uses_latest_committed_revision(monkeypatch):
    from tests.test_content_revision_contract import read
    r, p, h, q, row = await customer_case(indices=(1, 3), size_rules=[
        {'scope': 'items', 'item_indices': [3], 'size': {'resolution': '2K'}}])
    h.db = None
    monkeypatch.setattr(content_tools, 'check_source_scope', lambda *args: None)
    await read(r, p, h, q, row, 3)
    old = co.load(r.task_execution_state, ref(row, 3))[2]
    await execute(r, p, h, q, [call(co.SAVE, {'base': ref(row, 3), 'updates': [
        {'item_index': 3, 'item': {'body': old.body + '\n2K清晰度。',
                                 'options': {**old.options, 'resolution': '2K'}}}],
        'continue_execution': True}, 'mixed-save')])
    latest, = co.catalog(r.task_execution_state)
    assert latest['revision'] == row['revision'] + 1
    assert saved_revision(r, q, rr.binding(r)['target']) == {
        'artifact_id': latest['artifact_id'], 'item_index': 1}
    assert phase(r, q)['content_source']['artifact_id'] == latest['artifact_id']
