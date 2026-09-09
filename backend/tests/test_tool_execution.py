"""Block 03: isolated six-layer execution, call counts and exact legacy exits."""

import asyncio
from dataclasses import FrozenInstanceError, replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.multimodal import FileReadResult
from services.agent.agent_result import AgentResult
from services.agent.tool_executor import ToolExecutor
from services.scheduler.chat_task_manager import FormBlockResult
from services.tools import (
    LegacyAdvertisement, LegacyToolHandler, ToolCall, ToolConfirmation, ToolContext,
    ToolDispatcher, ToolExecutionService, ToolPolicy, ToolRegistry, build_legacy_catalog,
    build_legacy_handlers,
)


def context(**changes):
    fields = dict(
        actor_user_id="actor", workspace_owner_id="actor", org_id="org",
        context_scope="user", personal_context_allowed=True, agent_domain="general",
        permission_mode="ask", execution_mode="interactive", conversation_id="conversation",
        call_id="call", confirmation_available=True,
        feature_flags={"file_workspace_enabled": True, "sandbox_enabled": True, "crawler_enabled": True},
    )
    return ToolContext(**(fields | changes))


def stack(name="search_knowledge", output="ok", registry=None):
    registry = registry or build_legacy_catalog()
    executor = ToolExecutor(None, "actor", "conversation", "org")
    executor._handlers = {name: AsyncMock(return_value=output)}
    executor.execute = AsyncMock(side_effect=AssertionError("recursive public execute"))
    dispatcher = ToolDispatcher(build_legacy_handlers(executor))
    return ToolExecutionService(registry, dispatcher), executor, executor._handlers[name]


def receipt(service, call, ctx, status="approved"):
    decision = service.policy.decide(call.name, replace(ctx, call_id=call.call_id), call.arguments)
    assert decision.outcome == "require_confirmation"
    return ToolConfirmation(decision.confirmation_binding, status)


REPRESENTATIVE_CASES = [
    ("search_knowledge", "ask", None, False, "allow", 1),
    ("search_knowledge", "ask", None, True, "deny", 0),
    ("file_search", "ask", None, False, "allow", 1),
    ("file_search", "ask", None, True, "deny", 0),
    ("file_delete", "ask", None, False, "require_confirmation", 0),
    ("file_delete", "ask", "approved", False, "allow", 1),
    ("file_delete", "ask", "rejected", False, "deny", 0),
    ("file_delete", "auto", None, False, "require_confirmation", 0),
    ("file_delete", "auto", "approved", False, "allow", 1),
    ("file_delete", "plan", None, False, "deny", 0),
    ("file_delete", "ask", None, True, "deny", 0),
]


