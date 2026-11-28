"""Cross-task draft reads, contracts and pre-adoption checks share one source."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator

from services.handlers.chat import content_tools
from services.task_execution import content_objects as co, content_revision as cr, request_resolution as rr
from services.task_execution import content_source as sources, saved_image_revision as revision
from services.task_execution.contracts import TaskState
from services.task_execution.reference_prompt_format import encode_item
from tests.test_saved_revision_workflow import customer_case, tools_for, workflow
from tests.test_content_revision_contract import configure_reference, read
from tests.test_content_objects import ref, registered
from tests.test_model_directed_loop import execute
from tests.model_update_fixtures import call


@pytest.fixture(autouse=True)
def scoped_identity(monkeypatch):
    monkeypatch.setattr(content_tools, 'check_source_scope', lambda *args: None)


async def cross_case(default='other'):
    r, p, h, q, row = await customer_case(size={'resolution': '2K'})
    row = configure_reference(r, row)
    original = r.task_execution_state
    # Give the same referenced document explicit saved 1K options.
    artifacts = list(original.artifacts)
    pos = next(i for i, a in enumerate(artifacts) if a.id == row['artifact_id'])
    record = co.ContentRecord.model_validate_json(artifacts[pos].inline_text)
    record = record.model_copy(update={'document': record.document.model_copy(update={'items': tuple(
        i.model_copy(update={'options': {'aspect_ratio': '1:1', 'resolution': '1K'}}) for i in record.document.items)})})
    raw = record.model_dump_json()
    artifacts[pos] = artifacts[pos].model_copy(update={'inline_text': raw, 'sha256': hashlib.sha256(raw.encode()).hexdigest()})
    original = original.model_copy(update={'artifacts': tuple(artifacts)})
    row = co.catalog(original)[0]
    r.checkpoint_base[rr.KEY]['target']['root_task_id'] = str(original.root_task_id)
    r.checkpoint_base[cr.KEY] = {}
    r.task_content_groups = [{**row, 'root_task_id': str(original.root_task_id)}]
    other, *_ = await registered()
    other_state = other.task_execution_state.model_copy(update={
        'actor_user_id': original.actor_user_id, 'conversation_id': original.conversation_id,
        'org_id': original.org_id, 'execution_identity': original.execution_identity})
    # Distinct historical documents must have distinct immutable artifact IDs.
    names = {a.id: a.id + '-other' for a in co.objects(other_state)}
    other_state = other_state.model_copy(update={
        'artifacts': tuple(a.model_copy(update={'id': names.get(a.id, a.id)}) for a in other_state.artifacts),
        'results': tuple(x.model_copy(update={'artifact_ids': tuple(names.get(i, i) for i in x.artifact_ids)}) for x in other_state.results)})
    other_row = co.catalog(other_state)[0]
    r.task_content_groups.append({**other_row, 'root_task_id': str(other_state.root_task_id)})
    states = {str(original.root_task_id): original, str(other_state.root_task_id): other_state}
    async def load(root):
        return {'state': states[str(root)].model_dump(mode='json')}
    r.read_content_root = AsyncMock(side_effect=load)
    r.task_execution_state = None
    r.task_followup_candidate = None if default == 'none' else {'state': other_state.model_dump(mode='json')}
    return r, p, h, q, row, original, other_row, states


def patch(r, row, original, *, partial=True):
    _, _, item = co.load(original, ref(row, 3))
    old = encode_item(item.model_dump(mode='json'))
    sections = {'reference_usage': old['presentation']['reference_usage'] + '\n按2K输出。'}
    if not partial:
        sections = {**old['presentation'], **sections}
    args = {'base': {'artifact_id': row['artifact_id'], 'item_index': 3}, 'updates': [{'item_index': 3, 'item': {
        'prompt': {**old['prompt'], 'positive': old['prompt']['positive'] + '\n按2K输出。'},
        'presentation': sections, 'options': {'aspect_ratio': '1:1', 'resolution': '2K'}}}], 'continue_execution': True}
    from services.task_execution.image_reference_catalog import for_runtime
    return call(co.SAVE, for_runtime(r).encode(args), 'revision')


@pytest.mark.parametrize('default', ['none', 'other'])
@pytest.mark.parametrize('partial', [False, True])
async def test_cross_task_contract_and_section_merge_use_exact_read_source(default, partial):
    r, p, h, q, row, original, _, _ = await cross_case(default)
    owner = r.task_followup_candidate
    await sources.prepare_selected(r, q, p.execution_context)
    assert r.task_followup_candidate is owner and r.task_execution_state is None
    before = tools_for(r, q)
    assert co.SAVE not in before and workflow(before[content_tools.READ])['stage'] == 'read_required'
    result = await read(r, p, h, q, row, 3)
    assert result['ok'] and result['content_source']['artifact_id'] == row['artifact_id']
    assert r.task_followup_candidate is owner
    offered = tools_for(r, q)
    state = workflow(offered[co.SAVE])
    assert state['items'][0]['required_update_fields'] == ['options', 'prompt', 'presentation']
    for example in offered[co.SAVE]['parameters']['examples']:
        Draft202012Validator(offered[co.SAVE]['parameters']).validate(example)
    c = patch(r, row, original, partial=partial)
    public = json.loads(c['arguments'])
    Draft202012Validator(offered[co.SAVE]['parameters']).validate(public)
    from services.task_execution.reference_boundary import for_runtime, NativeToolCall
    native = NativeToolCall(name=co.SAVE, arguments=for_runtime(r).arguments(public, tool=co.SAVE), call_id=c['id'])
    assert not await sources.prepare_calls(r, q, [native], p.execution_context)
    assert cr.check_call(r, q, native) is None
    merged = cr.materialize_sections(r, q, native)
    assert revision.check(r, q, [merged], sources.call_state(r, merged)) == {}
    value = co.SaveContent.model_validate_json(merged['arguments'])
    assert value.base.artifact_id == row['artifact_id'] and value.updates[0].item_index == 3
    assert value.updates[0].item.reference_sections is None
    assert r.task_followup_candidate is owner and co.catalog(original)[0]['revision'] == row['revision']
    h._execute_tool_calls.assert_not_awaited()


async def test_reading_another_task_never_changes_selected_source():
    r, p, h, q, row, original, other_row, _ = await cross_case()
    await sources.prepare_selected(r, q, p.execution_context)
    owner = r.task_followup_candidate
    await read(r, p, h, q, row, 3)
    await read(r, p, h, q, other_row, 1)
    assert r.task_followup_candidate is owner
    assert sources.state_for(r, ref(row, 3)).root_task_id == original.root_task_id
    assert workflow(tools_for(r, q)[co.SAVE])['items'][0]['content_source']['item_index'] == 3


async def test_failed_revision_does_not_leave_temporary_owner():
    r, p, h, q, row, original, _, _ = await cross_case('none')
    await read(r, p, h, q, row, 3)
    old = co.load(original, ref(row, 3))[2]
    c = call(co.SAVE, {'base': ref(row, 3), 'updates': [{'item_index': 3,
        'item': {'body': old.body, 'options': old.options}}], 'continue_execution': True})
    await execute(r, p, h, q, [c])
    assert json.loads(p.messages[-1]['content'])['code'] == 'CONTENT_IMAGE_REVISION_REQUIRED'
    assert r.task_followup_candidate is None and r.task_execution_state is None
    h._execute_tool_calls.assert_not_awaited()


@pytest.mark.parametrize('failure', [RuntimeError('checkpoint unavailable'), asyncio.CancelledError()])
async def test_adoption_failure_or_cancel_restores_only_temporary_candidate(monkeypatch, failure):
    r, p, h, q, row, original, _, _ = await cross_case()
    await read(r, p, h, q, row, 3)
    owner = r.task_followup_candidate
    c = patch(r, row, original)
    async def adopt(*args, **kwargs):
        assert r.task_followup_candidate is owner
        assert kwargs['source']['state']['root_task_id'] == str(original.root_task_id)
        raise failure
    monkeypatch.setattr('services.task_execution.direct_loop.resume_from_model', adopt)
    with pytest.raises(type(failure)):
        await execute(r, p, h, q, [c])
    assert r.task_followup_candidate is owner and r.task_execution_state is None
    h._execute_tool_calls.assert_not_awaited()


async def test_restart_reloads_source_and_keeps_input_read_proof():
    r, p, h, q, row, original, _, _ = await cross_case('none')
    await read(r, p, h, q, row, 3)
    proof = copy.deepcopy(r.checkpoint_base[cr.KEY])
    r._resolved_content_sources = None
    await sources.prepare_selected(r, q, p.execution_context)
    assert r.checkpoint_base[cr.KEY] == proof and r.task_followup_candidate is None
    assert sources.state_for(r, ref(row, 3)).root_task_id == original.root_task_id
    assert not cr.pending_reads(r, q)


async def test_source_failure_blocks_save_and_generate_without_changing_owner():
    r, p, h, q, row, _, _, _ = await cross_case()
    owner = r.task_followup_candidate
    r.read_content_root = AsyncMock(return_value=None)
    await sources.prepare_selected(r, q, p.execution_context)
    offered = tools_for(r, q)
    assert co.SAVE not in offered and 'generate_image' not in offered
    assert 'CONTENT_SOURCE_UNAVAILABLE' in offered[content_tools.READ]['description']
    assert r.task_followup_candidate is owner


@pytest.mark.parametrize('failure,code', [
    (PermissionError('denied'), 'CONTENT_SOURCE_SCOPE_DENIED'),
    (RuntimeError('read unavailable'), 'CONTENT_SOURCE_LOAD_FAILED'),
])
async def test_failed_source_load_has_distinct_recovery_and_preserves_owner(failure, code):
    r, p, h, q, row, _, _, _ = await cross_case()
    owner = r.task_followup_candidate
    r.read_content_root = AsyncMock(side_effect=failure)
    await sources.prepare_selected(r, q, p.execution_context)
    assert str(r._content_source_error) == code
    assert co.SAVE not in tools_for(r, q)
    assert r.task_followup_candidate is owner


async def test_scope_denial_or_changed_version_cannot_use_cached_read():
    r, p, h, q, row, original, _, states = await cross_case()
    await read(r, p, h, q, row, 3)
    owner = r.task_followup_candidate
    root = str(original.root_task_id)
    states[root] = original.model_copy(update={'conversation_id': __import__('uuid').uuid4()})
    with pytest.raises(sources.ContentSourceError, match='CONTENT_SOURCE_SCOPE_DENIED'):
        await sources.resolve(r, ref(row, 3), context=p.execution_context, refresh=True)
    states[root] = original
    r.checkpoint_base[rr.KEY]['target']['revision'] = row['revision'] + 1
    with pytest.raises(sources.ContentSourceError, match='CONTENT_BASE_VERSION_CHANGED'):
        await sources.resolve(r, ref(row, 3), context=p.execution_context, refresh=True)
    assert r.task_followup_candidate is owner and r.task_execution_state is None


async def test_new_input_invalidates_cached_source_and_read_proof():
    from uuid import uuid4
    r, p, h, q, row, original, _, _ = await cross_case('none')
    await read(r, p, h, q, row, 3)
    assert sources.state_for(r, ref(row, 3)) is not None
    r.checkpoint_base[rr.CONTEXT]['input_message_id'] = str(uuid4())
    assert sources.state_for(r, ref(row, 3)) is None
    await sources.prepare_selected(r, q, p.execution_context)
    assert cr.pending_reads(r, q) and r.task_followup_candidate is None


async def test_successful_reread_clears_failed_source_fact():
    r, p, h, q, row, _, _, _ = await cross_case()
    reader = r.read_content_root
    r.read_content_root = AsyncMock(side_effect=RuntimeError('temporarily unavailable'))
    await sources.prepare_selected(r, q, p.execution_context)
    assert r._content_source_error is not None
    r.read_content_root = reader
    await read(r, p, h, q, row, 3)
    assert r._content_source_error is None and co.SAVE in tools_for(r, q)
