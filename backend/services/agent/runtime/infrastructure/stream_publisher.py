"""Runtime event-bus adapter for user-visible WebSocket stream messages."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from loguru import logger
from redis.asyncio import Redis

from schemas.websocket import (
    build_message_chunk,
    build_message_start,
    build_stream_end,
    build_thinking_chunk,
    build_tool_call,
)
from services.agent.runtime.domain import StopReason
from services.agent.runtime.ports.model import (
    ModelResponseStreamObserver,
    ModelStepResult,
    ModelStreamDelta,
)
from services.agent.runtime.ports.stream import (
    RuntimeStreamPublisher,
    RuntimeStreamTarget,
)
from services.websocket_redis import WS_CHANNEL


class RedisRuntimeStreamPublisher(RuntimeStreamPublisher):
    """PUBLISH-only Redis adapter; it never subscribes or reads keys."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str | None,
        db: int,
        ssl: bool,
        worker_id: str,
    ) -> None:
        self._worker_id = worker_id
        self._redis = Redis(
            host=host,
            port=port,
            password=password,
            db=db,
            ssl=ssl,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )

    async def publish(
        self,
        *,
        target: RuntimeStreamTarget,
        message: Mapping[str, object],
    ) -> None:
        envelope = {
            "source": self._worker_id,
            "target_type": "user",
            "target_id": target.user_id,
            "org_id": target.org_id,
            "message": dict(message),
        }
        try:
            await self._redis.publish(
                WS_CHANNEL,
                json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception as error:
            logger.warning(
                "runtime_stream_publish_failed | "
                f"task_id={target.task_id} | error={type(error).__name__}"
            )

    async def close(self) -> None:
        await self._redis.aclose()


class RuntimeWebSocketStreamObserver(ModelResponseStreamObserver):
    """Maps Runtime model events to the existing WebSocket message contract."""

    def __init__(
        self,
        *,
        publisher: RuntimeStreamPublisher,
        target: RuntimeStreamTarget,
        model_id: str,
    ) -> None:
        self._publisher = publisher
        self._target = target
        self._model_id = model_id
        self._started = False

    async def stream_started(self, *, model_id: str) -> None:
        if self._started:
            return
        self._started = True
        await self._publisher.publish(
            target=self._target,
            message=build_message_start(
                task_id=self._target.task_id,
                conversation_id=self._target.conversation_id,
                message_id=self._target.message_id,
                model=model_id or self._model_id,
            ),
        )

    async def stream_delta(self, *, delta: ModelStreamDelta) -> None:
        if delta.kind == "text":
            text = _text_value(delta.value.get("text"))
            if text:
                await self._publisher.publish(
                    target=self._target,
                    message=build_message_chunk(
                        task_id=self._target.task_id,
                        conversation_id=self._target.conversation_id,
                        message_id=self._target.message_id,
                        chunk=text,
                    ),
                )
        elif delta.kind == "thinking":
            text = _text_value(delta.value.get("text"))
            if text:
                await self._publisher.publish(
                    target=self._target,
                    message=build_thinking_chunk(
                        task_id=self._target.task_id,
                        conversation_id=self._target.conversation_id,
                        message_id=self._target.message_id,
                        chunk=text,
                    ),
                )

    async def stream_completed(self, *, result: ModelStepResult) -> None:
        if result.stop_reason is StopReason.TOOL_CALLS:
            await self._publisher.publish(
                target=self._target,
                message=build_tool_call(
                    task_id=self._target.task_id,
                    conversation_id=self._target.conversation_id,
                    message_id=self._target.message_id,
                    tool_calls=[
                        {"name": call.name, "call_id": call.call_id}
                        for call in result.tool_calls
                    ],
                    turn=0,
                ),
            )
            return
        if result.stop_reason in {
            StopReason.FINAL,
            StopReason.STRUCTURED_FINAL,
            StopReason.MODEL_REFUSAL,
            StopReason.LENGTH,
            StopReason.CONTENT_FILTER,
            StopReason.PROTOCOL_ERROR,
        }:
            await self._publisher.publish(
                target=self._target,
                message=build_stream_end(
                    task_id=self._target.task_id,
                    conversation_id=self._target.conversation_id,
                    message_id=self._target.message_id,
                ),
            )


def _text_value(value: Any) -> str:
    return value if isinstance(value, str) else ""
