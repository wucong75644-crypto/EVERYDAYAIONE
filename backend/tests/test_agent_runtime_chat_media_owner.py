from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agent.tool_executor import ToolExecutor
from services.agent.runtime.application.chat_action_bridge import ChatActionRequest


class _RuntimeExecutor:
    def __init__(self) -> None:
        self.requests: list[ChatActionRequest] = []

    async def execute(self, request: ChatActionRequest) -> str:
        self.requests.append(request)
        return f"Runtime Action created: {request.tool_name}"


def _executor(runtime: _RuntimeExecutor | None = None) -> ToolExecutor:
    executor = ToolExecutor(
        db=SimpleNamespace(), user_id="user-1", conversation_id="conv-1",
        org_id="org-1", runtime_action_executor=runtime,
    )
    executor._task_id = "task-1"
    executor._message_id = "message-1"
    executor._turn = 2
    executor._tool_call_id = "call-1"
    executor._model_id = "model-1"
    return executor


@pytest.mark.asyncio
async def test_text_to_image_uses_runtime_action_without_provider() -> None:
    runtime = _RuntimeExecutor()
    result = await _executor(runtime)._generate_image({
        "prompt": "a red cup", "image_urls": [], "aspect_ratio": "1:1",
    })

    assert result.status == "accepted"
    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    assert request.tool_name == "generate_image"
    assert request.arguments["prompt"] == "a red cup"
    assert request.arguments["image_urls"] == []
    assert request.arguments["task_id"] == "task-1"
    assert request.arguments["message_id"] == "message-1"
    assert request.arguments["user_id"] == "user-1"
    assert request.arguments["org_id"] == "org-1"
    assert result.metadata["runtime_owned"] is True


@pytest.mark.asyncio
async def test_image_to_image_preserves_runtime_arguments() -> None:
    runtime = _RuntimeExecutor()
    source = ["https://cdn.example/image.png"]
    result = await _executor(runtime)._image_agent({
        "task": "make it square", "image_urls": source,
        "style_directive": "warm", "history_images": [{"url": source[0]}],
    })

    assert result.status == "accepted"
    request = runtime.requests[0]
    assert request.tool_name == "generate_image"
    assert request.arguments["prompt"] == "make it square"
    assert request.arguments["image_urls"] == source
    assert request.arguments["style_directive"] == "warm"
    assert request.arguments["history_images"] == [{"url": source[0]}]


@pytest.mark.asyncio
async def test_runtime_unknown_is_not_retried_by_legacy_tool_executor() -> None:
    class _UnknownRuntime(_RuntimeExecutor):
        async def execute(self, request: ChatActionRequest) -> str:
            self.requests.append(request)
            return "Runtime Action unknown; reconcile_by_runtime=true"

    result = await _executor(_UnknownRuntime())._image_agent({
        "task": "reconcile this", "image_urls": [],
    })

    assert result.status == "unknown"
    assert result.metadata["reconcile_only"] is True
    assert result.metadata["readback"] == "runtime_projection"


@pytest.mark.asyncio
async def test_unwired_media_action_fails_closed_without_provider() -> None:
    result = await _executor()._generate_image({"prompt": "cat"})

    assert result.status == "error"
    assert result.error_message == "RUNTIME_CHAT_ACTION_EXECUTOR_NOT_WIRED"


@pytest.mark.asyncio
async def test_video_tool_uses_same_runtime_action_boundary() -> None:
    runtime = _RuntimeExecutor()
    result = await _executor(runtime)._generate_video({"prompt": "a sunset"})

    assert result.status == "accepted"
    assert runtime.requests[0].tool_name == "generate_video"