@pytest.mark.parametrize("name,mode,confirmation,denied_scope,outcome,count", REPRESENTATIVE_CASES)
async def test_representatives_complete_chain_real_internal_handlers(
    name, mode, confirmation, denied_scope, outcome, count,
):
    """Real registry/policy/dispatcher/adapter AND internal methods; only IO is mocked."""
    registry = build_legacy_catalog()
    ctx = context(permission_mode=mode, authorized_tool_names=set() if denied_scope else None)
    args = {"query": "经验"} if name == "search_knowledge" else (
        {"file_ids": ["fixture-id"], "files": ["fixture.txt"]} if name == "file_delete" else {"keyword": "fixture"}
    )
    call = ToolCall("call", name, args)
    executor = ToolExecutor(None, "actor", "conversation", "org")
    # Retain the actual bound method / file dispatch closure for this tool.
    internal = AsyncMock(wraps=executor._handlers[name])
    executor._handlers = {name: internal}
    executor.execute = AsyncMock(side_effect=AssertionError("recursive execute"))
    io = AsyncMock(return_value=(
        [{"title": "fixture", "content": "experience"}] if name == "search_knowledge"
        else FileReadResult(type="image", text="fixture image", image_url="https://example.test/image.png")
        if name == "file_search" else AgentResult("mock deleted", source="file_delete")
    ))
    executor._file_search = io
    executor._file_delete = io
    settings = MagicMock(file_workspace_enabled=True, file_workspace_root="/unavailable-test-workspace")
    service = ToolExecutionService(registry, ToolDispatcher(build_legacy_handlers(executor)))
    approved = receipt(service, call, ctx, confirmation) if confirmation else None
    with patch("services.knowledge_service.search_relevant", io), \
         patch("core.config.get_settings", return_value=settings), \
         patch("services.file_executor.FileExecutor") as files:
        resolution = registry.resolve(ctx, policy=service.policy, advertisement=LegacyAdvertisement([name]))
        result = await service.execute(call, ctx, confirmation=approved)
    assert result.decision.outcome == outcome
    assert (name in resolution.allowed) == (not denied_scope and mode != "plan")
    assert internal.await_count == io.await_count == count
    assert result.execution.handler_started is (count == 1)
    assert result.execution.attempts == count
    assert result.execution.status == ("succeeded" if count else "not_started")
    executor.execute.assert_not_awaited()
    assert files.call_count == (count if name != "search_knowledge" else 0)
    if count:
        internal.assert_awaited_once_with(args)
        if name == "search_knowledge":
            io.assert_awaited_once_with(query="经验", limit=5, org_id="org")
            assert result.to_legacy().summary == "- fixture: experience"
        else:
            assert result.to_legacy() is io.return_value
            assert io.await_args.args[1] == args
    else:
        with pytest.raises(PermissionError):
            result.to_legacy()


@pytest.mark.parametrize("kind", ["explicit", "legacy"])
@pytest.mark.parametrize("risk", ["safe", "dangerous"])
async def test_new_and_legacy_specs_use_same_policy_and_handler_key(kind, risk):
    original = build_legacy_catalog().require("search_knowledge")
    schema = original.to_schema()
    schema["function"]["name"] = "new_tool"
    spec = replace(original, name="new_tool", schema=schema, handler_key="internal_key",
                   definition_kind=kind, risk_level=risk)
    registry = ToolRegistry([spec])
    service, executor, handler = stack("internal_key", registry=registry)
    call, ctx = ToolCall("call", "new_tool", {"query": "x"}), context()
    result = await service.execute(call, ctx)
    if risk == "dangerous":
        assert result.status == "confirmation_required"
        handler.assert_not_awaited()
        result = await service.execute(call, ctx, confirmation=receipt(service, call, ctx))
    assert result.to_legacy() == "ok"
    handler.assert_awaited_once_with({"query": "x"})
    executor.execute.assert_not_awaited()
    denied, _, blocked = stack("internal_key", registry=registry)
    result = await denied.execute(call, replace(ctx, authorized_tool_names=set()))
    assert result.status == "denied"
    blocked.assert_not_awaited()


@pytest.mark.parametrize("name,missing", [("missing_tool", False), ("search_knowledge", True)])
async def test_unknown_tool_or_missing_handler_preserves_value_error(name, missing):
    service, executor, handler = stack()
    if missing:
        service.dispatcher = ToolDispatcher({})
    result = await service.execute(ToolCall("call", name, {}), context())
    with pytest.raises(ValueError, match=f"^Unknown sync tool: {name}$") as new:
        result.to_legacy()
    old = ToolExecutor(None, "actor", "conversation", "org")
    old._handlers.pop(name, None)
    with pytest.raises(ValueError) as legacy:
        await old.execute(name, {})
    assert type(new.value) is type(legacy.value)
    assert str(new.value) == str(legacy.value)
    assert result.execution.status == "not_started"
    handler.assert_not_awaited()


