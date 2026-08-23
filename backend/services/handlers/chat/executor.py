"""Conversation Actor 使用的 ChatGenerationExecutor。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Mapping

from pydantic import TypeAdapter

from schemas.message import ContentPart, serialize_content_parts
from services.conversation_execution import GenerationClaim, GenerationOutcome
from services.conversation_command_store import ConversationCommandStore
from services.conversation_turn_runtime import ConversationTurnRuntime
from services.conversation_subtasks import ConversationSubtaskStore
from services.conversation_state import ConversationStopRequested
from services.conversation_commands import SafePoint
from services.replay_checkpoint_store import (
    ReplayCheckpointBoundary,
    ReplayCheckpointStore,
)
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
        command_store: ConversationCommandStore | None = None,
        subtask_store: ConversationSubtaskStore | None = None,
        replay_checkpoint_store: ReplayCheckpointStore | None = None,
    ) -> None:
        self._db = db
        self._handler_factory = handler_factory or _create_handler
        self._handler_db_factory = handler_db_factory or _get_handler_db
        self._sink_factory = sink_factory
        self._command_store = command_store
        self._subtask_store = subtask_store
        self._replay_checkpoint_store = replay_checkpoint_store

    async def execute(
        self,
        task: Mapping[str, Any],
        claim: GenerationClaim,
        cancellation_event: asyncio.Event,
    ) -> GenerationOutcome:
        _validate_task(task, claim)
        runtime = ConversationTurnRuntime(
            conversation_id=claim.conversation_id,
            task_id=claim.task_id,
            turn_id=claim.turn_id,
            cancellation_event=cancellation_event,
            execution_token=claim.execution_token,
            command_store=self._command_store,
            subtask_store=self._subtask_store,
            replay_checkpoint_callback=(
                self._build_replay_checkpoint_callback(claim)
                if self._replay_checkpoint_store is not None else None
            ),
        )
        content, execution_scope = await asyncio.gather(
            self._load_input_content(claim),
            resolve_execution_scope(self._db, task, claim.conversation_id),
        )
        replay_context = await self._load_replay_context(task, claim)
        if replay_context and replay_context.get("checkpoint_kind") == "commit_ready":
            return _generation_outcome_from_replay(replay_context)
        handler = self._handler_factory(self._handler_db_factory())
        # Actor 运行时上下文供跨进程审批等待器使用；普通 ChatHandler 不依赖这些字段。
        handler._actor_runtime = runtime
        handler._actor_command_store = self._command_store
        handler._actor_execution_token = claim.execution_token
        handler._actor_cancellation_event = cancellation_event
        handler._actor_enabled = self._command_store is not None
        handler._actor_turn_id = claim.turn_id
        if handler._actor_enabled:
            from services.tool_invocation_store import DatabaseToolInvocationStore
            handler._actor_invocation_store = DatabaseToolInvocationStore(
                handler.db,
            )
        handler.org_id = task.get("org_id")
        handler.execution_scope = execution_scope
        handler._workspace_user_id = execution_scope.workspace_owner_id
        handler._personal_context_allowed = (
            execution_scope.personal_context_allowed
        )
        params = _parse_params(task.get("request_params"))
        sink = (
            self._sink_factory(task, claim, cancellation_event)
            if self._sink_factory else None
        )
        if sink is not None and replay_context is not None:
            seed_progress = getattr(sink, "seed_progress", None)
            if seed_progress is not None:
                seed_progress(
                    task.get("accumulated_content"),
                    replay_context.get("content_blocks"),
                )
        runtime.start_command_watcher()
        try:
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
                    replay_context=replay_context,
                ),
                cancellation_event=cancellation_event,
                sink=sink,
                runtime=runtime,
            )
        finally:
            await runtime.stop_command_watcher()
        replay_payload = result.replay_context or {
            "messages": [],
            "content_blocks": result.content_blocks,
            "turn_index": 0,
            "tool_call_ids": [],
        }
        replay_payload = {
            **replay_payload,
            "checkpoint_kind": "commit_ready",
            "result_content": serialize_content_parts(result.parts),
            "usage": result.usage,
            "credits_cost": result.credits_cost,
            "tool_digest": result.tool_digest,
        }
        await runtime.safe_point(
            SafePoint.BEFORE_COMMIT,
            replay_payload=replay_payload,
        )
        return GenerationOutcome(
            result_content=serialize_content_parts(result.parts),
            usage=result.usage,
            credits_cost=result.credits_cost,
            tool_digest=result.tool_digest,
        )

    def _build_replay_checkpoint_callback(
        self,
        claim: GenerationClaim,
    ) -> Callable[[SafePoint, Mapping[str, Any]], Any]:
        async def write(
            point: SafePoint,
            payload: Mapping[str, Any],
        ) -> dict[str, Any]:
            return await self._write_replay_checkpoint(
                claim, point, payload,
            )

        return write

    async def _write_replay_checkpoint(
        self,
        claim: GenerationClaim,
        point: SafePoint,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._replay_checkpoint_store is None:
            return {"outcome": "disabled"}
        boundary = {
            SafePoint.BEFORE_MODEL: ReplayCheckpointBoundary.BEFORE_MODEL,
            SafePoint.AFTER_TOOL: ReplayCheckpointBoundary.AFTER_TOOL,
            SafePoint.BEFORE_COMMIT: ReplayCheckpointBoundary.BEFORE_COMMIT,
        }.get(point)
        if boundary is None:
            return {"outcome": "ignored"}
        result = await self._replay_checkpoint_store.write(
            task_id=claim.task_id,
            execution_token=claim.execution_token,
            boundary=boundary,
            payload=dict(payload),
        )
        if result.get("outcome") in {
            "ownership_lost", "lease_expired", "terminal",
        }:
            raise ConversationStopRequested("ownership_lost")
        return result

    async def _load_replay_context(
        self,
        task: Mapping[str, Any],
        claim: GenerationClaim,
    ) -> dict[str, Any] | None:
        if self._replay_checkpoint_store is None:
            return None
        read_latest = getattr(self._replay_checkpoint_store, "read_latest", None)
        if read_latest is None:
            # 兼容仅实现写入的旧注入器；生产 Store 必须提供读取能力。
            return None
        result = await read_latest(
            task_id=claim.task_id,
            execution_token=claim.execution_token,
        )
        if result.get("outcome") == "not_found":
            return None
        if result.get("outcome") != "found":
            raise RuntimeError("ACTOR_REPLAY_CHECKPOINT_READ_FAILED")
        payload = result.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("ACTOR_REPLAY_CONTEXT_INVALID")
        return payload

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


def _generation_outcome_from_replay(
    payload: Mapping[str, Any],
) -> GenerationOutcome:
    result_content = payload.get("result_content")
    usage = payload.get("usage")
    credits_cost = payload.get("credits_cost")
    tool_digest = payload.get("tool_digest")
    if (
        not isinstance(result_content, list)
        or not all(isinstance(part, dict) for part in result_content)
        or not isinstance(usage, dict)
        or not isinstance(credits_cost, int)
        or credits_cost < 0
        or (tool_digest is not None and not isinstance(tool_digest, dict))
    ):
        raise RuntimeError("ACTOR_COMMIT_REPLAY_PAYLOAD_INVALID")
    return GenerationOutcome(
        result_content=result_content,
        usage=usage,
        credits_cost=credits_cost,
        tool_digest=tool_digest,
    )


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
