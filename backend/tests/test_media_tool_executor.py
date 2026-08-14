"""ToolExecutor media actions are Runtime-owned and fail closed when unwired."""

from types import SimpleNamespace

import pytest

from services.agent.runtime.application.chat_action_bridge import ChatActionRequest
from services.agent.tool_executor import ToolExecutor


class _RuntimeExecutor:
    def __init__(self) -> None:
        self.requests: list[ChatActionRequest] = []

    async def execute(self, request: ChatActionRequest) -> str:
        self.requests.append(request)
        return f"Runtime Action {request.tool_name} accepted; reconcile_by_runtime=true"


def _make_executor(runtime=None):
    return ToolExecutor(
        db=SimpleNamespace(), user_id="u1", conversation_id="c1", org_id="org1",
        runtime_action_executor=runtime,
    )


@pytest.mark.asyncio
async def test_empty_image_prompt_is_rejected_before_runtime_submission():
    runtime = _RuntimeExecutor()
    result = await _make_executor(runtime)._generate_image({"prompt": ""})

    assert "不能为空" in result
    assert runtime.requests == []


@pytest.mark.asyncio
async def test_text_to_image_is_submitted_to_runtime():
    runtime = _RuntimeExecutor()
    result = await _make_executor(runtime)._generate_image({
        "prompt": "a cute cat", "image_urls": [], "aspect_ratio": "1:1",
    })

    assert result.status == "accepted"
    assert runtime.requests[0].tool_name == "generate_image"
    assert runtime.requests[0].arguments["prompt"] == "a cute cat"


@pytest.mark.asyncio
async def test_image_to_image_preserves_reference_urls():
    runtime = _RuntimeExecutor()
    urls = ["https://cdn.example.com/source.png"]
    await _make_executor(runtime)._generate_image({
        "prompt": "make it square", "image_urls": urls,
    })

    assert runtime.requests[0].arguments["image_urls"] == urls


@pytest.mark.asyncio
async def test_video_is_submitted_to_runtime():
    runtime = _RuntimeExecutor()
    result = await _make_executor(runtime)._generate_video({"prompt": "a sunset"})

    assert result.status == "accepted"
    assert runtime.requests[0].tool_name == "generate_video"


@pytest.mark.asyncio
async def test_unwired_media_actions_never_construct_provider():
    result = await _make_executor()._generate_image({"prompt": "cat"})

    assert result.status == "error"
    assert result.error_message == "RUNTIME_CHAT_ACTION_EXECUTOR_NOT_WIRED"


class TestToolExecutorInheritance:
    def test_inherits_credit_mixin(self):
        from services.handlers.mixins.credit_mixin import CreditMixin

        assert issubclass(ToolExecutor, CreditMixin)

    def test_has_runtime_media_methods(self):
        executor = _make_executor()
        assert hasattr(executor, "_generate_image")
        assert hasattr(executor, "_generate_video")
        assert executor._handlers["generate_image"] == executor._generate_image
        assert executor._handlers["generate_video"] == executor._generate_video