async def test_dispatcher_only_consumes_own_allow_permit_once():
    service, _, handler = stack()
    call, ctx = ToolCall("call", "search_knowledge", {}), context()
    decision = service.policy.decide(call.name, ctx, call.arguments)
    for invalid in [None, call, decision, {"outcome": "allow"}]:
        with pytest.raises(PermissionError):
            await service.dispatcher.dispatch(invalid)
    for outcome in ["deny", "require_confirmation"]:
        with pytest.raises(PermissionError):
            service.dispatcher._approve(call, service.registry.require(call.name), replace(decision, outcome=outcome))
    permit = service.dispatcher._approve(call, service.registry.require(call.name), decision)
    with pytest.raises(FrozenInstanceError):
        permit.call = ToolCall("other", "file_delete", {})
    with pytest.raises(PermissionError):
        await ToolDispatcher({}).dispatch(permit)
    handler.assert_not_awaited()
    assert await service.dispatcher.dispatch(permit) == "ok"
    with pytest.raises(PermissionError):
        await service.dispatcher.dispatch(permit)
    handler.assert_awaited_once()
    with pytest.raises(ValueError, match="Unknown sync tool"):
        LegacyToolHandler(ToolExecutor(None, "actor", "conversation"), "missing")


async def test_pending_approval_concurrent_resubmission_runs_once_and_freezes_arguments():
    service, _, handler = stack("file_delete")
    args = {"files": ["fixture.txt"]}
    call, ctx = ToolCall("call", "file_delete", args), context()
    pending = await service.execute(call, ctx)
    assert pending.status == "confirmation_required"
    handler.assert_not_awaited()
    approval = receipt(service, call, ctx)
    args["files"].append("unapproved.txt")
    first, second = await asyncio.gather(
        service.execute(call, ctx, confirmation=approval),
        service.execute(call, ctx, confirmation=approval),
    )
    assert first.to_legacy() == "ok"
    with pytest.raises(PermissionError, match="already dispatched"):
        second.to_legacy()
    handler.assert_awaited_once_with({"files": ["fixture.txt"]})


@pytest.mark.parametrize("change", ["args", "call", "scope", "mode", "permission", "confirmation_unavailable"])
async def test_approval_rechecks_current_call_context_before_any_dispatch(change):
    service, _, handler = stack("file_delete")
    call, ctx = ToolCall("call", "file_delete", {"file_ids": ["fixture"]}), context()
    approval = receipt(service, call, ctx)
    if change == "args":
        call = ToolCall("call", "file_delete", {"file_ids": ["different"]})
    elif change == "call":
        call = replace(call, call_id="different")
    elif change == "scope":
        ctx = replace(ctx, conversation_id="different")
    elif change == "mode":
        ctx = replace(ctx, permission_mode="plan")
    elif change == "permission":
        ctx = replace(ctx, authorized_tool_names=set())
    else:
        ctx = replace(ctx, confirmation_available=False)
    result = await service.execute(call, ctx, confirmation=approval)
    assert result.status == "denied"
    assert result.execution.status == "not_started"
    handler.assert_not_awaited()


@pytest.mark.parametrize("error", [ValueError("bad value"), PermissionError("scope"), RuntimeError("provider"), TimeoutError("deadline")])
async def test_raised_errors_preserve_identity_and_never_retry(error):
    service, _, handler = stack("web_search")  # legacy effects are explicitly unknown
    handler.side_effect = error
    call = ToolCall("call", "web_search", {"query": "x"})
    result = await service.execute(call, context())
    assert result.exception is error
    assert result.execution.status == "uncertain"
    assert result.is_failure and not result.error.safe_to_retry
    with pytest.raises(type(error)) as raised:
        result.to_legacy()
    assert raised.value is error
    duplicate = await service.execute(call, context())
    assert duplicate.execution.status == "not_started"
    handler.assert_awaited_once()
    compatible, _, second_handler = stack("web_search")
    second_handler.side_effect = error
    with pytest.raises(type(error)) as raised:
        await compatible.execute_legacy(call, context())
    assert raised.value is error
    second_handler.assert_awaited_once()


