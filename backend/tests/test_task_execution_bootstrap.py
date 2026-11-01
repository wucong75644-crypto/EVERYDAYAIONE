"""Requirements originate in a validated user anchor, never a tool result."""
import copy
import asyncio
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from schemas.message import TextPart, ImagePart
from services.handlers.context_snapshot import ContextAnchor
from services.task_execution.bootstrap import TaskBootstrap, START_TASK, StartTaskInput
from services.task_execution.contracts import TaskError
from tests.test_task_execution_model_bridge import call, execute, setup_case
from tests.test_task_execution_reducer import state


def bootstrap():
    s = state()
    anchor = ContextAnchor(task_id=str(s.root_task_id), conversation_id=str(s.conversation_id),
        turn_id='turn', input_message_id=str(s.source_message_ids[0]), base_revision=0,
        through_message_id=None, org_id=None)
    scope = SimpleNamespace(actor_user_id=str(s.actor_user_id), **s.execution_identity.model_dump())
    value = TaskBootstrap.from_input(anchor=anchor, scope=scope,
        content=[TextPart(text='先完整规划提示词，再生成五张详情图'), ImagePart(url='https://example.invalid/item.png')],
        task_id=s.root_task_id, conversation_id=s.conversation_id, org_id=None)
    return value, s, anchor, scope


def proposal():
    return {'requirements': [
        {'id': 'prompts', 'description': '完整逐图提示词规划', 'artifact_kind': 'text'},
        {'id': 'images', 'description': '五张详情图', 'artifact_kind': 'image', 'minimum': 5}]}


def test_initializer_preserves_actual_goal_source_scope_and_quantity():
    source, s, *_ = bootstrap()
    initialized = source.initialize(proposal(), model_rounds_used=2)
    assert initialized.original_goal == source.original_goal
    assert initialized.actor_user_id == s.actor_user_id
    assert initialized.source_message_ids == s.source_message_ids
    assert initialized.requirements[-1].minimum == 5
    assert all(r.source_message_id == s.source_message_ids[0] for r in initialized.requirements)
    assert initialized.model_rounds_used == 2
    assert initialized.steps == ()
    assert source.checkpoint()['version'] == 1


@pytest.mark.parametrize('field', ['actor_user_id', 'source_message_id', 'validator', 'capability_gates'])
def test_model_cannot_supply_trusted_fields(field):
    value = proposal()
    value['requirements'][0][field] = str(uuid4())
    with pytest.raises(ValidationError):
        StartTaskInput.model_validate(value)


def test_invalid_anchor_cannot_bootstrap_a_different_task():
    source, s, anchor, scope = bootstrap()
    with pytest.raises(TaskError, match='INPUT_MISMATCH'):
        TaskBootstrap.from_input(anchor=anchor, scope=scope, content=[TextPart(text='request')],
            task_id=uuid4(), conversation_id=s.conversation_id, org_id=None)


def test_duplicate_requirement_ids_rejected_and_model_cannot_drop_host_requirements():
    from dataclasses import replace
    from services.task_execution.contracts import Requirement, CapabilityGate
    source, s, *_ = bootstrap()
    required = Requirement(id='method.prompts', source_message_id=source.input_message_id,
        description='方法规定的完整提示词', artifact_kind='text', validator='method.prompts.valid')
    source = replace(source, required_deliveries=(required,), capability_gates=(
        CapabilityGate(capability='image.generate', requirement_ids=(required.id,)),))
    initialized = source.initialize(proposal(), model_rounds_used=1)
    assert required in initialized.requirements
    assert initialized.capability_gates == source.capability_gates
    duplicate = proposal()
    duplicate['requirements'].append(duplicate['requirements'][0])
    with pytest.raises(TaskError, match='DUPLICATE_REQUIREMENT'):
        source.initialize(duplicate, model_rounds_used=1)


