"""Native Policy/approval, operation lifecycle and actual handler-return path.

Business handler and persistence are deterministic fakes here; the same store's
real transactional behavior is covered by operations_postgres.
"""
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.agent.agent_result import AgentResult
from services.conversation_state import ConversationStopRequested
from services.task_execution.contracts import TaskState
from services.task_execution.image_binding import bind_image_call, image_prompt
from services.task_execution.image_operations import ImageOperations
from services.task_execution.methods import ImageMethodDocument
from services.task_execution.operation_store import OperationWrite, TaskOperationPersistenceError
from services.task_execution.reducer import reserve_operation, record_operation
from services.tools import ToolCall, ToolConfirmation, ToolContext, ToolDispatcher, ToolExecutionService
from services.tools.catalog import build_capability_catalog
from services.tools.result_payload import encode_result
from tests.test_task_execution_image_binding import ready
from tests.test_task_execution_methods import document


class Store:
    registry = build_capability_catalog()
    def __init__(self, state, turn):
        self.state, self.turn, self.payloads = state, turn, {}
        self.calls, self.fail = [], False

    async def begin(self, state, *, step_id, effect_key, effect_version, call):
        self.calls.append(call.call_id)
        if self.fail:
            raise TaskOperationPersistenceError('TASK_OPERATION_COMMIT_UNKNOWN')
        candidate, op, action = reserve_operation(state, expected_revision=state.revision, step_id=step_id,
            capability='platform.generate_image', effect_key=effect_key, effect_version=effect_version,
            arguments=dict(call.arguments), owner_task_id=state.root_task_id, invocation_id=call.call_id)
        if action == 'reserve':
            op = op.model_copy(update={'owner_turn_id': self.turn, 'tool_name': call.name})
            candidate = TaskState.model_validate({**candidate.model_dump(), 'operations': tuple(
                op if old.id == op.id else old for old in candidate.operations)})
            self.state = candidate
            return OperationWrite('execute', candidate, op)
        return OperationWrite('replay' if op.id in self.payloads else 'reconcile', state, op, self.payloads.get(op.id))

    async def complete_invocation(self, state, *, operation_id, result, status, receipt_ref):
        self.state = record_operation(state, operation_id, owner_task_id=state.root_task_id,
            status=status, receipt_ref=receipt_ref)
        self.payloads[operation_id] = encode_result(result)
        op = next(op for op in self.state.operations if op.id == operation_id)
        return OperationWrite('saved', self.state, op)


async def case(monkeypatch):
    state = await ready(monkeypatch)
    turn = uuid4()
    store = Store(state, turn)
    runtime = SimpleNamespace(task_execution_state=state, task_operation_store=store,
        committed_task_execution_revision=state.revision, replay_save_sequence=0)
    brief = ImageMethodDocument.model_validate_json(document()).image_plans[0]
    call = ToolCall('first', 'generate_image', {'mode': 'text_to_image', 'prompt': image_prompt(brief), 'aspect_ratio': '3:4'})
    binding = bind_image_call(state, call, store.registry)
    coordinator = ImageOperations(runtime, {'first': binding})
    context = ToolContext(actor_user_id=str(state.actor_user_id), workspace_owner_id=state.execution_identity.workspace_owner_id,
        org_id=None, context_scope='user', personal_context_allowed=True, agent_domain='general',
        permission_mode='ask', execution_mode='interactive', conversation_id=str(state.conversation_id),
        task_id=str(state.root_task_id), confirmation_available=True, feature_flags={'chat_image_async_enabled': True})
    handler = SimpleNamespace(_actor_enabled=True, _actor_invocation_store=object(),
        _actor_turn_id=str(turn), _actor_execution_token=str(uuid4()))
    io = AsyncMock(return_value=AgentResult('已受理', metadata={'accepted': True, 'completed': False,
        'task_id': str(uuid4()), 'message_id': str(uuid4()), 'submission_state': 'queued'}))
    service = ToolExecutionService(store.registry, ToolDispatcher({('legacy', 'generate_image'): io}))
    return runtime, coordinator, context, handler, io, service, call


async def execute(service, coordinator, handler, context, call, *, approved=True):
    lifecycle = coordinator.lifecycle(handler, context, 'first')
    decision = service.policy.decide(call.name, context, call.arguments)
    confirmation = (ToolConfirmation(decision.confirmation_binding, 'approved')
        if approved and decision.outcome == 'require_confirmation' else None)
    return await service.execute(call, context, confirmation=confirmation,
        before_dispatch=lifecycle.begin, on_result=lifecycle.complete)


