"""旧 ChatStreamLoop 兼容 façade；实际循环统一由 execution_engine 执行。"""

from __future__ import annotations

import asyncio
from typing import Any

from services.handlers.chat.execution_engine import (
    ChatExecutionRequest,
    _apply_budget_stop,
    _consume_emit_payloads,
    _run_loop,
)
from services.handlers.chat.execution_sink import WebSocketExecutionSink
from services.handlers.chat.stream_session import StreamDelivery, StreamTotals


class _LegacyLoopSink(WebSocketExecutionSink):
    """保留旧调用方已发送 message_start 的生命周期语义。"""

    async def start(self) -> None:
        self._websocket.register_steer_listener(self._task_id)
        self._websocket.register_cancel_listener(self._task_id)


class ChatStreamLoop:
    """兼容旧构造接口，委托唯一的通道无关模型—工具循环。"""

    def __init__(
        self,
        *,
        handler: Any,
        prepared: Any,
        delivery: StreamDelivery,
        websocket: Any,
        thinking_effort: str | None,
        thinking_mode: str | None,
    ) -> None:
        self.handler = handler
        self.prepared = prepared
        self.delivery = delivery
        self.websocket = websocket
        self.thinking_effort = thinking_effort
        self.thinking_mode = thinking_mode
        self.totals = StreamTotals()
        self.content_blocks: list[dict[str, Any]] = []

    async def run(self) -> None:
        cancellation_event = asyncio.Event()
        sink = _LegacyLoopSink(
            task_id=self.delivery.task_id,
            conversation_id=self.delivery.conversation_id,
            message_id=self.delivery.message_id,
            user_id=self.delivery.user_id,
            model_id="",
            websocket=self.websocket,
            save_content=self.handler._save_accumulated_content,
            save_blocks=self.handler._save_accumulated_blocks,
        )
        monitor = asyncio.create_task(
            self._watch_cancellation(cancellation_event),
        )
        previous_sink = getattr(self.handler, "_execution_sink", None)
        self.handler._execution_sink = sink

        async def on_cancel(
            messages: list[dict[str, Any]],
            blocks: list[dict[str, Any]],
            partial_text: str,
            partial_thinking: str,
            location: str,
        ) -> None:
            await self.handler._handle_user_cancel(
                self.delivery.task_id,
                self.delivery.message_id,
                self.delivery.conversation_id,
                messages,
                blocks,
                location,
                partial_text=partial_text,
                partial_thinking=partial_thinking,
            )

        try:
            await sink.start()
            request = ChatExecutionRequest(
                content=[],
                user_id=self.delivery.user_id,
                conversation_id=self.delivery.conversation_id,
                task_id=self.delivery.task_id,
                message_id=self.delivery.message_id,
                model_id="",
                context_anchor=None,
                permission_mode=self.prepared.permission_mode,
                thinking_effort=self.thinking_effort,
                thinking_mode=self.thinking_mode,
                steer_reader=lambda: self.websocket.check_steer(
                    self.delivery.task_id,
                ),
                on_cancel=on_cancel,
            )
            await _run_loop(
                handler=self.handler,
                request=request,
                prepared=self.prepared,
                cancellation_event=cancellation_event,
                sink=sink,
                totals=self.totals,
                blocks=self.content_blocks,
                runtime=None,
            )
            await _apply_budget_stop(
                self.prepared,
                self.totals,
                self.content_blocks,
                sink,
            )
            await _consume_emit_payloads(
                self.handler,
                self.content_blocks,
                sink,
            )
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            if getattr(self.handler, "_execution_sink", None) is sink:
                self.handler._execution_sink = previous_sink

    async def _watch_cancellation(
        self,
        cancellation_event: asyncio.Event,
    ) -> None:
        while not cancellation_event.is_set():
            if self.websocket.is_cancelled(self.delivery.task_id):
                cancellation_event.set()
                return
            await asyncio.sleep(0.05)