@pytest.mark.asyncio
async def test_real_dispatch_initializer_is_atomic_and_blocks_same_batch_business():
    source, s, *_ = bootstrap()
    snapshots = []

    async def checkpoint(point, payload):
        snapshots.append(copy.deepcopy(payload))
        return {'outcome': 'saved'}

    runtime, prepared, handler, sink = setup_case(checkpoint, s)
    runtime.task_execution_state = None
    runtime.task_bootstrap = source
    prepared.budget = SimpleNamespace(turns_used=1)
    await execute(runtime, prepared, handler, sink,
        [call(START_TASK, proposal(), 'start'), call('generate_image', {}, 'business')], [])
    assert runtime.task_execution_state.requirements[-1].minimum == 5
    assert snapshots[-1]['_task_execution']['original_goal'] == source.original_goal
    assert [m['tool_call_id'] for m in snapshots[-1]['messages'] if m['role'] == 'tool'] == ['start', 'business']
    handler._execute_tool_calls.assert_not_called()
    original = runtime.task_execution_state
    await execute(runtime, prepared, handler, sink, [call(START_TASK, proposal(), 'again')], [])
    assert runtime.task_execution_state == original


@pytest.mark.asyncio
async def test_failed_initial_save_restores_uninitialized_state_and_transcript():
    source, s, *_ = bootstrap()
    runtime, prepared, handler, sink = setup_case(AsyncMock(return_value={'outcome': 'disabled'}), s)
    runtime.task_execution_state = None
    runtime.task_bootstrap = source
    prepared.budget = SimpleNamespace(turns_used=1)
    messages = copy.deepcopy(prepared.messages)
    with pytest.raises(RuntimeError, match='NOT_SAVED'):
        await execute(runtime, prepared, handler, sink, [call(START_TASK, proposal())], [])
    assert runtime.task_execution_state is None and prepared.messages == messages
    sink.on_block_update.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['new_enabled', 'new_model_directed', 'legacy_enabled', 'resume_disabled', 'tampered'])
