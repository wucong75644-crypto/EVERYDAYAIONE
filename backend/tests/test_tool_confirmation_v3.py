from __future__ import annotations

from unittest.mock import AsyncMock
from datetime import datetime, timedelta, timezone

import pytest

from services.tool_confirmation.canonical import (
    CanonicalArgumentsError, canonical_arguments_hash,
)
from services.tool_confirmation.preview import build_confirmation_summary
from services.tool_confirmation.types import (
    ConfirmationBinding, ConfirmationRequest,
)


def _binding() -> ConfirmationBinding:
    return ConfirmationBinding(
        "action", "interaction", 0, "task", "call", "web_search",
        "hash", "user", "org",
        datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def _record(binding: ConfirmationBinding) -> dict[str, str]:
    return {
        "confirmation_id": "cid", "action_id": binding.action_id,
        "interaction_id": binding.interaction_id,
        "interaction_version": str(binding.interaction_version),
        "task_id": "task", "tool_call_id": "call",
        "tool_name": "web_search", "arguments_hash": "hash",
        "user_id": "user", "org_id": "org",
        "authorization_expires_at": binding.expires_at.isoformat(),
    }


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
    service = ToolConfirmationService(store)
    service.set_available(True)
    request = await service.create(
        action_id="action", interaction_id="interaction",
        interaction_version=0,
        authorization_expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ),
        task_id="task", tool_call_id="call", tool_name="web_search",
        arguments={"query": "public"}, user_id="user", org_id="org",
        safety_level="confirm",
    )
    first, second = store.create.await_args_list
    assert first.args == second.args
    assert request.confirmation_id == first.args[0]


@pytest.mark.asyncio
async def test_restart_reuses_pending_identity_without_new_execution_claim() -> None:
    from services.tool_confirmation.service import ToolConfirmationService

    store = AsyncMock()
    store.create = AsyncMock(return_value="CREATE_CONFLICT")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    existing = _record(ConfirmationBinding(
        "action", "interaction", 0, "task", "call", "web_search",
        canonical_arguments_hash({"query": "public"}), "user", "org",
        expires_at,
    ))
    existing.update({
        "confirmation_id": "existing-confirmation", "state": "PENDING",
        "arguments_hash": canonical_arguments_hash({"query": "public"}),
    })
    store.find = AsyncMock(return_value=("existing-confirmation", existing))
    service = ToolConfirmationService(store)
    service.set_available(True)

    request = await service.create(
        action_id="action", interaction_id="interaction",
        interaction_version=0, authorization_expires_at=expires_at,
        task_id="task", tool_call_id="call", tool_name="web_search",
        arguments={"query": "public"}, user_id="user", org_id="org",
        safety_level="confirm",
    )

    assert request.confirmation_id == "existing-confirmation"
    assert request.waiter_token == ""
    store.find.assert_awaited_once()


@pytest.mark.asyncio
async def test_approved_readback_alone_never_executes_without_claim() -> None:
    from services.tool_confirmation.service import ToolConfirmationService
    store = AsyncMock()
    binding = _binding()
    request = ConfirmationRequest("cid", "waiter", binding, {}, "confirm")
    approved = {**_record(binding), "state": "APPROVED"}
    store.read = AsyncMock(return_value=approved)
    store.claim = AsyncMock(return_value="NOT_APPROVED:EXPIRED")
    decision = await ToolConfirmationService(store).await_and_claim(request)
    assert decision.can_execute is False


@pytest.mark.asyncio
async def test_only_execution_claim_allows_execution() -> None:
    from services.tool_confirmation.service import ToolConfirmationService
    store = AsyncMock()
    binding = _binding()
    request = ConfirmationRequest("cid", "waiter", binding, {}, "confirm")
    base = _record(binding)
    store.read = AsyncMock(side_effect=[{**base,"state":"APPROVED"},{**base,"state":"EXECUTION_CLAIMED"}])
    store.claim = AsyncMock(return_value="WON:EXECUTION_CLAIMED")
    decision = await ToolConfirmationService(store).await_and_claim(request)
    assert decision.can_execute is False
    assert decision.code == "POSTGRES_AUTHORIZATION_REQUIRED"


@pytest.mark.asyncio
async def test_task_cancel_denies_before_claim() -> None:
    from services.tool_confirmation.service import ToolConfirmationService

    store = AsyncMock()
    binding = _binding()
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
    binding = _binding()
    request = ConfirmationRequest("cid", "waiter", binding, {}, "confirm")
    store.read = AsyncMock(return_value={
        **_record(binding), "state": "APPROVED",
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
    binding = _binding()
    request = ConfirmationRequest("cid", "waiter", binding, {}, "confirm")
    base = _record(binding)
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
