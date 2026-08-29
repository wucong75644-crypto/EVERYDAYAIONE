"""WebSocket ExecutionSink 的协议投影测试。"""

from unittest.mock import AsyncMock

import pytest

from services.handlers.chat.execution_sink import WebSocketExecutionSink


class _WebSocket:
    def __init__(self) -> None:
        self.messages = []

    def register_steer_listener(self, _task_id):
        return None

    def register_cancel_listener(self, _task_id):
        return None

    async def send_to_task_or_user(self, task_id, user_id, message):
        self.messages.append((task_id, user_id, message))


@pytest.mark.asyncio
async def test_websocket_sink_projects_shared_events_and_persists_blocks():
    websocket = _WebSocket()
    save_content = AsyncMock()
    save_blocks = AsyncMock()
    sink = WebSocketExecutionSink(
        task_id="task-1",
        conversation_id="conversation-1",
        message_id="message-1",
        user_id="user-1",
        model_id="model-1",
        websocket=websocket,
        save_content=save_content,
        save_blocks=save_blocks,
    )

    await sink.start()
    await sink.on_text("答案")
    await sink.on_tool_calls(
        [{"id": "call-1", "name": "query"}],
        1,
    )
    await sink.on_block({
        "type": "tool_step",
        "tool_call_id": "call-1",
        "tool_name": "query",
        "status": "running",
    })
    await sink.on_block_update({
        "type": "tool_step",
        "tool_call_id": "call-1",
        "tool_name": "query",
        "status": "completed",
        "output": "ok",
    })
    await sink.on_tool_result(
        tool_name="query",
        tool_call_id="call-1",
        success=True,
        summary="ok",
        turn=1,
    )
    await sink.flush()

    types = [message[2]["type"] for message in websocket.messages]
    assert types == [
        "message_start",
        "message_chunk",
        "tool_call",
        "content_block_add",
        "content_block_add",
        "tool_result",
        "stream_end",
    ]
    assert sink.blocks[-1]["status"] == "completed"
    save_blocks.assert_awaited()
