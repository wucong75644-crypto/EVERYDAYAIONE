"""Conversation Actor 使用的 ChatGenerationExecutor。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, Callable, Mapping

from loguru import logger
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
        request = ChatExecutionRequest(
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
        )
        result = await self._execute_with_retry(
            handler=handler,
            task=task,
            claim=claim,
            request=request,
            cancellation_event=cancellation_event,
        )
        from services.agent.runtime.artifacts import materialize_artifacts
        from services.agent.runtime.context import build_turn_context_items

        artifacts = await materialize_artifacts(
            result.artifact_drafts,
            task_id=claim.task_id,
            user_id=str(task["user_id"]),
            org_id=str(task["org_id"]) if task.get("org_id") else None,
        )
        context_items = build_turn_context_items(
            input_content=content,
            output_blocks=(
                result.content_blocks
                or [
                    part.model_dump(exclude_none=True)
                    for part in result.parts
                ]
            ),
            artifacts=result.artifact_drafts,
            input_message_id=claim.input_message_id,
            output_message_id=str(task["assistant_message_id"]),
        )
        return GenerationOutcome(
            result_content=[
                part.model_dump(exclude_none=True) for part in result.parts
            ],
            usage=result.usage,
            credits_cost=result.credits_cost,
            tool_digest=result.tool_digest,
            data_evidence=result.data_evidence,
            context_items=context_items,
            artifacts=artifacts,
            context_receipts=result.context_receipts,
            compaction=result.compaction,
        )

    async def _execute_with_retry(
        self,
        *,
        handler: Any,
        task: Mapping[str, Any],
        claim: GenerationClaim,
        request: ChatExecutionRequest,
        cancellation_event: asyncio.Event,
    ) -> Any:
        retry_context = None
        while True:
            try:
                result = await execute_chat(
                    handler=handler,
                    request=request,
                    cancellation_event=cancellation_event,
                    sink=(
                        self._sink_factory(
                            task,
                            claim,
                            cancellation_event,
                        )
                        if self._sink_factory else None
                    ),
                )
                _record_breaker(handler, request.model_id, True)
                return result
            except Exception as error:
                _record_breaker(
                    handler,
                    request.model_id,
                    False,
                    error,
                )
                if not request.params.get("_is_smart_mode"):
                    raise
                retry_context = handler._build_retry_context(
                    params=request.params,
                    content=request.content,
                    model_id=request.model_id,
                    error=str(error),
                    existing_ctx=retry_context,
                )
                if not retry_context or not retry_context.can_retry:
                    raise
                decision = await handler._route_retry(retry_context)
                new_model = (
                    decision.recommended_model if decision else None
                )
                if not new_model or new_model == request.model_id:
                    raise
                attempt = len(retry_context.failed_attempts)
                push_task_id = _push_task_id(task, claim)
                logger.info(
                    "actor_chat_retry | "
                    f"task_id={claim.task_id} | "
                    f"failed={request.model_id} | new={new_model} | "
                    f"attempt={attempt}"
                )
                await handler._send_retry_notification(
                    push_task_id,
                    claim.conversation_id,
                    str(task["user_id"]),
                    new_model,
                    attempt,
                )
                await self._update_task_model(
                    claim.task_id,
                    new_model,
                    request.params,
                    request.model_id,
                )
                request = replace(request, model_id=new_model)

    async def _update_task_model(
        self,
        task_id: str,
        model_id: str,
        params: Mapping[str, Any],
        failed_model: str,
    ) -> None:
        try:
            updated_params = dict(params)
            updated_params["_retry_from_model"] = failed_model
            await (
                self._db.table("tasks")
                .update(
                    {
                        "model_id": model_id,
                        "request_params": updated_params,
                    }
                )
                .eq("id", task_id)
                .execute()
            )
        except Exception as error:
            logger.warning(
                "actor_retry_model_update_failed | "
                f"task_id={task_id} | error={type(error).__name__}"
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


def _push_task_id(
    task: Mapping[str, Any],
    claim: GenerationClaim,
) -> str:
    return str(
        task.get("client_task_id")
        or task.get("external_task_id")
        or claim.task_id
    )


def _record_breaker(
    handler: Any,
    model_id: str,
    success: bool,
    error: Exception | None = None,
) -> None:
    recorder = getattr(handler, "_record_breaker_result", None)
    if callable(recorder):
        recorder(model_id, success=success, error=error)


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
