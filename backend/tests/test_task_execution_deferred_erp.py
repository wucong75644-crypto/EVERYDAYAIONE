"""Release only non-ERP orchestration; keep original ERP and old effects safe."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.config import Settings, get_settings
from services.task_execution.contracts import TaskError
from services.task_execution.dispatch import plan_dispatch_blockers, blocker_message
from services.task_execution.native_results import lifecycle_for
from services.task_execution.native_writes import NativeWriteLifecycle, prepare_erp
from services.tools import ToolCall
from services.tools.result import UncertainToolInvocationError
from tests.test_task_execution_native_writes import case, execute, controller


@pytest.fixture(autouse=True)
def deferred(monkeypatch):
    monkeypatch.setattr(get_settings(), 'agent_task_erp_write_enabled', False)


def test_non_erp_release_default_and_tool_exposure_keep_original_agent():
    assert Settings.model_fields['agent_task_erp_write_enabled'].default is False
    runtime, _, _, _, service, *_ = case()
    blockers = plan_dispatch_blockers(runtime.task_execution_state, service.registry)
    assert blockers['erp_execute'] == 'TASK_ERP_WRITE_ADAPTER_DEFERRED'
    assert 'erp_agent' not in blockers
    assert '原ERP Agent' in blocker_message(blockers['erp_execute'])
    assert plan_dispatch_blockers(None, service.registry) == {}


@pytest.mark.asyncio
async def test_forged_new_erp_call_is_blocked_before_reserving_or_remote_io():
    runtime, handler, ctx, call, service, io, store, client = case()
    before = runtime.task_execution_state.model_dump()
    with pytest.raises(TaskError, match='ERP_WRITE_ADAPTER_DEFERRED'):
        await execute(runtime, handler, ctx, call, service)
    assert runtime.task_execution_state.model_dump() == before
    io.assert_not_awaited(); client.request_once.assert_not_awaited()
    assert isinstance(lifecycle_for(handler, ctx, service.registry, call.name), NativeWriteLifecycle)


@pytest.mark.asyncio
async def test_direct_native_preparation_is_blocked_but_original_unmanaged_path_remains():
    runtime, *_ = case()
    db = Mock()
    managed = SimpleNamespace(_task_execution_state=runtime.task_execution_state, db=db)
    with pytest.raises(TaskError, match='ERP_WRITE_ADAPTER_DEFERRED'):
        await prepare_erp(managed, {})
    db.rpc.assert_not_called()
    assert await prepare_erp(SimpleNamespace(db=db), {}) is None
    legacy = SimpleNamespace(_task_execution_state=runtime.task_execution_state.model_copy(update={'image_binding_version': 1}), db=db)
    assert await prepare_erp(legacy, {}) is None
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_flag_closed_between_replay_and_begin_never_reserves_new_effect(monkeypatch):
    runtime, handler, ctx, call, service, io, _, client = case()
    lifecycle = lifecycle_for(handler, ctx, service.registry, call.name)
    decision = service.policy.decide(call.name, ctx, call.arguments)
    monkeypatch.setattr(get_settings(), 'agent_task_erp_write_enabled', True)
    assert await lifecycle.replay(call, ctx, decision) is None
    monkeypatch.setattr(get_settings(), 'agent_task_erp_write_enabled', False)
    with pytest.raises(TaskError, match='ERP_WRITE_ADAPTER_DEFERRED'):
        await lifecycle.begin(call, ctx, replace(decision, outcome='allow'))
    assert not runtime.task_execution_state.operations
    io.assert_not_awaited(); client.request_once.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['ok', 'format_loss', 'timeout', 'missing'])
async def test_closed_domain_retains_existing_effect_recovery_without_resending(monkeypatch, mode):
    monkeypatch.setattr(get_settings(), 'agent_task_erp_write_enabled', True)
    runtime, handler, ctx, call, service, io, _, client = case(mode=mode)
    await execute(runtime, handler, ctx, call, service)
    before = runtime.task_execution_state.model_dump()
    monkeypatch.setattr(get_settings(), 'agent_task_erp_write_enabled', False)
    if mode == 'timeout':
        with pytest.raises(UncertainToolInvocationError):
            await execute(runtime, handler, ctx, ToolCall('different', call.name, dict(call.arguments)), service)
    elif mode == 'missing':
        assert (await execute(runtime, handler, ctx, call, service)).execution.replayed
        with pytest.raises(TaskError, match='ERP_WRITE_ADAPTER_DEFERRED'):
            await execute(runtime, handler, ctx, ToolCall('different', call.name, dict(call.arguments)), service)
    else:
        if mode == 'ok':
            assert (await execute(runtime, handler, ctx, ToolCall('different', call.name, dict(call.arguments)), service)).execution.replayed
        ctrl = controller(runtime, ctx, runtime.task_operation_store)
        op = ctrl.state.operations[-1]
        read = await ctrl.control('read_task_write_result', {'expected_revision': ctrl.state.revision, 'operation_id': op.id}, call_id='recover')
        assert read.result['ok'] and read.result['operation_status'] == 'completed'
    assert runtime.task_execution_state.model_dump() == before
    io.assert_awaited_once()
    assert client.request_once.await_count == (0 if mode == 'missing' else 1)
    client.request_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_erp_plan_is_rejected_without_losing_other_plan_or_results():
    runtime, _, ctx, _, _, _, store, _ = case()
    ctrl = controller(runtime, ctx, store)
    before = ctrl.state.model_dump()
    reply = await ctrl.control('update_task_plan', {'expected_revision': ctrl.state.revision, 'steps': [
        {'id': 'write', 'title': '新ERP写步骤', 'capability': 'platform.erp_execute', 'requirement_ids': ['report']}]}, call_id='plan')
    assert reply.result['code'] == 'TASK_ERP_WRITE_ADAPTER_DEFERRED' and not reply.result['ok']
    assert ctrl.state.model_dump() == before
    ctrl.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_historical_erp_plan_can_still_be_retained_when_domain_closed(monkeypatch):
    from services.task_execution.contracts import WorkPlan
    from services.task_execution.dispatch import validate_plan_capabilities
    monkeypatch.setattr(get_settings(), 'agent_task_erp_write_enabled', True)
    runtime, handler, ctx, call, service, *_ = case()
    await execute(runtime, handler, ctx, call, service)
    monkeypatch.setattr(get_settings(), 'agent_task_erp_write_enabled', False)
    state = runtime.task_execution_state
    proposal = WorkPlan(expected_revision=state.revision, steps=state.steps)
    validate_plan_capabilities(proposal, service.registry, state=state)
    assert 'erp_execute' not in plan_dispatch_blockers(state, service.registry)