async def test_actual_executor_admission_and_frozen_bootstrap_recovery(monkeypatch, mode):
    from core.config import get_settings
    from services.conversation_execution import GenerationClaim
    from services.handlers.chat.execution_engine import ChatExecutionResult
    from services.handlers.chat.executor import ChatGenerationExecutor
    from tests.test_chat_generation_executor import _DB, _ReplayCheckpointStore
    source, s, anchor, scope = bootstrap()
    actor, conversation, task_id = str(s.actor_user_id), str(s.conversation_id), str(s.root_task_id)
    message_id = str(s.source_message_ids[0])
    db = _DB({'id': message_id, 'conversation_id': conversation, 'turn_id': 'turn', 'role': 'user',
        'content': [{'type': 'text', 'text': source.original_goal}]}, conversation={
        'id': conversation, 'org_id': None, 'user_id': actor, 'source': 'web', 'scope_type': 'user'})
    def followup_rpc(name, parameters):
        assert name == 'read_agent_task_followup'
        assert parameters == {'p_task_id': task_id, 'p_execution_token': 'token'}
        return SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(data={'outcome': 'not_found'})))
    db.rpc = followup_rpc
    store = _ReplayCheckpointStore()
    store.write = AsyncMock(return_value={'outcome': 'saved'})
    if mode not in {'new_enabled','new_model_directed'}:
        payload = {'messages': [], 'content_blocks': [], 'turn_index': 0, 'tool_call_ids': []}
        if mode in {'resume_disabled', 'tampered'}:
            payload['_task_bootstrap'] = source.checkpoint()
        if mode == 'tampered':
            payload['_task_bootstrap']['input']['original_goal'] = '替换原始用户目标'
        store.read_result = {'outcome': 'found', 'payload': payload}
    monkeypatch.setattr(get_settings(), 'agent_task_execution_enabled', mode != 'resume_disabled')
    monkeypatch.setattr(get_settings(), 'agent_task_model_directed_enabled', mode in {'new_model_directed','resume_disabled'})
    captured = []

    async def execute_chat(**kwargs):
        captured.append(kwargs['runtime'].task_bootstrap)
        return ChatExecutionResult(parts=[TextPart(text='结果')], content_blocks=[],
            usage={'prompt_tokens': 0, 'completion_tokens': 0}, credits_cost=0, tool_digest=None)

    monkeypatch.setattr('services.handlers.chat.executor.execute_chat', execute_chat)
    executor = ChatGenerationExecutor(db, handler_factory=lambda _: SimpleNamespace(org_id=None),
        handler_db_factory=lambda: object(), replay_checkpoint_store=store)
    claim = GenerationClaim(task_id=task_id, execution_token='token', conversation_id=conversation,
        turn_id='turn', input_message_id=message_id, base_context_revision=0,
        context_through_message_id=None, execution_attempt=1, execution_mode='serial')
    task = {'id': task_id, 'conversation_id': conversation, 'assistant_message_id': str(uuid4()),
        'user_id': actor, 'org_id': None, 'model_id': 'auto', 'request_params': {}}
    if mode == 'tampered':
        with pytest.raises(RuntimeError, match='BOOTSTRAP_REPLAY_MISMATCH'):
            await executor.execute(task, claim, asyncio.Event())
        assert captured == []
    else:
        await executor.execute(task, claim, asyncio.Event())
        assert (captured[0] is not None) == (mode != 'legacy_enabled')
        if captured[0] is not None:
            from dataclasses import replace
            expected=replace(source,require_coverage_review=mode=='new_enabled',model_directed=mode=='new_model_directed').checkpoint()
            assert captured[0].checkpoint() == expected
            assert store.write.call_args.kwargs['payload']['_task_bootstrap'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('matches', [True, False])
async def test_bootstrap_only_lost_ack_uses_exact_saved_payload(matches):
    from services.conversation_commands import SafePoint
    from services.handlers.chat.executor import ChatGenerationExecutor
    from tests.test_chat_generation_executor import _claim
    source, *_ = bootstrap()
    payload = {'_task_bootstrap': source.checkpoint(), 'messages': []}
    saved = {**copy.deepcopy(payload), 'safe_point': 'before_model'}
    if not matches:
        saved['messages'] = [{'role': 'system', 'content': 'different'}]
    store = SimpleNamespace(write=AsyncMock(side_effect=RuntimeError('lost acknowledgement')),
        read_latest=AsyncMock(return_value={'outcome': 'found', 'payload': saved}))
    executor = ChatGenerationExecutor(None, replay_checkpoint_store=store)
    action = executor._write_replay_checkpoint(_claim(), SafePoint.BEFORE_MODEL, payload)
    if matches:
        assert (await action)['reconciled']
    else:
        with pytest.raises(RuntimeError, match='SAVE_UNCONFIRMED'):
            await action


@pytest.mark.asyncio
async def test_initialization_uses_replayed_model_round_not_reset_attempt_budget():
    from services.handlers.chat.execution_engine import _execute_tools
    source, s, *_ = bootstrap()
    runtime, prepared, handler, sink = setup_case(initial=s)
    runtime.task_execution_state = None
    runtime.task_bootstrap = source
    prepared.budget = SimpleNamespace(turns_used=1)
    await _execute_tools(handler=handler, request=SimpleNamespace(), prepared=prepared,
        turn=0, turn_text='', calls=[call(START_TASK, proposal(), 'start')],
        previewed_call_ids=set(), cancellation_event=runtime.cancellation_event,
        sink=sink, blocks=[], runtime=runtime, next_model_round=8)
    assert runtime.task_execution_state.model_rounds_used == 8


@pytest.mark.parametrize('change', [None, 'channel', 'foreign_workspace', 'no_personal', 'channel_id'])
def test_new_scope_admission_does_not_enroll_unsupported_identity(change):
    from services.task_execution.bootstrap import admits_new_task
    _, _, _, scope = bootstrap()
    if change == 'channel':scope.context_scope = 'channel'
    if change == 'foreign_workspace':scope.workspace_owner_id = 'channels/wecom/group'
    if change == 'no_personal':scope.personal_context_allowed = False
    if change == 'channel_id':scope.channel_scope_id = 'group'
    assert admits_new_task(scope) is (change is None)


@pytest.mark.asyncio
async def test_saved_channel_bootstrap_still_recovers_with_new_admission_disabled(monkeypatch):
    from dataclasses import replace
    from core.config import get_settings
    from services.conversation_execution import GenerationClaim
    from services.handlers.chat.execution_engine import ChatExecutionResult
    from services.handlers.chat.execution_scope import resolve_execution_scope
    from services.handlers.chat.executor import ChatGenerationExecutor
    from tests.test_chat_generation_executor import _DB, _ReplayCheckpointStore
    source, s, *_ = bootstrap()
    actor, conversation, task_id = str(s.actor_user_id), str(s.conversation_id), str(s.root_task_id)
    message_id = str(s.source_message_ids[0])
    db = _DB({'id': message_id, 'conversation_id': conversation, 'turn_id': 'turn', 'role': 'user',
        'content': [{'type': 'text', 'text': source.original_goal}]}, conversation={
        'id': conversation, 'org_id': None, 'user_id': None, 'source': 'wecom',
        'scope_type': 'channel', 'scope_id': 'group'})
    task = {'id': task_id, 'conversation_id': conversation, 'assistant_message_id': str(uuid4()),
        'user_id': actor, 'org_id': None, 'model_id': 'auto', 'request_params': {},
        'delivery_context': {'channel': 'wecom', 'chattype': 'group', 'chatid': 'group', 'corp_id': 'corp'}}
    scope = await resolve_execution_scope(db, task, conversation)
    from services.task_execution.contracts import ExecutionIdentity
    identity = ExecutionIdentity(context_scope=scope.context_scope, workspace_owner_id=scope.workspace_owner_id,
        personal_context_allowed=scope.personal_context_allowed, channel_scope_id=scope.channel_scope_id)
    source = replace(source, execution_identity=identity)
    store = _ReplayCheckpointStore()
    payload = {'messages': [{'role': 'system', 'content': '原群上下文，不得替换为个人记忆'}],
        'content_blocks': [{'type': 'text', 'text': '已有群成果'}], 'turn_index': 0, 'tool_call_ids': [],
        '_task_bootstrap': source.checkpoint()}
    store.read_result = {'outcome': 'found', 'payload': copy.deepcopy(payload)}
    store.write = AsyncMock(return_value={'outcome': 'saved'})
    monkeypatch.setattr(get_settings(), 'agent_task_execution_enabled', False)
    captured = []

    async def execute_chat(**kwargs):
        runtime = kwargs['runtime']
        captured.append(runtime.task_bootstrap.checkpoint())
        assert runtime.checkpoint_base['messages'] == payload['messages']
        assert runtime.checkpoint_base['content_blocks'] == payload['content_blocks']
        assert kwargs['request'].execution_scope == scope
        return ChatExecutionResult(parts=[TextPart(text='原恢复路径')], content_blocks=[],
            usage={}, credits_cost=0, tool_digest=None)

    monkeypatch.setattr('services.handlers.chat.executor.execute_chat', execute_chat)
    executor = ChatGenerationExecutor(db, handler_factory=lambda _: SimpleNamespace(org_id=None),
        handler_db_factory=lambda: object(), replay_checkpoint_store=store)
    claim = GenerationClaim(task_id=task_id, execution_token='token', conversation_id=conversation,
        turn_id='turn', input_message_id=message_id, base_context_revision=0,
        context_through_message_id=None, execution_attempt=1, execution_mode='serial')
    await executor.execute(task, claim, asyncio.Event())
    assert captured == [source.checkpoint()]
    assert store.write.call_args.kwargs['payload']['_task_bootstrap'] == source.checkpoint()
