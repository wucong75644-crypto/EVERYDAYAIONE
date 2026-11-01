"""No model controls expose native operation reservations or receipt writes."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.conversation_state import ConversationStopRequested
from services.task_execution.contracts import PlanStep, TaskError
from services.task_execution.operation_store import TaskOperationStore, TaskOperationPersistenceError
from services.task_execution.reducer import reserve_operation
from services.tools import ToolCall, ToolContext, ToolResult
from services.tools.catalog import build_capability_catalog
from services.tools.policy import ToolDecision
from tests.test_task_execution_reducer import state, plan


def sample():
    current = plan(state(), (PlanStep(id='image', title='图片', requirement_ids=('report',),
        capability='platform.generate_image'),))
    call = ToolCall('native-1', 'generate_image', {'prompt': '完整原文', 'aspect_ratio': '3:4'})
    turn = uuid4()
    candidate, operation, _ = reserve_operation(current, expected_revision=current.revision, step_id='image',
        capability='platform.generate_image', effect_key='image-slot-1', effect_version=1,
        arguments=dict(call.arguments), owner_task_id=current.root_task_id, invocation_id=call.call_id)
    operation = operation.model_copy(update={'owner_turn_id': turn, 'tool_name': call.name})
    candidate = candidate.model_copy(update={'operations': (operation,)})
    return current, candidate, operation, call, turn


def native_result(state, call, *, error=False):
    from services.agent.agent_result import AgentResult
    context = ToolContext(actor_user_id=str(state.actor_user_id),
        workspace_owner_id=state.execution_identity.workspace_owner_id, org_id=None,
        context_scope='user', personal_context_allowed=True, agent_domain='general',
        permission_mode='auto', execution_mode='interactive', conversation_id=str(state.conversation_id),
        task_id=str(state.root_task_id), call_id=call.call_id)
    decision = ToolDecision('allow', 'fixture_only', 'confirm', 'generate', False, False, ('unknown',), 'record_required')
    raw = AgentResult('已受理', status='error' if error else 'success',
        error_message='未受理' if error else '', metadata={'accepted': not error, 'completed': False,
            **({'submission_state': 'not_accepted'} if error else {})})
    return ToolResult.wrap(raw, call=call, context=context, decision=decision)


def fake_store(response):
    original, candidate, operation, call, turn = sample()
    execute = AsyncMock(return_value=SimpleNamespace(data=response))
    db = SimpleNamespace(rpc=lambda *args: SimpleNamespace(execute=execute))
    store = TaskOperationStore(db, task_id=original.root_task_id, execution_token=uuid4(), turn_id=turn,
        registry=build_capability_catalog())
    return store, original, candidate, operation, call, execute


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['ownership_lost', 'lease_expired', 'terminal'])
async def test_fence_loss_stops_without_granting_dispatch(outcome):
    store, original, _, _, call, execute = fake_store({'outcome': outcome})
    with pytest.raises(ConversationStopRequested):
        await store.begin(original, step_id='image', effect_key='image-slot-1', effect_version=1, call=call)
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_commit_is_not_a_retryable_plan_error():
    store, original, _, _, call, execute = fake_store(None)
    execute.side_effect = RuntimeError('private database credential must not leak')
    with pytest.raises(TaskOperationPersistenceError, match='^TASK_OPERATION_COMMIT_UNKNOWN$'):
        await store.begin(original, step_id='image', effect_key='image-slot-1', effect_version=1, call=call)
    assert original.operations == ()


@pytest.mark.asyncio
async def test_unconfirmed_or_wrong_scope_result_never_grants_dispatch():
    store, original, candidate, operation, call, execute = fake_store(None)
    execute.return_value.data = {'outcome': 'execute', 'state': candidate.model_copy(
        update={'original_goal': 'changed'}).model_dump(mode='json'), 'operation': operation.model_dump(mode='json')}
    with pytest.raises(TaskOperationPersistenceError, match='TASK_OPERATION_RESULT_INVALID'):
        await store.begin(original, step_id='image', effect_key='image-slot-1', effect_version=1, call=call)


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['actor_user_id', 'workspace_owner_id', 'org_id', 'conversation_id',
    'task_id', 'tool_name', 'tool_call_id', 'args'])
async def test_wrong_native_result_identity_or_arguments_rejected_before_write(field):
    store, original, candidate, operation, call, execute = fake_store(None)
    result = native_result(candidate, call)
    audit = {**result.audit, field: {'prompt': 'changed'} if field=='args' else str(uuid4())}
    result = replace(result, audit=audit)
    with pytest.raises(TaskError, match='TASK_OPERATION_RESULT_SCOPE_MISMATCH'):
        await store.complete_invocation(candidate, operation_id=operation.id, result=result,
            status='accepted', receipt_ref='task:' + str(uuid4()))
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_business_failure_is_not_success_just_because_handler_returned():
    store, original, candidate, operation, call, execute = fake_store(None)
    with pytest.raises(TaskError, match='TASK_OPERATION_SUCCESS_UNVERIFIED'):
        await store.complete_invocation(candidate, operation_id=operation.id,
            result=native_result(candidate, call, error=True), status='completed', receipt_ref='fake')
    execute.assert_not_awaited()
