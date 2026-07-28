from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.tool_confirmation.canonical import (
    CanonicalArgumentsError, canonical_arguments_hash,
)
from services.tool_confirmation.preview import build_confirmation_summary
from services.tool_confirmation.types import (
    ConfirmationBinding, ConfirmationRequest,
)


def test_handler_safety_and_preview_registries_are_consistent() -> None:
    from config.chat_tools import (
        SafetyLevel, get_safety_level, registered_safety_tools,
    )
    from services.agent.tool_executor import ToolExecutor
    from services.tool_confirmation.preview import registered_preview_tools
    from unittest.mock import MagicMock

    executor = ToolExecutor(
        db=MagicMock(), user_id="user", conversation_id="conversation",
        org_id="org",
    )
    assert set(executor._handlers) <= registered_safety_tools()
    non_safe = {
        name for name in registered_safety_tools()
        if get_safety_level(name) != SafetyLevel.SAFE
    }
    assert non_safe == set(registered_preview_tools())


def test_canonical_hash_is_order_independent_and_value_sensitive() -> None:
    assert canonical_arguments_hash({"b": [1, None], "a": True}) == canonical_arguments_hash({"a": True, "b": [1, None]})
    assert canonical_arguments_hash({"a": 1}) != canonical_arguments_hash({"a": 2})


def test_canonical_rejects_non_json_values() -> None:
    with pytest.raises(CanonicalArgumentsError):
        canonical_arguments_hash({"value": object()})


def test_restore_summary_is_fixed_and_redacted() -> None:
    summary = build_confirmation_summary("restore_file", {
        "filename": "secret.xlsx", "path": "/workspace/secret.xlsx",
        "oss_object_key": "private/key", "id": 42,
    })
    assert summary == {"description": "恢复一个文件"}
    assert len(str(summary).encode()) < 2048


def test_code_summary_contains_metadata_not_code() -> None:
    summary = build_confirmation_summary("code_execute", {
        "runtime": "python", "code": "print('secret')", "timeout": 30,
    })
    assert summary["runtime"] == "python"
    assert summary["code_characters"] == 15
    assert "secret" not in str(summary)


def test_media_summary_never_reflects_unregistered_values() -> None:
    summary = build_confirmation_summary("generate_image", {
        "model": "customer-password-file.xlsx",
        "size": "/workspace/private",
        "prompt": "secret prompt",
    })
    assert summary["model"] == "other"
    assert summary["size"] == "other"
    assert "customer" not in str(summary)
    assert "workspace" not in str(summary)


@pytest.mark.asyncio
async def test_create_response_loss_retries_same_challenge_and_waiter() -> None:
    from services.tool_confirmation.service import ToolConfirmationService

    store = AsyncMock()
    store.create = AsyncMock(side_effect=[ConnectionError(), "IDEMPOTENT:PENDING"])
    request = await ToolConfirmationService(store).create(
        task_id="task", tool_call_id="call", tool_name="web_search",
        arguments={"query": "public"}, user_id="user", org_id="org",
        safety_level="confirm",
    )
    first, second = store.create.await_args_list
    assert first.args == second.args
    assert request.confirmation_id == first.args[0]


@pytest.mark.asyncio
async def test_approved_readback_alone_never_executes_without_claim() -> None:
    from services.tool_confirmation.service import ToolConfirmationService
    store = AsyncMock()
    binding = ConfirmationBinding("task", "call", "web_search", "hash", "user", "org")
    request = ConfirmationRequest("cid", "waiter", binding, {}, "confirm")
    approved = {
        "confirmation_id": "cid", "task_id": "task", "tool_call_id": "call",
        "tool_name": "web_search", "arguments_hash": "hash",
        "user_id": "user", "org_id": "org", "state": "APPROVED",
    }
    store.read = AsyncMock(return_value=approved)
    store.claim = AsyncMock(return_value="NOT_APPROVED:EXPIRED")
    decision = await ToolConfirmationService(store).await_and_claim(request)
    assert decision.can_execute is False


@pytest.mark.asyncio
async def test_only_execution_claim_allows_execution() -> None:
    from services.tool_confirmation.service import ToolConfirmationService
    store = AsyncMock()
    binding = ConfirmationBinding("task", "call", "web_search", "hash", "user", "org")
    request = ConfirmationRequest("cid", "waiter", binding, {}, "confirm")
    base = {"confirmation_id":"cid","task_id":"task","tool_call_id":"call","tool_name":"web_search","arguments_hash":"hash","user_id":"user","org_id":"org"}
    store.read = AsyncMock(side_effect=[{**base,"state":"APPROVED"},{**base,"state":"EXECUTION_CLAIMED"}])
    store.claim = AsyncMock(return_value="WON:EXECUTION_CLAIMED")
    decision = await ToolConfirmationService(store).await_and_claim(request)
    assert decision.can_execute is True


@pytest.mark.asyncio
async def test_task_cancel_denies_before_claim() -> None:
    from services.tool_confirmation.service import ToolConfirmationService

    store = AsyncMock()
    binding = ConfirmationBinding(
        "task", "call", "web_search", "hash", "user", "org",
    )
    request = ConfirmationRequest("cid", "waiter", binding, {}, "confirm")
    store.consume = AsyncMock(return_value="WON:DENIED")
    decision = await ToolConfirmationService(store).await_and_claim(
        request, is_cancelled=lambda: True,
    )
    assert decision.can_execute is False
    assert decision.outcome.value == "cancelled"
    store.consume.assert_awaited_once()
    store.claim.assert_not_called()


@pytest.mark.asyncio
async def test_task_cancel_after_approved_readback_still_prevents_claim() -> None:
    from services.tool_confirmation.service import ToolConfirmationService

    store = AsyncMock()
    binding = ConfirmationBinding(
        "task", "call", "web_search", "hash", "user", "org",
    )
    request = ConfirmationRequest("cid", "waiter", binding, {}, "confirm")
    store.read = AsyncMock(return_value={
        "confirmation_id": "cid", "task_id": "task",
        "tool_call_id": "call", "tool_name": "web_search",
        "arguments_hash": "hash", "user_id": "user", "org_id": "org",
        "state": "APPROVED",
    })
    store.consume = AsyncMock(return_value="ALREADY_TERMINAL:APPROVED")
    cancelled = iter((False, True))

    decision = await ToolConfirmationService(store).await_and_claim(
        request, is_cancelled=lambda: next(cancelled),
    )

    assert decision.can_execute is False
    assert decision.outcome.value == "cancelled"
    store.claim.assert_not_called()


@pytest.mark.asyncio
async def test_task_cancel_after_claim_never_returns_execution_permission() -> None:
    from services.tool_confirmation.service import ToolConfirmationService

    store = AsyncMock()
    binding = ConfirmationBinding(
        "task", "call", "web_search", "hash", "user", "org",
    )
    request = ConfirmationRequest("cid", "waiter", binding, {}, "confirm")
    base = {
        "confirmation_id": "cid", "task_id": "task",
        "tool_call_id": "call", "tool_name": "web_search",
        "arguments_hash": "hash", "user_id": "user", "org_id": "org",
    }
    store.read = AsyncMock(side_effect=[
        {**base, "state": "APPROVED"},
        {**base, "state": "EXECUTION_CLAIMED"},
    ])
    store.claim = AsyncMock(return_value="WON:EXECUTION_CLAIMED")
    cancelled = iter((False, False, True))

    decision = await ToolConfirmationService(store).await_and_claim(
        request, is_cancelled=lambda: next(cancelled),
    )

    assert decision.can_execute is False
    assert decision.outcome.value == "cancelled"