async def test_real_internal_handler_returned_error_vs_propagated_exception():
    executor = ToolExecutor(None, "actor", "conversation", "org")
    registry = build_legacy_catalog()
    error = PermissionError("fixture denied")
    executor._file_search = AsyncMock(side_effect=error)
    service = ToolExecutionService(registry, ToolDispatcher(build_legacy_handlers(executor)))
    with patch("core.config.get_settings", return_value=MagicMock(file_workspace_enabled=True)), \
         patch("services.file_executor.FileExecutor"), \
         patch("services.knowledge_service.search_relevant", AsyncMock(side_effect=error)):
        returned = await service.execute(ToolCall("file", "file_search", {}), context())
        raised = await service.execute(ToolCall("knowledge", "search_knowledge", {"query": "x"}), context())
    assert returned.to_legacy().status == "error"
    assert returned.error.message == "PermissionError: fixture denied"
    assert returned.execution.status == "succeeded"
    assert returned.error.retryable is False
    assert raised.exception is error
    assert raised.execution.status == "failed"  # declared effects=(none,)


@pytest.mark.parametrize("when", ["before", "handler", "task"])
async def test_cancellation_propagates_without_retry(when):
    service, _, handler = stack()
    event, entered = asyncio.Event(), asyncio.Event()
    ctx, call = context(cancellation=event), ToolCall("call", "search_knowledge", {})
    cancelled = asyncio.CancelledError("fixture cancelled")
    if when == "before":
        event.set()
    elif when == "handler":
        handler.side_effect = cancelled
    else:
        async def wait(_):
            entered.set()
            await asyncio.Future()
        handler.side_effect = wait
    task = asyncio.create_task(service.execute_legacy(call, ctx))
    if when == "task":
        await entered.wait()
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert handler.await_count == (0 if when == "before" else 1)
    if when != "before":
        duplicate = await service.execute(call, ctx)
        assert duplicate.execution.status == "not_started"
        assert handler.await_count == 1


async def test_policy_exception_and_cancellation_during_decision_fail_closed():
    service, _, handler = stack()
    call, ctx = ToolCall("call", "search_knowledge", {}), context()
    with patch.object(service.policy, "decide", side_effect=RuntimeError("policy unavailable")):
        with pytest.raises(RuntimeError, match="policy unavailable"):
            await service.execute(call, ctx)
    event = asyncio.Event()
    decide = service.policy.decide
    def cancel(*args, **kwargs):
        result = decide(*args, **kwargs)
        event.set()
        return result
    with patch.object(service.policy, "decide", side_effect=cancel):
        with pytest.raises(asyncio.CancelledError):
            await service.execute(call, replace(ctx, cancellation=event))
    handler.assert_not_awaited()


@pytest.mark.parametrize("raw", [AgentResult("fixture"), FileReadResult(text="fixture"),
                                 FormBlockResult({"form_id": "fixture"}, "hint"), "fixture"])
async def test_entrypoint_and_legacy_exit_return_all_supported_types(raw):
    call = ToolCall("call", "search_knowledge", {"query": "fixture"})
    service, _, handler = stack(output=raw)
    result = await service.execute(call, context())
    assert result.to_legacy() is raw
    handler.assert_awaited_once()
    service, _, handler = stack(output=raw)
    assert await service.execute_legacy(call, context()) is raw
    handler.assert_awaited_once()


async def test_unknown_executor_type_mapping_cannot_fall_back_to_same_key():
    handler = AsyncMock(return_value="unexpected")
    service = ToolExecutionService(build_legacy_catalog(), ToolDispatcher({("other", "search_knowledge"): handler}))
    result = await service.execute(ToolCall("call", "search_knowledge", {}), context())
    with pytest.raises(ValueError, match="Unknown sync tool: search_knowledge"):
        result.to_legacy()
    handler.assert_not_awaited()


@pytest.mark.parametrize("mode", ["scheduled", "preflight"])
async def test_noninteractive_dangerous_names_are_not_execution_grants(mode):
    service, _, handler = stack("file_delete")
    result = await service.execute(ToolCall("call", "file_delete", {"file_ids": ["fixture"]}), context(
        execution_mode=mode, task_id="task", authorized_tool_names={"file_delete"},
        authorization_snapshot={"allowed_tools": ["file_delete"]},
    ))
    assert result.status == "denied" and result.execution.status == "not_started"
    handler.assert_not_awaited()
