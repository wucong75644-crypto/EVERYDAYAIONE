"""通道无关 Chat 执行事件出口。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from loguru import logger

from schemas.websocket import (
    build_content_block_add,
    build_message_chunk,
    build_message_start,
    build_stream_end,
    build_thinking_chunk,
    build_tool_call,
    build_tool_result,
)


class ExecutionSink(Protocol):
    """执行内核的可选过程事件接收器。"""

    async def start(self) -> None:
        """生成开始。"""

    async def on_text(self, text: str) -> None:
        """接收模型文本增量。"""

    async def on_thinking(self, text: str) -> None:
        """接收模型思考增量。"""

    async def on_block(self, block: dict[str, Any]) -> None:
        """接收已形成的结构化内容块。"""

    async def on_block_update(self, block: dict[str, Any]) -> None:
        """接收对已有结构化内容块的更新。"""

    async def on_tool_calls(
        self, tool_calls: list[dict[str, Any]], turn: int,
    ) -> None:
        """接收一轮已完成解析的工具调用。"""

    async def on_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        success: bool,
        summary: str,
        turn: int,
    ) -> None:
        """接收工具结果通知。"""

    async def flush(self) -> None:
        """提交剩余过程状态并结束流。"""


class CollectingExecutionSink:
    """企微和 Actor 使用的无副作用收集器。"""

    def __init__(self) -> None:
        self.text = ""
        self.thinking = ""
        self.blocks: list[dict[str, Any]] = []

    async def start(self) -> None:
        return None

    async def on_text(self, text: str) -> None:
        self.text += text

    async def on_thinking(self, text: str) -> None:
        self.thinking += text

    async def on_block(self, block: dict[str, Any]) -> None:
        self.blocks.append(block)

    async def on_block_update(self, block: dict[str, Any]) -> None:
        tool_call_id = block.get("tool_call_id")
        if tool_call_id is None:
            self.blocks.append(block)
            return
        for index, existing in enumerate(self.blocks):
            if existing.get("tool_call_id") == tool_call_id:
                self.blocks[index] = dict(block)
                return
        self.blocks.append(block)

    async def on_tool_calls(
        self, _tool_calls: list[dict[str, Any]], _turn: int,
    ) -> None:
        return None

    async def on_tool_result(self, **_kwargs: Any) -> None:
        return None

    async def flush(self) -> None:
        return None


class WebSocketExecutionSink:
    """旧 Web 协议的事件投影与请求级进度持久化适配器。"""

    def __init__(
        self,
        *,
        task_id: str,
        conversation_id: str,
        message_id: str,
        user_id: str,
        model_id: str,
        websocket: Any,
        save_content: Callable[[str, str], Awaitable[None]],
        save_blocks: Callable[[str, list[dict[str, Any]]], Awaitable[None]],
    ) -> None:
        self._task_id = task_id
        self._conversation_id = conversation_id
        self._message_id = message_id
        self._user_id = user_id
        self._model_id = model_id
        self._websocket = websocket
        self._save_content = save_content
        self._save_blocks = save_blocks
        self.emit_empty_thinking = True
        self.text = ""
        self.thinking = ""
        self.blocks: list[dict[str, Any]] = []
        self._chunk_count = 0

    async def start(self) -> None:
        self._websocket.register_steer_listener(self._task_id)
        self._websocket.register_cancel_listener(self._task_id)
        await self._send(
            build_message_start(
                task_id=self._task_id,
                conversation_id=self._conversation_id,
                message_id=self._message_id,
                model=self._model_id,
            )
        )

    async def on_text(self, text: str) -> None:
        self.text += text
        self._chunk_count += 1
        await self._send(
            build_message_chunk(
                task_id=self._task_id,
                conversation_id=self._conversation_id,
                message_id=self._message_id,
                chunk=text,
            )
        )
        if self._chunk_count % 20 == 0:
            await self._save_content(self._task_id, self.text)

    async def on_thinking(self, text: str) -> None:
        self.thinking += text
        await self._send(
            build_thinking_chunk(
                task_id=self._task_id,
                conversation_id=self._conversation_id,
                message_id=self._message_id,
                chunk=text,
                accumulated=self.thinking,
            )
        )

    async def on_block(self, block: dict[str, Any]) -> None:
        self.blocks.append(dict(block))
        await self._send_block(block)
        await self._save_blocks(self._task_id, self.blocks)

    async def on_block_update(self, block: dict[str, Any]) -> None:
        updated = dict(block)
        tool_call_id = updated.get("tool_call_id")
        if tool_call_id is None:
            await self.on_block(updated)
            return
        for index, existing in enumerate(self.blocks):
            if existing.get("tool_call_id") == tool_call_id:
                self.blocks[index] = updated
                break
        else:
            self.blocks.append(updated)
        await self._send_block(updated)
        await self._save_blocks(self._task_id, self.blocks)

    async def on_tool_calls(
        self, tool_calls: list[dict[str, Any]], turn: int,
    ) -> None:
        await self._send(
            build_tool_call(
                task_id=self._task_id,
                conversation_id=self._conversation_id,
                message_id=self._message_id,
                tool_calls=[
                    {"name": call["name"], "id": call["id"]}
                    for call in tool_calls
                ],
                turn=turn,
            )
        )

    async def on_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        success: bool,
        summary: str,
        turn: int,
    ) -> None:
        await self._send(
            build_tool_result(
                task_id=self._task_id,
                conversation_id=self._conversation_id,
                message_id=self._message_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                success=success,
                summary=summary,
                turn=turn,
            )
        )

    async def flush(self) -> None:
        await self._save_content(self._task_id, self.text)
        await self._save_blocks(self._task_id, self.blocks)
        await self._send(
            build_stream_end(
                task_id=self._task_id,
                conversation_id=self._conversation_id,
                message_id=self._message_id,
            )
        )

    async def _send_block(self, block: dict[str, Any]) -> None:
        await self._send(
            build_content_block_add(
                task_id=self._task_id,
                conversation_id=self._conversation_id,
                message_id=self._message_id,
                block=block,
            )
        )

    async def _send(self, message: dict[str, Any]) -> None:
        try:
            await self._websocket.send_to_task_or_user(
                self._task_id,
                self._user_id,
                message,
            )
        except Exception as error:
            logger.warning(
                "legacy_execution_delivery_failed | "
                f"task_id={self._task_id} | "
                f"message_type={message.get('type')} | "
                f"error={type(error).__name__}"
            )
