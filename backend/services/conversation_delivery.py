"""Conversation Actor 数据库终态后的 Web 投递与资源释放。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

from loguru import logger

from schemas.websocket import build_message_done, build_message_error
from services.message_utils import format_message
from services.task_limit_service import release_task_slot


class ActorTerminalDelivery:
    """以数据库终态为事实源发送 best-effort WebSocket 通知。"""

    def __init__(
        self,
        db: Any,
        websocket: Any,
        post_handler_factory: Any | None = None,
    ) -> None:
        self._db = db
        self._websocket = websocket
        self._post_handler_factory = post_handler_factory

    async def notify(
        self,
        task: Mapping[str, Any],
        terminal_result: Mapping[str, Any],
    ) -> None:
        if terminal_result.get("outcome") in {
            "ownership_lost",
            "lease_expired",
        }:
            return
        current = await self._load_task(str(task["id"]))
        status = current.get("status")
        if status not in {"completed", "failed", "cancelled"}:
            return
        await release_task_slot(current)
        if status == "cancelled":
            return
        if status == "completed":
            await self._send_completed(current, terminal_result)
        else:
            await self._send_failed(current)

    async def _send_completed(
        self,
        task: Mapping[str, Any],
        terminal_result: Mapping[str, Any],
    ) -> None:
        message = await self._load_message(str(task["assistant_message_id"]))
        push_task_id = _push_task_id(task)
        await self._websocket.send_to_task_or_user(
            push_task_id,
            str(task["user_id"]),
            build_message_done(
                task_id=push_task_id,
                conversation_id=str(task["conversation_id"]),
                message=format_message(message),
                credits_consumed=int(message.get("credits_cost") or 0),
            ),
            org_id=task.get("org_id"),
        )
        self._dispatch_completed_hooks(task, message, terminal_result)

    async def _send_failed(self, task: Mapping[str, Any]) -> None:
        push_task_id = _push_task_id(task)
        await self._websocket.send_to_task_or_user(
            push_task_id,
            str(task["user_id"]),
            build_message_error(
                task_id=push_task_id,
                conversation_id=str(task["conversation_id"]),
                message_id=str(task["assistant_message_id"]),
                error_code="GENERATION_FAILED",
                error_message=str(task.get("error_message") or "生成失败"),
            ),
            org_id=task.get("org_id"),
        )
        self._dispatch_failed_hooks(task)

    def _dispatch_completed_hooks(
        self,
        task: Mapping[str, Any],
        message: Mapping[str, Any],
        terminal_result: Mapping[str, Any],
    ) -> None:
        handler = self._build_post_handler(task)
        if handler is None:
            return
        from services.handlers.chat_context.content_extractors import (
            extract_text_from_content,
        )

        params = _as_dict(task.get("request_params"))
        result = _as_dict(task.get("result"))
        usage = _as_dict(result.get("usage"))
        usage.setdefault("prompt_tokens", 0)
        usage.setdefault("completion_tokens", 0)
        handler._dispatch_post_tasks(
            user_id=str(task["user_id"]),
            conversation_id=str(task["conversation_id"]),
            text_content=str(params.get("content") or ""),
            accumulated_text=extract_text_from_content(
                message.get("content"),
            ),
            model_id=str(task.get("model_id") or "unknown"),
            final_usage=usage,
            elapsed_ms=0,
            retry_context=None,
            input_message_id=str(task.get("input_message_id") or ""),
            output_message_id=str(task.get("assistant_message_id") or ""),
            through_revision=_committed_revision(task, terminal_result),
        )
        retry_from_model = params.get("_retry_from_model")
        if retry_from_model:
            asyncio.create_task(
                handler._extract_retry_knowledge(
                    task_type="chat",
                    model_id=str(task.get("model_id") or "unknown"),
                    retry_from_model=str(retry_from_model),
                )
            )

    def _dispatch_failed_hooks(self, task: Mapping[str, Any]) -> None:
        handler = self._build_post_handler(task)
        if handler is None:
            return
        model_id = str(task.get("model_id") or "unknown")
        error_message = str(task.get("error_message") or "生成失败")
        asyncio.create_task(
            handler._record_knowledge_metric(
                task_type="chat",
                model_id=model_id,
                status="failed",
                error_code="GENERATION_FAILED",
                cost_time_ms=0,
                user_id=str(task["user_id"]),
                org_id=task.get("org_id"),
            )
        )
        asyncio.create_task(
            handler._extract_failure_knowledge(
                task_type="chat",
                model_id=model_id,
                error_message=error_message,
            )
        )

    def _build_post_handler(
        self,
        task: Mapping[str, Any],
    ) -> Any | None:
        if self._post_handler_factory is None:
            return None
        handler = self._post_handler_factory()
        handler.org_id = task.get("org_id")
        return handler

    async def _load_task(self, task_id: str) -> dict[str, Any]:
        result = await (
            self._db.table("tasks")
            .select("*")
            .eq("id", task_id)
            .maybe_single()
            .execute()
        )
        if not result.data:
            raise RuntimeError("ACTOR_DELIVERY_TASK_MISSING")
        return dict(result.data)

    async def _load_message(self, message_id: str) -> dict[str, Any]:
        result = await (
            self._db.table("messages")
            .select("*")
            .eq("id", message_id)
            .maybe_single()
            .execute()
        )
        if not result.data:
            raise RuntimeError("ACTOR_DELIVERY_MESSAGE_MISSING")
        return dict(result.data)


def _push_task_id(task: Mapping[str, Any]) -> str:
    value = (
        task.get("client_task_id")
        or task.get("external_task_id")
        or task.get("id")
    )
    if not value:
        logger.error(f"actor_delivery_task_id_missing | task={task.get('id')}")
        raise RuntimeError("ACTOR_DELIVERY_TASK_ID_MISSING")
    return str(value)


def _committed_revision(
    task: Mapping[str, Any],
    terminal_result: Mapping[str, Any],
) -> int | None:
    """优先采用原子提交返回的闭合 revision，兼容旧结果推导。"""
    closed_revision = terminal_result.get("closed_revision")
    if isinstance(closed_revision, int) and not isinstance(
        closed_revision,
        bool,
    ) and closed_revision > 0:
        return closed_revision
    base_revision = task.get("base_context_revision")
    if isinstance(base_revision, bool) or not isinstance(base_revision, int):
        return None
    return base_revision + 1 if base_revision >= 0 else None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}
