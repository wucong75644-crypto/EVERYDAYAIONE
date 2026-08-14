"""Chat tools must use the Runtime Action boundary exclusively."""

from __future__ import annotations

import pytest

from services.agent.runtime.application.chat_action_bridge import (
    ChatActionRequest,
    FailClosedRuntimeChatActionExecutor,
    RuntimeChatActionLoopAdapter,
    RuntimeChatActionPersistenceExecutor,
    RuntimeChatActionOwnershipError,
)
from services.agent.runtime.ports.action_repository import (
    ActionMutationOutcome, ActionMutationReceipt,
)


@pytest.mark.asyncio
async def test_unwired_chat_action_executor_fails_closed() -> None:
    with pytest.raises(RuntimeChatActionOwnershipError,
                       match="RUNTIME_CHAT_ACTION_EXECUTOR_NOT_WIRED"):
        await FailClosedRuntimeChatActionExecutor().execute(
            ChatActionRequest(
                tool_name="local_data",
                arguments={"query": "x"},
                task_id="task",
                conversation_id="conversation",
                message_id="message",
                user_id="user",
                turn=1,
            )
        )


@pytest.mark.asyncio
async def test_chat_action_request_is_the_only_adapter_input() -> None:
    received: list[ChatActionRequest] = []

    class Adapter:
        async def execute(self, request: ChatActionRequest) -> str:
            received.append(request)
            return "runtime-result"

    request = ChatActionRequest(
        tool_name="file_analyze",
        arguments={"file_id": "artifact:test"},
        task_id="task",
        conversation_id="conversation",
        message_id="message",
        user_id="user",
        turn=2,
    )
    assert await Adapter().execute(request) == "runtime-result"
    assert received == [request]


@pytest.mark.asyncio
async def test_action_loop_adapter_fails_closed_without_dispatch_port() -> None:
    with pytest.raises(RuntimeChatActionOwnershipError,
                       match="RUNTIME_CHAT_ACTION_DISPATCH_NOT_WIRED"):
        await RuntimeChatActionLoopAdapter(None).execute(
            ChatActionRequest(
                tool_name="local_data",
                arguments={},
                task_id="task",
                conversation_id="conversation",
                message_id="message",
                user_id="user",
                turn=1,
            )
        )


@pytest.mark.asyncio
async def test_chat_action_persistence_submits_catalog_bound_action() -> None:
    calls = []

    class Descriptor:
        executor_type = "runtime_read"
        revision = 1

    class Registry:
        def resolve(self, name):
            assert name == "local_data"
            return Descriptor(), object()

        def safety_level(self, name):
            return "safe"

    class Submission:
        async def submit_chat_action(self, **kwargs):
            calls.append(kwargs)
            return ActionMutationReceipt(
                outcome=ActionMutationOutcome.CLAIMED,
                action_id="00000000-0000-0000-0000-000000000001",
            )

    result = await RuntimeChatActionPersistenceExecutor(
        submission=Submission(), registry=Registry(),
    ).execute(ChatActionRequest(
        tool_name="local_data", arguments={"query": "x"}, task_id="task",
        conversation_id="conversation", message_id="message", user_id="user",
        turn=1, tool_call_id="call-1", org_id="org",
    ))

    assert "action_id=00000000-0000-0000-0000-000000000001" in result
    assert calls[0]["request"]["tool_call_id"] == "call-1"
    assert calls[0]["executor_type"] == "runtime_read"


@pytest.mark.asyncio
async def test_chat_action_persistence_submits_confirm_catalog_entry() -> None:
    class Descriptor:
        executor_type = "mutation"
        revision = 1

    class Registry:
        def resolve(self, _name):
            return Descriptor(), object()

        def safety_level(self, _name):
            return "confirm"

    class Submission:
        async def submit_chat_action(self, **kwargs):
            assert kwargs["policy_snapshot"]["safety_level"] == "confirm"
            return ActionMutationReceipt(
                outcome=ActionMutationOutcome.CREATED,
                action_id="00000000-0000-0000-0000-000000000002",
            )

    result = await RuntimeChatActionPersistenceExecutor(
        submission=Submission(), registry=Registry(),
    ).execute(ChatActionRequest(
        tool_name="erp_execute", arguments={}, task_id="task",
        conversation_id="conversation", message_id="message",
        user_id="user", turn=1,
    ))
    assert "action_id=00000000-0000-0000-0000-000000000002" in result
