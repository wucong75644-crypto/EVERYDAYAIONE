"""旧 Web 入口通过共享执行内核与协议 Sink 运行。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from schemas.message import TextPart
from services.handlers.chat.execution_engine import ChatExecutionRequest
from services.handlers.chat.stream_runner import (
    LegacyStreamRequest,
    run_legacy_chat_stream,
)


class _WebSocket:
    def __init__(self) -> None:
        self.messages = []

    def register_steer_listener(self, _task_id: str) -> None:
        return None

    def register_cancel_listener(self, _task_id: str) -> None:
        return None

    def unregister_steer_listener(self, _task_id: str) -> None:
        return None

    def unregister_cancel_listener(self, _task_id: str) -> None:
        return None

    def is_cancelled(self, _task_id: str) -> bool:
        return False

    def check_steer(self, _task_id: str) -> str | None:
        return None

    async def send_to_task_or_user(self, task_id, user_id, message) -> None:
        self.messages.append((task_id, user_id, message))


@pytest.mark.asyncio
async def test_legacy_stream_delegates_to_shared_execution_engine(monkeypatch) -> None:
    handler = SimpleNamespace(
        org_id="org-1",
        _adapter=None,
        _pending_emit_payloads=[],
        _save_accumulated_content=AsyncMock(),
        _save_accumulated_blocks=AsyncMock(),
        _extract_text_content=lambda content: content[0].text,
        on_complete=AsyncMock(),
        _record_breaker_result=lambda **_kwargs: None,
        _dispatch_post_tasks=lambda **_kwargs: None,
    )
    websocket = _WebSocket()
    captured: dict[str, object] = {}

    async def fake_execute_chat(*, handler, request, cancellation_event, sink):
        captured["handler"] = handler
        captured["request"] = request
        captured["sink"] = sink
        captured["event"] = cancellation_event
        sink.text = "答案"
        return SimpleNamespace(
            parts=[TextPart(text="答案")],
            usage={"prompt_tokens": 2, "completion_tokens": 1},
            credits_cost=3,
            tool_digest=None,
        )

    monkeypatch.setattr(
        "services.handlers.chat.stream_runner.execute_chat",
        fake_execute_chat,
    )

    await run_legacy_chat_stream(
        handler=handler,
        request=LegacyStreamRequest(
            task_id="task-1",
            message_id="message-1",
            conversation_id="conversation-1",
            user_id="user-1",
            content=[TextPart(text="问题")],
            model_id="model-1",
            thinking_effort="high",
            thinking_mode="deep",
            params={"permission_mode": "auto"},
        ),
        websocket=websocket,
    )

    request = captured["request"]
    assert isinstance(request, ChatExecutionRequest)
    assert request.thinking_effort == "high"
    assert request.thinking_mode == "deep"
    assert request.steer_reader is not None
    handler.on_complete.assert_awaited_once()
    assert handler.on_complete.call_args.kwargs["credits_consumed"] == 3


def test_legacy_runner_no_longer_imports_chat_stream_loop() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "services/handlers/chat/stream_runner.py"
    ).read_text(encoding="utf-8")
    assert "ChatStreamLoop" not in source
