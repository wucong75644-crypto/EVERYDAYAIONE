"""Conversation Actor 使用的 ChatGenerationExecutor。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from pydantic import TypeAdapter

from schemas.message import ContentPart, serialize_content_parts
from services.conversation_execution import GenerationClaim, GenerationOutcome
from services.conversation_command_store import ConversationCommandStore
from services.conversation_turn_runtime import ConversationTurnRuntime
from services.conversation_subtasks import ConversationSubtaskStore
from services.conversation_state import ConversationPauseRequested, ConversationStopRequested
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
from services.task_execution.followup import FollowupRestart
from services.task_execution.bootstrap import admits_new_task


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
        from services.agent.observability.chat_lifecycle import chat_phase
        chat_phase('executor.begin', task_id=claim.task_id, turn_id=claim.turn_id, attempt=claim.execution_attempt)
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
        chat_phase('executor.inputs', task_id=claim.task_id, turn_id=claim.turn_id, attempt=claim.execution_attempt)
        replay_context = await self._load_replay_context(task, claim)
        from core.config import get_settings
        if (not (replay_context or {}).get('_task_execution') and not (replay_context or {}).get('_task_followup')
                and not (replay_context or {}).get('_task_requirement_review') and self._replay_checkpoint_store is not None
                and getattr(get_settings(),'agent_task_execution_enabled',False)
                and admits_new_task(execution_scope)
                and (task.get('delivery_context') or {}).get('input_kind')!='agent_task_continuation'):
            from services.task_execution.database_scope import task_database
            from types import SimpleNamespace
            followup_db=task_database(self._db,SimpleNamespace(actor_user_id=execution_scope.actor_user_id,org_id=task.get('org_id')))
            args = {'p_task_id': claim.task_id, 'p_execution_token': claim.execution_token}
            followup_read = followup_db.rpc('read_agent_task_followup', args).execute()
            saved_policy = (replay_context or {}).get('_task_bootstrap')
            model_directed = (saved_policy['input'].get('model_directed', False) if saved_policy else
                              getattr(get_settings(), 'agent_task_model_directed_enabled', False))
            if model_directed and not (replay_context or {}).get('_task_direct_plan'):
                response, plans = await asyncio.gather(
                    followup_read, followup_db.rpc('read_agent_working_plan_candidates', args).execute(),
                )
                if not plans or plans.data.get('outcome') not in {'found', 'not_found'}:
                    raise RuntimeError('TASK_WORKING_PLAN_CANDIDATES_UNCONFIRMED')
                runtime.task_plan_candidates = {
                    candidate['plan_id']: candidate for candidate in plans.data.get('candidates', [])
                }
            else:
                response = await followup_read
            if not response or response.data.get('outcome') not in {'found', 'not_found'}:
                raise RuntimeError('TASK_FOLLOWUP_READ_UNCONFIRMED')
            runtime.task_followup_candidate = response.data.get('source')
            async def adopt_followup(source,observation,payload):
                parameters={'p_task_id':claim.task_id,'p_execution_token':claim.execution_token,
                    'p_root_task_id':source['state']['root_task_id'],'p_expected_revision':source['state']['revision'],
                    'p_source_sha256':source['source_sha256'],'p_observation':observation,'p_payload':payload}
                try:response=await followup_db.rpc('adopt_agent_task_followup',parameters).execute()
                except Exception:
                    # Repeating this local transaction only reconciles the same
                    # original message/owner. It never sends a business request.
                    response=await followup_db.rpc('adopt_agent_task_followup',parameters).execute()
                return response.data
            runtime.task_followup_adopt=adopt_followup
        if (replay_context is None and (task.get('delivery_context') or {}).get('input_kind') == 'agent_task_continuation'):
            source_kind=(task.get('delivery_context') or {}).get('source_kind','image')
            if source_kind in {'scheduled','video'}:
                from services.task_execution.receipt_continuation import prepare_receipt_continuation as prepare_continuation
            elif source_kind=='image':
                from services.task_execution.image_continuation import prepare_image_continuation as prepare_continuation
            else:
                raise ValueError('TASK_CONTINUATION_SOURCE_UNSUPPORTED')
            replay_context = await prepare_continuation(self._db,task=task,claim=claim,execution_scope=execution_scope)
        from services.task_execution.replay import restore_task_state
        runtime.task_execution_state = restore_task_state(
            replay_context, conversation_id=claim.conversation_id,
            actor_user_id=execution_scope.actor_user_id, org_id=task.get('org_id'),
            execution_scope=execution_scope,
        ) if replay_context and '_task_execution' in replay_context else None
        runtime.task_native_event_pending=((task.get('delivery_context') or {}).get('input_kind')=='agent_task_continuation'
            and (task.get('delivery_context') or {}).get('source_kind') in {'scheduled','video'})
        if runtime.task_execution_state is not None:
            runtime.committed_task_execution_revision = runtime.task_execution_state.revision
        if (replay_context or {}).get('_task_requirement_review') is not None:
            from services.task_execution.requirement_review import restore_review,verify_review_state
            from schemas.message import TextPart
            state=runtime.task_execution_state
            goal='\n'.join(p.text for p in content if isinstance(p,TextPart)).strip() or '请根据本条用户消息的附件完成任务。'
            runtime.task_requirement_review=restore_review(replay_context['_task_requirement_review'],
                root_task_id=state.root_task_id if state else claim.task_id,
                actor_user_id=task['user_id'],conversation_id=claim.conversation_id,
                source_message_id=state.source_message_ids[0] if state else claim.input_message_id,
                original_goal=state.original_goal if state else goal)
            verify_review_state(runtime.task_requirement_review,state)
        if (replay_context or {}).get('_task_plan_reviews') is not None:
            from services.task_execution.plan_facts import restore_reviews
            runtime.task_plan_reviews=restore_reviews(replay_context['_task_plan_reviews'],runtime.task_execution_state)
        from services.task_execution.user_changes import restore as restore_user_changes, capture as capture_user_change
        runtime.task_user_changes = restore_user_changes(replay_context,
            root_task_id=runtime.task_execution_state.root_task_id if runtime.task_execution_state is not None else claim.task_id,
            owner_task_id=claim.task_id, inherited_root_task_id=(
                task['delivery_context'].get('logical_task_id')
                if (task.get('delivery_context') or {}).get('input_kind') in {'agent_task_continuation','agent_task_followup'} else None))
        if runtime.task_user_changes is not None:
            runtime._steer_messages.extend(item.message for item in runtime.task_user_changes.items if not item.delivered)
        async def capture_change(event_id):
            return await capture_user_change(self._db, actor_user_id=execution_scope.actor_user_id,
                org_id=task.get('org_id'), task_id=claim.task_id, execution_token=claim.execution_token,event_id=event_id)
        runtime.task_user_change_capture = capture_change
        import copy
        runtime.checkpoint_base=copy.deepcopy(replay_context or {})
        from services.task_execution.requirement_changes import restore as restore_revision_review
        runtime.task_revision_review=restore_revision_review((replay_context or {}).get('_task_revision_review'),runtime.task_execution_state)
        async def read_pending_changes():
            state=runtime.task_execution_state
            if state is None or state.image_binding_version<3:return []
            from services.task_execution.database_scope import task_database
            reply=await task_database(self._db,state).rpc('read_agent_task_user_change_pending',{
                'p_task_id':claim.task_id,'p_execution_token':claim.execution_token,'p_logical_task_id':str(state.root_task_id)}).execute()
            if not reply or reply.data.get('outcome')!='found':raise RuntimeError('TASK_USER_CHANGE_PENDING_UNCONFIRMED')
            return reply.data['events']
        async def apply_revision(source,review,payload):
            from services.task_execution.database_scope import task_database
            from services.replay_checkpoint_store import DatabaseReplayCheckpointStore
            database=task_database(self._db,source)
            payload['safe_point']='after_tool'
            try:
                reply=await database.rpc('apply_agent_task_requirement_revision',{'p_task_id':claim.task_id,
                    'p_execution_token':claim.execution_token,'p_logical_task_id':str(source.root_task_id),
                    'p_expected_revision':source.revision,'p_review':review,'p_payload':payload}).execute()
                return reply.data
            except Exception:
                frozen=await DatabaseReplayCheckpointStore(database).read_latest(task_id=claim.task_id,execution_token=claim.execution_token)
                if frozen.get('outcome')!='found' or frozen.get('payload')!=payload:raise RuntimeError('TASK_REVISION_APPLY_UNCONFIRMED') from None
                return {'outcome':'saved','state':payload['_task_execution'],'payload':payload}
        runtime.task_user_change_pending_reader=read_pending_changes
        runtime.task_revision_apply=apply_revision
        if (replay_context or {}).get('_task_read_arguments') is not None:
            from services.task_execution.native_results import restore_arguments
            runtime.task_read_arguments=restore_arguments(replay_context['_task_read_arguments'],task_id=claim.task_id,turn_id=claim.turn_id)
        from services.task_execution.requirement_changes import refresh as refresh_user_changes
        await refresh_user_changes(runtime)
        pending_user_delivery = bool(runtime.task_unreviewed_changes or runtime.task_user_changes and any(not i.delivered for i in runtime.task_user_changes.items))
        if (not pending_user_delivery and replay_context and replay_context.get("checkpoint_kind") == "commit_ready"
                and replay_context.get("skill_runtime") is None):
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
        from services.skills.selection import SkillSelection
        raw_selection = params.pop("_selected_skill", None)
        selected_skill = SkillSelection.model_validate(raw_selection) if raw_selection is not None else None
        if not pending_user_delivery and replay_context and replay_context.get("checkpoint_kind") == "commit_ready":
            if replay_context.get("skill_runtime") is not None:
                from services.skills.runtime import create_skill_runtime
                from services.tools.runtime_context import chat_context
                await create_skill_runtime(
                    handler=handler, runtime=runtime, replay_context=replay_context,
                    context=chat_context(
                        handler, user_id=str(task["user_id"]), conversation_id=claim.conversation_id,
                        task_id=claim.task_id, permission_mode=str(params.get("permission_mode") or "auto"),
                        cancellation=cancellation_event,
                    ),
                )
                if runtime.task_execution_state is not None:
                    from services.task_execution.methods import validate_method_replay
                    await validate_method_replay(runtime.task_execution_state, runtime.skill_runtime)
            return _generation_outcome_from_replay(replay_context)
        sink = (
            self._sink_factory(task, claim, cancellation_event)
            if self._sink_factory else None
        )
        # 工具结果需要复用 ActorWebSink 的 delivery session；否则工具完成事件
        # 会绕过 delivery_seq/快照，只能等 message_done 才在页面上体现。
        handler._actor_sink = sink
        if sink is not None and replay_context is not None:
            seed_progress = getattr(sink, "seed_progress", None)
            if seed_progress is not None:
                seed_progress(
                    task.get("accumulated_content"),
                    replay_context.get("content_blocks"),
                )
        runtime.start_command_watcher()

        async def on_model_retry(model_id: str, attempt: int) -> None:
            # 仅 Actor executor 更新已持有任务的模型元数据。Gateway 不访问
            # task/token/lease；任务执行次数和 commit/fail RPC 均保持原样。
            await runtime.safe_point(SafePoint.BEFORE_MODEL)
            if cancellation_event.is_set():
                raise asyncio.CancelledError
            response = await (
                self._db.table("tasks").update({"model_id": model_id})
                .eq("id", claim.task_id)
                .eq("execution_token", claim.execution_token)
                .eq("status", "running")
                .gt("lease_expires_at", datetime.now(timezone.utc).isoformat())
                .execute()
            )
            if not response or not response.data:
                raise ConversationStopRequested("ownership_lost")
            # 通道可选的展示回调不参与模型选择或任务终态。
            notify = getattr(sink, "on_model_retry", None)
            if notify is not None:
                await notify(model_id, attempt)

        diagnostic_outcome = "failed"
        try:
            try:
                anchor = _build_anchor(claim, task.get('org_id'), task)
                from core.config import get_settings
                saved_bootstrap = (replay_context or {}).get('_task_bootstrap')
                if (runtime.task_execution_state is None and self._replay_checkpoint_store is not None
                        and ((getattr(get_settings(), 'agent_task_execution_enabled', False) and replay_context is None
                              and admits_new_task(execution_scope))
                             or saved_bootstrap is not None)):
                    from services.task_execution.bootstrap import TaskBootstrap
                    direct_policy=(saved_bootstrap.get('input',{}).get('model_directed',False)
                        if saved_bootstrap else getattr(get_settings(),'agent_task_model_directed_enabled',False))
                    runtime.task_bootstrap = TaskBootstrap.from_input(anchor=anchor, scope=execution_scope,
                        content=content, task_id=claim.task_id, conversation_id=claim.conversation_id,
                        org_id=task.get('org_id'),require_coverage_review=(
                            saved_bootstrap.get('input',{}).get('require_coverage_review',False)
                            if saved_bootstrap else not direct_policy),model_directed=direct_policy)
                    if saved_bootstrap is not None and runtime.task_bootstrap.checkpoint() != saved_bootstrap:
                        raise RuntimeError('TASK_BOOTSTRAP_REPLAY_MISMATCH')
                from services.task_execution.direct_loop import restore_plan
                restore_plan(runtime,replay_context)
                if runtime.task_bootstrap is not None or runtime.task_execution_state is not None:
                    from services.task_execution.artifact_reader import TaskArtifactReader
                    runtime.task_artifact_reader = TaskArtifactReader(self._db,
                        task_id=claim.task_id, execution_token=claim.execution_token)
                    from services.task_execution.operation_store import TaskOperationStore
                    from services.tools.catalog import build_capability_catalog
                    runtime.task_operation_store_factory = lambda: TaskOperationStore(self._db, task_id=claim.task_id,
                        execution_token=claim.execution_token, turn_id=claim.turn_id, registry=build_capability_catalog())
                result = await execute_chat(
                    handler=handler,
                    request=ChatExecutionRequest(
                        content=content,
                        user_id=str(task["user_id"]),
                        conversation_id=claim.conversation_id,
                        task_id=claim.task_id,
                        message_id=str(task["assistant_message_id"]),
                        model_id=_normalize_model_id(task.get("model_id")),
                        context_anchor=anchor,
                        params=params,
                        selected_skill=selected_skill,
                        permission_mode=str(params.get("permission_mode") or "auto"),
                        needs_google_search=bool(params.get("_needs_google_search")),
                        execution_scope=execution_scope,
                        replay_context=replay_context,
                        thinking_effort=params.get("thinking_effort"),
                        thinking_mode=params.get("thinking_mode"),
                        on_model_retry=on_model_retry,
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
            diagnostic_outcome = "generated"
            return GenerationOutcome(
                result_content=serialize_content_parts(result.parts),
                usage=result.usage,
                credits_cost=result.credits_cost,
                tool_digest=result.tool_digest,
            )
        except FollowupRestart as restart:
            diagnostic_outcome='followup'
            return await self.execute({**task,'delivery_context':restart.delivery_context},claim,cancellation_event)
        except ConversationPauseRequested:
            diagnostic_outcome = "paused"
            raise
        except asyncio.CancelledError:
            diagnostic_outcome = "cancelled"
            raise
        finally:
            finish_diagnostics = getattr(sink, "finish_diagnostics", None)
            if finish_diagnostics is not None:
                try:
                    finish_diagnostics(diagnostic_outcome)
                except Exception:
                    pass

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
            SafePoint.BEFORE_TOOL: ReplayCheckpointBoundary.BEFORE_TOOL,
            SafePoint.AFTER_TOOL: ReplayCheckpointBoundary.AFTER_TOOL,
            SafePoint.AFTER_SKILL_ACTIVATION: ReplayCheckpointBoundary.AFTER_SKILL_ACTIVATION,
            SafePoint.BEFORE_COMMIT: ReplayCheckpointBoundary.BEFORE_COMMIT,
        }.get(point)
        if boundary is None:
            return {"outcome": "ignored"}
        try:
            result = await self._replay_checkpoint_store.write(
                task_id=claim.task_id,
                execution_token=claim.execution_token,
                boundary=boundary,
                payload=dict(payload),
            )
        except Exception:
            # An acknowledgement can be lost after commit. Reconcile the whole
            # checkpoint, not merely a matching composition ID, before proceeding.
            if not any(key in payload for key in ('_skill_composition', '_task_execution', '_task_bootstrap')):
                raise
            read = getattr(self._replay_checkpoint_store, 'read_latest', None)
            if read is None:
                raise
            expected = dict(payload)
            expected.setdefault('safe_point', boundary.value)
            unconfirmed = ('TASK_EXECUTION_SAVE_UNCONFIRMED' if any(
                key in payload for key in ('_task_execution', '_task_bootstrap'))
                           else 'SKILL_COMPOSITION_SAVE_UNCONFIRMED')
            try:
                saved = await read(task_id=claim.task_id, execution_token=claim.execution_token,
                                   boundary=boundary)
            except Exception:
                raise RuntimeError(unconfirmed) from None
            if saved.get('outcome') != 'found' or saved.get('payload') != expected:
                raise RuntimeError(unconfirmed) from None
            result = {'outcome':'saved','reconciled':True}
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
    from config.model_aliases import canonical_model_id
    return canonical_model_id(str(raw))


def _build_anchor(
    claim: GenerationClaim,
    org_id: Any,
    task: dict | None = None,
) -> ContextAnchor:
    from services.handlers.chat_context.image_sources import legacy_catalog
    return ContextAnchor(
        task_id=claim.task_id,
        conversation_id=claim.conversation_id,
        turn_id=claim.turn_id,
        input_message_id=claim.input_message_id,
        base_revision=claim.base_context_revision,
        through_message_id=claim.context_through_message_id,
        org_id=str(org_id) if org_id else None,
        legacy_image_sources=tuple(legacy_catalog(task)) if task else (),
        image_source_owner_id=str(task["user_id"]) if task else None,
        image_sources_unavailable_reason=(
            ((task.get("request_params") or {}).get("_image_sources_v1") or {}).get("unavailable_reason")
            if task else None),
    )


def _create_handler(db: Any) -> Any:
    from services.handlers.chat_handler import ChatHandler

    return ChatHandler(db)


def _get_handler_db() -> Any:
    from core.database import get_db

    return get_db()
