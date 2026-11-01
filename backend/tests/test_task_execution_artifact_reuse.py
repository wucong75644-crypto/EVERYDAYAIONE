"""Existing text is actual evidence, not an intent label waiving prerequisites."""
import copy
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.task_execution.artifact_reader import TaskArtifactReader
from services.task_execution.contracts import Artifact, TaskError
from tests.test_task_execution_methods import bound, document
from tests.test_task_execution_model_bridge import setup_case, execute, call


def source_artifact(text=None):
    text = text or document()
    return Artifact(id='saved-plan', kind='text', reference='task-text:prior:saved-plan',
        sha256=hashlib.sha256(text.encode()).hexdigest(), source_task_id=uuid4(),
        source_call_id='prior-call', step_id='prior-planning', inline_text=text)


@pytest.mark.asyncio
async def test_saved_plan_reuse_completes_phase_without_rewriting_it(monkeypatch):
    state, skills = await bound(monkeypatch)
    runtime, prepared, handler, sink = setup_case(initial=state)
    runtime.skill_runtime = skills
    source = source_artifact()
    runtime.task_artifact_reader = SimpleNamespace(load_text=AsyncMock(return_value=source))
    blocks = []
    await execute(runtime, prepared, handler, sink, [call('reuse_task_text', {
        'expected_revision': state.revision, 'step_id': 'planning',
        'source_logical_task_id': str(uuid4()), 'source_artifact_id': source.id}, 'reuse')], blocks)
    reused = runtime.task_execution_state.artifacts[0]
    assert reused.inline_text == source.inline_text and reused.reference == source.reference
    assert reused.source_task_id == source.source_task_id and reused.step_id == 'planning'
    assert 'reference-prompts-v1' in reused.validations
    assert not any(b.get('type') == 'text' for b in blocks)  # no duplicate planning delivery
    await execute(runtime, prepared, handler, sink, [call('complete_task_step', {
        'expected_revision': runtime.task_execution_state.revision, 'step_id': 'planning',
        'artifact_ids': [reused.id]}, 'complete')], blocks)
    assert 'report' not in runtime.skill_runtime.active
    assert 'generate_image' in runtime.skill_runtime.effective_allowed_tool_names


@pytest.mark.asyncio
async def test_reuse_does_not_trust_source_validation_tags_for_new_method(monkeypatch):
    state, skills = await bound(monkeypatch)
    runtime, prepared, handler, sink = setup_case(initial=state)
    source = source_artifact('只是一句摘要，不是完整规划')
    source = source.model_copy(update={'validations': ('artifact.exists', 'reference-prompts-v1')})
    runtime.task_artifact_reader = SimpleNamespace(load_text=AsyncMock(return_value=source))
    await execute(runtime, prepared, handler, sink, [call('reuse_task_text', {
        'expected_revision': state.revision, 'step_id': 'planning',
        'source_logical_task_id': str(uuid4()), 'source_artifact_id': source.id})], [])
    reused = runtime.task_execution_state.artifacts[0]
    assert reused.validations == ('artifact.exists',)


@pytest.mark.asyncio
async def test_reuse_checkpoint_failure_keeps_gate_closed_and_discards_import(monkeypatch):
    state, skills = await bound(monkeypatch)
    runtime, prepared, handler, sink = setup_case(AsyncMock(return_value={'outcome': 'disabled'}), state)
    runtime.skill_runtime = skills
    runtime.task_artifact_reader = SimpleNamespace(load_text=AsyncMock(return_value=source_artifact()))
    before = copy.deepcopy(prepared.messages)
    with pytest.raises(RuntimeError, match='NOT_SAVED'):
        await execute(runtime, prepared, handler, sink, [call('reuse_task_text', {
            'expected_revision': state.revision, 'step_id': 'planning',
            'source_logical_task_id': str(uuid4()), 'source_artifact_id': 'saved-plan'})], [])
    assert runtime.task_execution_state is state and prepared.messages == before
    assert skills.effective_allowed_tool_names == frozenset()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['checksum', 'identity', 'ownership_lost'])
async def test_reader_rejects_bad_persisted_content_or_lost_owner(bad):
    from tests.test_task_execution_reducer import state
    from services.conversation_state import ConversationStopRequested
    source, root = source_artifact(), uuid4()
    artifact = source.model_dump(mode='json')
    if bad == 'checksum':
        artifact['inline_text'] = 'modified after checksum'
    data = {'outcome': 'found', 'artifacts': [
        {'source_logical_task_id': str(root if bad != 'identity' else uuid4()), 'artifact': artifact}]}
    if bad == 'ownership_lost':
        data = {'outcome': 'ownership_lost'}
    request = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(data=data)))
    reader = TaskArtifactReader(SimpleNamespace(rpc=lambda *args: request), task_id=uuid4(), execution_token=uuid4())
    with pytest.raises(ConversationStopRequested if bad == 'ownership_lost' else TaskError):
        await reader.load_text(state(), source_logical_task_id=root, artifact_id=source.id)