@pytest.mark.asyncio
async def test_native_policy_precedes_reservation_and_retains_existing_image_permission_behavior(monkeypatch):
    runtime, coordinator, context, handler, io, service, call = await case(monkeypatch)
    result = await execute(service, coordinator, handler, replace(context, permission_mode='plan'), call, approved=False)
    assert result.status == 'denied'
    assert coordinator.store.calls == [] and runtime.task_execution_state.operations == ()
    io.assert_not_awaited()
    result = await execute(service, coordinator, handler, context, call)
    io.assert_awaited_once()
    assert result.metadata['accepted'] is True
    assert runtime.task_execution_state.operations[0].status == 'accepted'
    assert runtime.task_execution_state.status == 'active'
    assert runtime.task_execution_state.results[-1].step_id == 'planning'
    assert runtime.task_execution_state.artifacts[0].kind == 'text'


@pytest.mark.asyncio
async def test_new_model_call_replays_accepted_original_without_second_handler_call(monkeypatch):
    runtime, coordinator, context, handler, io, service, call = await case(monkeypatch)
    first = await execute(service, coordinator, handler, context, call)
    new = ToolCall('new-model-call', call.name, dict(call.arguments))
    replay = await coordinator.lifecycle(handler, context, 'first').replay(new, context,
        service.policy.decide(new.name, context, new.arguments))
    assert replay.metadata == first.metadata and replay.execution.replayed
    io.assert_awaited_once()
    assert len(runtime.task_execution_state.operations) == 1


@pytest.mark.asyncio
async def test_confirmed_rejection_allows_repair_but_preserves_attempt_history(monkeypatch):
    runtime, coordinator, context, handler, io, service, call = await case(monkeypatch)
    io.return_value = AgentResult('未受理', status='error', error_message='IMAGE_INPUT_UNAVAILABLE',
        metadata={'accepted': False, 'completed': False, 'submission_state': 'not_accepted'})
    await execute(service, coordinator, handler, context, call)
    original = runtime.task_execution_state.operations[0]
    assert original.status == 'failed_before_dispatch'
    repaired = ToolCall('repaired', call.name, {**dict(call.arguments), 'output_format': 'png'})
    binding = bind_image_call(runtime.task_execution_state, repaired, coordinator.store.registry)
    coordinator = ImageOperations(runtime, {'first': binding})
    io.return_value = AgentResult('已受理', metadata={'accepted': True, 'completed': False,
        'task_id': str(uuid4()), 'message_id': str(uuid4())})
    await execute(service, coordinator, handler, context, repaired)
    current = runtime.task_execution_state.operations[0]
    assert current.id == original.id and current.status == 'accepted'
    assert current.previous_attempts[0].invocation_id == 'first'
    assert io.await_count == 2


@pytest.mark.asyncio
async def test_commit_unknown_cancels_dispatch_instead_of_becoming_model_retry(monkeypatch):
    runtime, coordinator, context, handler, io, service, call = await case(monkeypatch)
    coordinator.store.fail = True
    with pytest.raises(ConversationStopRequested):
        await execute(service, coordinator, handler, context, call)
    io.assert_not_awaited()
    assert runtime.task_execution_state.operations == ()


@pytest.mark.asyncio
async def test_moving_other_unexecuted_image_steps_cannot_charge_accepted_image_again(monkeypatch):
    from tests.test_task_execution_image_binding import grouped_ready_state
    from services.task_execution.contracts import WorkPlan
    from services.task_execution.reducer import revise_plan
    runtime,coordinator,context,handler,io,service,_=await case(monkeypatch)
    runtime.task_execution_state=grouped_ready_state(runtime.task_execution_state)
    brief=ImageMethodDocument.model_validate_json(document()).image_plans[1]
    first=ToolCall('accepted-image-2','generate_image',{'mode':'text_to_image','prompt':image_prompt(brief),'aspect_ratio':'3:4'})
    binding=bind_image_call(runtime.task_execution_state,first,coordinator.store.registry)
    coordinator=ImageOperations(runtime,{'first':binding})
    accepted_result=await execute(service,coordinator,handler,context,first)
    assert accepted_result.metadata.get('accepted') is True
    io.assert_awaited_once()
    accepted=runtime.task_execution_state
    steps=tuple(step.model_copy(update={'image_item_index':3 if step.id=='image-1' else 1})
        if step.id in {'image-1','image-3'} else step for step in accepted.steps)
    runtime.task_execution_state=revise_plan(accepted,WorkPlan(expected_revision=accepted.revision,steps=steps))
    again=replace(first,call_id='new-proposal-after-reordering')
    current=bind_image_call(runtime.task_execution_state,again,coordinator.store.registry)
    assert current.effect_key==binding.effect_key
    coordinator=ImageOperations(runtime,{'first':current})
    replay=await coordinator.lifecycle(handler,context,'first').replay(again,context,service.policy.decide(
        again.name,context,again.arguments))
    assert replay.execution.replayed and len(runtime.task_execution_state.operations)==1
    io.assert_awaited_once()
