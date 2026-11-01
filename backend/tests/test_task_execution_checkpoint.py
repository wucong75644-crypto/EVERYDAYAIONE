"""New task facts use the existing Actor boundary; legacy payloads are unchanged."""
import asyncio
from types import SimpleNamespace

import pytest

from services.conversation_commands import SafePoint
from services.conversation_turn_runtime import ConversationTurnRuntime
from services.replay_checkpoint_store import DatabaseReplayCheckpointStore, ReplayCheckpointBoundary
from services.task_execution.contracts import TaskError
from services.task_execution.replay import restore_task_state
from tests.test_replay_checkpoint_store import _Db
from tests.test_task_execution_reducer import state, plan


def runtime(callback=None):
    return ConversationTurnRuntime(conversation_id='conversation', task_id='task', turn_id='turn',
        cancellation_event=asyncio.Event(), replay_checkpoint_callback=callback)


@pytest.mark.asyncio
async def test_existing_store_routes_only_new_task_payload_to_atomic_rpc():
    s = plan(state())
    db = _Db({'outcome': 'saved'})
    store = DatabaseReplayCheckpointStore(db)
    await store.write(task_id=str(s.root_task_id), execution_token='token',
        boundary=ReplayCheckpointBoundary.BEFORE_MODEL, payload={
            'messages': [], '_task_execution': s.model_dump(mode='json'),
            '_task_execution_expected_revision': 0,
        })
    name, params = db.calls[-1]
    assert name == 'save_agent_task_checkpoint'
    assert params['p_expected_revision'] == 0
    assert params['p_state'].obj['safe_point'] == 'before_model'
    with pytest.raises(TaskError, match='EXPECTED_REVISION_REQUIRED'):
        await store.write(task_id='task', execution_token='token', boundary=ReplayCheckpointBoundary.BEFORE_MODEL,
            payload={'_task_execution': s.model_dump(mode='json')})
    assert len(db.calls) == 1


@pytest.mark.asyncio
async def test_runtime_requires_confirmed_checkpoint_before_task_state_is_committed():
    seen = []

    async def write(point, payload):
        seen.append(payload)
        return {'outcome': 'saved'}
    r = runtime(write)
    s = plan(state())
    r.task_execution_state = s
    await r.safe_point(SafePoint.BEFORE_MODEL, replay_payload={'messages': []})
    assert seen[0]['_task_execution_expected_revision'] == 0
    assert seen[0]['_task_execution'] == s.model_dump(mode='json')
    assert r.committed_task_execution_revision == s.revision


@pytest.mark.asyncio
async def test_disabled_or_missing_persistence_does_not_silently_continue_new_task():
    async def disabled(*args):
        return {'outcome': 'disabled'}
    r = runtime(disabled)
    r.task_execution_state = plan(state())
    with pytest.raises(RuntimeError, match='TASK_EXECUTION_NOT_SAVED'):
        await r.safe_point(SafePoint.BEFORE_MODEL, replay_payload={'messages': []})
    assert r.committed_task_execution_revision == 0
    r = runtime()
    r.task_execution_state = plan(state())
    with pytest.raises(RuntimeError, match='TASK_EXECUTION_CHECKPOINT_UNAVAILABLE'):
        await r.safe_point(SafePoint.BEFORE_MODEL, replay_payload={'messages': []})


@pytest.mark.asyncio
async def test_legacy_runtime_payload_has_no_new_task_fields():
    seen = []

    async def write(point, payload):
        seen.append(payload)
        return {'outcome': 'saved'}
    await runtime(write).safe_point(SafePoint.BEFORE_MODEL, replay_payload={'messages': []})
    assert seen == [{'messages': []}]


def test_replay_keeps_scope_and_rejects_workspace_switch():
    s = plan(state())
    scope = SimpleNamespace(**s.execution_identity.model_dump())
    payload = {'_task_execution': s.model_dump(mode='json')}
    assert restore_task_state(payload, conversation_id=s.conversation_id, actor_user_id=s.actor_user_id,
        org_id=s.org_id, execution_scope=scope) == s
    scope.workspace_owner_id = 'another-owner'
    with pytest.raises(TaskError, match='REPLAY_SCOPE_CHANGED'):
        restore_task_state(payload, conversation_id=s.conversation_id, actor_user_id=s.actor_user_id,
            org_id=s.org_id, execution_scope=scope)
    assert restore_task_state({'messages': []}, conversation_id=None, actor_user_id=None,
        org_id=None, execution_scope=None) is None


@pytest.mark.asyncio
async def test_lost_ack_requires_complete_task_checkpoint_match():
    from services.handlers.chat.executor import ChatGenerationExecutor
    s = plan(state())
    payload = {'_task_execution': s.model_dump(mode='json'), '_task_execution_expected_revision': 0}

    class LostAck:
        matches = True

        async def write(self, **kwargs):
            raise ConnectionError('ack lost')

        async def read_latest(self, **kwargs):
            saved = {**payload, 'safe_point': 'before_model'}
            if not self.matches:
                saved['messages'] = ['different checkpoint']
            return {'outcome': 'found', 'payload': saved}
    store = LostAck()
    executor = ChatGenerationExecutor(None, replay_checkpoint_store=store)
    claim = SimpleNamespace(task_id=str(s.root_task_id), execution_token='token')
    assert (await executor._write_replay_checkpoint(claim, SafePoint.BEFORE_MODEL, payload))['reconciled']
    store.matches = False
    with pytest.raises(RuntimeError, match='TASK_EXECUTION_SAVE_UNCONFIRMED'):
        await executor._write_replay_checkpoint(claim, SafePoint.BEFORE_MODEL, payload)
