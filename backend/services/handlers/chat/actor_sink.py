"""Conversation Actor 的 WebSocket 进度与 fencing 持久化 Sink。"""

from __future__ import annotations

import asyncio
import uuid
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
from services.conversation_delivery_store import (
    ConversationDeliveryStore,
    DeliverySession,
)


_PERSIST_EVERY_CHUNKS = 20
_DELIVERY_RETRY_ATTEMPTS = 3
_DELIVERY_RETRY_BASE_SECONDS = 0.05


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
    execution_attempt: int = 1


class ActorWebSink:
    """实时推送 Actor 过程事件，并以 fencing token 保存恢复进度。"""

    def __init__(
        self,
        db: Any,
        delivery: ActorDelivery,
        cancellation_event: asyncio.Event,
        websocket: Any,
        delivery_store: ConversationDeliveryStore | None = None,
    ) -> None:
        self._db = db
        self._delivery = delivery
        self._cancellation_event = cancellation_event
        self._websocket = websocket
        self._delivery_store = delivery_store
        self._session: DeliverySession | None = None
        self._text = ""
        self._thinking = ""
        self._blocks: list[dict[str, Any]] = []
        self._chunks_since_persist = 0

    async def start(self) -> None:
        await self._ensure_session()
        delivery_seq = await self._append_event(
            "message_start", {"model": self._delivery.model_id},
        )
        await self._send(
            build_message_start(
                task_id=self._delivery.push_task_id,
                conversation_id=self._delivery.conversation_id,
                message_id=self._delivery.message_id,
                model=self._delivery.model_id,
                delivery_session_id=self._session.session_id if self._session else None,
                stream_id=self._session.stream_id if self._session else None,
                execution_attempt=(
                    self._session.execution_attempt if self._session else None
                ),
                delivery_seq=delivery_seq,
            )
        )

    async def on_text(self, text: str) -> None:
        self._text += text
        self._chunks_since_persist += 1
        delivery_seq = await self._append_event(
            "message_chunk", {"chunk": text},
        )
        await self._send(
            build_message_chunk(
                task_id=self._delivery.push_task_id,
                conversation_id=self._delivery.conversation_id,
                message_id=self._delivery.message_id,
                chunk=text,
                delivery_session_id=self._session.session_id if self._session else None,
                stream_id=self._session.stream_id if self._session else None,
                execution_attempt=(
                    self._session.execution_attempt if self._session else None
                ),
                delivery_seq=delivery_seq,
            )
        )
        if self._chunks_since_persist >= _PERSIST_EVERY_CHUNKS:
            await self._persist()

    async def on_thinking(self, text: str) -> None:
        self._thinking += text
        delivery_seq = await self._append_event(
            "thinking_chunk", {"chunk": text},
        )
        await self._send(
            build_thinking_chunk(
                task_id=self._delivery.push_task_id,
                conversation_id=self._delivery.conversation_id,
                message_id=self._delivery.message_id,
                chunk=text,
                accumulated=self._thinking,
                delivery_session_id=self._session.session_id if self._session else None,
                stream_id=self._session.stream_id if self._session else None,
                execution_attempt=(
                    self._session.execution_attempt if self._session else None
                ),
                delivery_seq=delivery_seq,
            )
        )

    async def on_block(self, block: dict[str, Any]) -> None:
        self._blocks.append(block)
        delivery_seq = await self._append_event(
            "content_block_add", {"block": block},
        )
        await self._send(
            build_content_block_add(
                task_id=self._delivery.push_task_id,
                conversation_id=self._delivery.conversation_id,
                message_id=self._delivery.message_id,
                block=block,
                delivery_session_id=self._session.session_id if self._session else None,
                stream_id=self._session.stream_id if self._session else None,
                execution_attempt=(
                    self._session.execution_attempt if self._session else None
                ),
                delivery_seq=delivery_seq,
            )
        )
        await self._persist()

    async def on_block_update(self, block: dict[str, Any]) -> None:
        """更新已投递的结构化 block，并通过同一交付会话持久化。"""
        updated = dict(block)
        tool_call_id = updated.get("tool_call_id")
        if tool_call_id is None:
            await self.on_block(updated)
            return

        for index, existing in enumerate(self._blocks):
            if existing.get("tool_call_id") == tool_call_id:
                self._blocks[index] = updated
                break
        else:
            self._blocks.append(updated)

        delivery_seq = await self._append_event(
            "content_block_add", {"block": updated},
        )
        await self._send(
            build_content_block_add(
                task_id=self._delivery.push_task_id,
                conversation_id=self._delivery.conversation_id,
                message_id=self._delivery.message_id,
                block=updated,
                delivery_session_id=self._session.session_id if self._session else None,
                stream_id=self._session.stream_id if self._session else None,
                execution_attempt=(
                    self._session.execution_attempt if self._session else None
                ),
                delivery_seq=delivery_seq,
            )
        )
        await self._persist()

    async def flush(self) -> None:
        await self.flush_progress()
        delivery_seq = await self._append_event("stream_end", {})
        await self._send(
            build_stream_end(
                task_id=self._delivery.push_task_id,
                conversation_id=self._delivery.conversation_id,
                message_id=self._delivery.message_id,
                delivery_session_id=self._session.session_id if self._session else None,
                stream_id=self._session.stream_id if self._session else None,
                execution_attempt=(
                    self._session.execution_attempt if self._session else None
                ),
                delivery_seq=delivery_seq,
            )
        )

    async def flush_progress(self) -> None:
        """在安全点前刷入最新进度，不发送 stream_end。"""
        await self._persist()

    async def _persist(self) -> None:
        self._chunks_since_persist = 0
        last_error: Exception | None = None
        for attempt in range(_DELIVERY_RETRY_ATTEMPTS):
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
                if self._delivery_store is not None and self._session is not None:
                    snapshot = await self._delivery_store.save_snapshot(
                        task_id=self._delivery.task_id,
                        execution_token=self._delivery.execution_token,
                        content=self._text,
                        blocks=self._blocks,
                    )
                    if snapshot.get("outcome") == "ownership_lost":
                        self._cancellation_event.set()
                        raise asyncio.CancelledError
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if "OWNERSHIP_LOST" in str(error):
                    self._cancellation_event.set()
                    raise asyncio.CancelledError from error
                last_error = error
                if attempt + 1 < _DELIVERY_RETRY_ATTEMPTS:
                    await asyncio.sleep(_DELIVERY_RETRY_BASE_SECONDS * (2 ** attempt))

        logger.error(
            "actor_progress_write_failed | "
            f"task_id={self._delivery.task_id} | "
            f"attempts={_DELIVERY_RETRY_ATTEMPTS} | "
            f"error={type(last_error).__name__ if last_error else 'unknown'}"
        )
        raise RuntimeError("ACTOR_PROGRESS_PERSISTENCE_FAILED") from last_error

    async def _ensure_session(self) -> None:
        if self._delivery_store is None or self._session is not None:
            return
        try:
            self._session = await self._delivery_store.begin(
                task_id=self._delivery.task_id,
                execution_token=self._delivery.execution_token,
                execution_attempt=self._delivery.execution_attempt,
                message_id=self._delivery.message_id,
            )
        except RuntimeError as error:
            if "OWNERSHIP_LOST" in str(error):
                self._cancellation_event.set()
                raise asyncio.CancelledError from error
            raise

    async def _append_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> int | None:
        if self._delivery_store is None:
            return None
        await self._ensure_session()
        if self._session is None:
            return None
        event_id = str(uuid.uuid4())
        last_error: Exception | None = None
        for attempt in range(_DELIVERY_RETRY_ATTEMPTS):
            try:
                result = await self._delivery_store.append(
                    task_id=self._delivery.task_id,
                    execution_token=self._delivery.execution_token,
                    event_type=event_type,
                    payload=payload,
                    event_id=event_id,
                )
                if result.get("outcome") != "appended":
                    raise RuntimeError(
                        f"DELIVERY_EVENT_APPEND_{result.get('outcome', 'INVALID').upper()}"
                    )
                return int(result["delivery_seq"])
            except RuntimeError as error:
                if any(
                    marker in str(error)
                    for marker in ("OWNERSHIP_LOST", "LEASE_EXPIRED", "TERMINAL")
                ):
                    self._cancellation_event.set()
                    raise asyncio.CancelledError from error
                last_error = error
            except Exception as error:
                last_error = error
            if attempt + 1 < _DELIVERY_RETRY_ATTEMPTS:
                await asyncio.sleep(_DELIVERY_RETRY_BASE_SECONDS * (2 ** attempt))

        logger.error(
            "actor_delivery_event_append_failed | "
            f"task_id={self._delivery.task_id} | event={event_type} | "
            f"event_id={event_id} | attempts={_DELIVERY_RETRY_ATTEMPTS} | "
            f"error={type(last_error).__name__ if last_error else 'unknown'}"
        )
        raise RuntimeError("DELIVERY_EVENT_PERSISTENCE_FAILED") from last_error

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
