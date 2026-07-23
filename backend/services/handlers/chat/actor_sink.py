"""Conversation Actor 的 WebSocket 进度与 fencing 持久化 Sink。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from loguru import logger
from psycopg.types.json import Jsonb

from schemas.websocket import (
    build_content_block_add,
    build_message_chunk,
    build_message_start,
    build_stream_end,
    build_thinking_chunk,
)


_PERSIST_EVERY_CHUNKS = 20


@dataclass(frozen=True)
class ActorDelivery:
    task_id: str
    push_task_id: str
    execution_token: str
    conversation_id: str
    message_id: str
    user_id: str
    org_id: str | None
    model_id: str


class ActorWebSink:
    """实时推送 Actor 过程事件，并以 fencing token 保存恢复进度。"""

    def __init__(
        self,
        db: Any,
        delivery: ActorDelivery,
        cancellation_event: asyncio.Event,
        websocket: Any,
    ) -> None:
        self._db = db
        self._delivery = delivery
        self._cancellation_event = cancellation_event
        self._websocket = websocket
        self._text = ""
        self._thinking = ""
        self._blocks: list[dict[str, Any]] = []
        self._chunks_since_persist = 0

    async def start(self) -> None:
        self._websocket.register_steer_listener(
            self._delivery.push_task_id, self._delivery.org_id,
        )
        self._websocket.register_cancel_listener(
            self._delivery.push_task_id,
        )
        await self._send(
            build_message_start(
                task_id=self._delivery.push_task_id,
                conversation_id=self._delivery.conversation_id,
                message_id=self._delivery.message_id,
                model=self._delivery.model_id,
            )
        )

    async def on_text(self, text: str) -> None:
        self._text += text
        self._chunks_since_persist += 1
        await self._send(
            build_message_chunk(
                task_id=self._delivery.push_task_id,
                conversation_id=self._delivery.conversation_id,
                message_id=self._delivery.message_id,
                chunk=text,
            )
        )
        if self._chunks_since_persist >= _PERSIST_EVERY_CHUNKS:
            await self._persist()

    async def on_thinking(self, text: str) -> None:
        self._thinking += text
        await self._send(
            build_thinking_chunk(
                task_id=self._delivery.push_task_id,
                conversation_id=self._delivery.conversation_id,
                message_id=self._delivery.message_id,
                chunk=text,
                accumulated=self._thinking,
            )
        )

    async def on_block(self, block: dict[str, Any]) -> None:
        self._blocks.append(block)
        await self._send(
            build_content_block_add(
                task_id=self._delivery.push_task_id,
                conversation_id=self._delivery.conversation_id,
                message_id=self._delivery.message_id,
                block=block,
            )
        )
        await self._persist()

    async def flush(self) -> None:
        await self._persist()
        await self._send(
            build_stream_end(
                task_id=self._delivery.push_task_id,
                conversation_id=self._delivery.conversation_id,
                message_id=self._delivery.message_id,
            )
        )

    def take_steer(self) -> str | None:
        return self._websocket.check_steer(
            self._delivery.push_task_id, self._delivery.org_id,
        )

    def is_cancelled(self) -> bool:
        return self._websocket.is_cancelled(self._delivery.push_task_id)

    async def close(self) -> None:
        self._websocket.unregister_steer_listener(
            self._delivery.push_task_id, self._delivery.org_id,
        )
        self._websocket.unregister_cancel_listener(
            self._delivery.push_task_id,
        )

    async def _persist(self) -> None:
        self._chunks_since_persist = 0
        try:
            response = await self._db.rpc(
                "update_generation_progress",
                {
                    "p_task_id": self._delivery.task_id,
                    "p_execution_token": self._delivery.execution_token,
                    "p_accumulated_content": self._text,
                    "p_accumulated_blocks": Jsonb(self._blocks),
                },
            ).execute()
            result = response.data if response else None
            if not isinstance(result, dict):
                raise RuntimeError("ACTOR_PROGRESS_RESULT_INVALID")
            if result.get("outcome") in {
                "ownership_lost",
                "lease_expired",
                "terminal",
            }:
                self._cancellation_event.set()
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "actor_progress_write_failed | "
                f"task_id={self._delivery.task_id} | "
                f"error={type(error).__name__}"
            )

    async def _send(self, message: dict[str, Any]) -> None:
        try:
            await self._websocket.send_to_task_or_user(
                self._delivery.push_task_id,
                self._delivery.user_id,
                message,
                org_id=self._delivery.org_id,
            )
        except Exception as error:
            logger.warning(
                "actor_progress_delivery_failed | "
                f"task_id={self._delivery.task_id} | "
                f"message_type={message.get('type')} | "
                f"error={type(error).__name__}"
            )
