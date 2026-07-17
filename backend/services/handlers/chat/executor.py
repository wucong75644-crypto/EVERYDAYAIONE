"""Conversation Actor 使用的 ChatGenerationExecutor。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Mapping

from pydantic import TypeAdapter

from schemas.message import ContentPart
from services.conversation_execution import GenerationClaim, GenerationOutcome
from services.handlers.chat.execution_engine import (
    ChatExecutionRequest,
    execute_chat,
)
from services.handlers.chat.execution_scope import resolve_execution_scope
from services.handlers.context_snapshot import ContextAnchor


ContentPartsAdapter = TypeAdapter(list[ContentPart])


class ChatGenerationExecutor:
    """把 Actor claim 转换为纯 Chat 执行并返回原子提交产物。"""

    def __init__(
        self,
        db: Any,
        handler_factory: Callable[[Any], Any] | None = None,
        handler_db_factory: Callable[[], Any] | None = None,
        sink_factory: Callable[
            [Mapping[str, Any], GenerationClaim, asyncio.Event], Any
        ] | None = None,
    ) -> None:
        self._db = db
        self._handler_factory = handler_factory or _create_handler
        self._handler_db_factory = handler_db_factory or _get_handler_db
        self._sink_factory = sink_factory

    async def execute(
        self,
        task: Mapping[str, Any],
        claim: GenerationClaim,
        cancellation_event: asyncio.Event,
    ) -> GenerationOutcome:
        _validate_task(task, claim)
        content, execution_scope = await asyncio.gather(
            self._load_input_content(claim),
            resolve_execution_scope(self._db, task, claim.conversation_id),
        )
        handler = self._handler_factory(self._handler_db_factory())
        handler.org_id = task.get("org_id")
        handler.execution_scope = execution_scope
        handler._workspace_user_id = execution_scope.workspace_owner_id
        handler._personal_context_allowed = (
            execution_scope.personal_context_allowed
        )
        params = _parse_params(task.get("request_params"))
        result = await execute_chat(
            handler=handler,
            request=ChatExecutionRequest(
                content=content,
                user_id=str(task["user_id"]),
                conversation_id=claim.conversation_id,
                task_id=claim.task_id,
                message_id=str(task["assistant_message_id"]),
                model_id=_normalize_model_id(task.get("model_id")),
                context_anchor=_build_anchor(claim, task.get("org_id")),
                params=params,
                permission_mode=str(params.get("permission_mode") or "auto"),
                needs_google_search=bool(params.get("_needs_google_search")),
                execution_scope=execution_scope,
            ),
            cancellation_event=cancellation_event,
            sink=(
                self._sink_factory(task, claim, cancellation_event)
                if self._sink_factory else None
            ),
        )
        return GenerationOutcome(
            result_content=[
                part.model_dump(exclude_none=True) for part in result.parts
            ],
            usage=result.usage,
            credits_cost=result.credits_cost,
            tool_digest=result.tool_digest,
        )

    async def _load_input_content(
        self,
        claim: GenerationClaim,
    ) -> list[ContentPart]:
        response = await (
            self._db.table("messages")
            .select("id,conversation_id,turn_id,role,content")
            .eq("id", claim.input_message_id)
            .maybe_single()
            .execute()
        )
        row = response.data if response else None
        if not row:
            raise RuntimeError("ACTOR_INPUT_MESSAGE_MISSING")
        if (
            row.get("conversation_id") != claim.conversation_id
            or row.get("turn_id") != claim.turn_id
            or row.get("role") != "user"
        ):
            raise RuntimeError("ACTOR_INPUT_MESSAGE_SCOPE_MISMATCH")
        raw = row.get("content")
        if isinstance(raw, str):
            raw = json.loads(raw)
        return ContentPartsAdapter.validate_python(raw)


def _validate_task(
    task: Mapping[str, Any],
    claim: GenerationClaim,
) -> None:
    required = ("user_id", "assistant_message_id")
    if any(not task.get(field) for field in required):
        raise RuntimeError("ACTOR_TASK_GENERATION_DATA_MISSING")
    if task.get("conversation_id") != claim.conversation_id:
        raise RuntimeError("ACTOR_TASK_SCOPE_MISMATCH")


def _parse_params(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    return {}


def _normalize_model_id(raw: Any) -> str:
    from services.adapters.factory import DEFAULT_MODEL_ID

    if not raw or raw == "auto":
        return DEFAULT_MODEL_ID
    return str(raw)


def _build_anchor(
    claim: GenerationClaim,
    org_id: Any,
) -> ContextAnchor:
    return ContextAnchor(
        task_id=claim.task_id,
        conversation_id=claim.conversation_id,
        turn_id=claim.turn_id,
        input_message_id=claim.input_message_id,
        base_revision=claim.base_context_revision,
        through_message_id=claim.context_through_message_id,
        org_id=str(org_id) if org_id else None,
    )


def _create_handler(db: Any) -> Any:
    from services.handlers.chat_handler import ChatHandler

    return ChatHandler(db)


def _get_handler_db() -> Any:
    from core.database import get_db

    return get_db()
