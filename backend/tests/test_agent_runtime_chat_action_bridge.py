"""Chat tools must use the Runtime Action boundary exclusively."""

from __future__ import annotations

import pytest

from services.agent.runtime.application.chat_action_bridge import (
    ChatActionRequest,
    FailClosedRuntimeChatActionExecutor,
    RuntimeChatActionLoopAdapter,
    RuntimeChatActionOwnershipError,
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
