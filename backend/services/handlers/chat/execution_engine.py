"""通道无关的 Chat 模型流与工具循环执行内核。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

from loguru import logger

from schemas.message import ContentPart, TextPart
from services.handlers.chat.execution_sink import (
    CollectingExecutionSink,
    ExecutionSink,
)
from services.handlers.chat.outcome_builder import build_content_parts
from services.handlers.chat.stream_session import StreamTotals
from services.handlers.chat.stream_setup import prepare_chat_stream
from services.handlers.chat.tool_loop import (
    append_tool_images,
    apply_tool_results,
    build_running_step,
    compact_tool_context,
    prepare_tool_turn,
)
from services.handlers.chat_tool_mixin import accumulate_tool_call_delta
from services.conversation_commands import (
    CommandType,
    ConversationCommand,
    SafePoint,
)
from services.conversation_turn_runtime import ConversationTurnRuntime
from services.conversation_state import ConversationState, ConversationStopRequested
from services.skills.runtime import (ACTIVATE_SKILL, ACTIVATE_SKILL_SCHEMA, DEACTIVATE_SKILL,
    DEACTIVATE_SKILL_SCHEMA, SKILL_CONTROL_TOOLS, control_result, READ_SKILL_ASSET, READ_SKILL_ASSET_SCHEMA)
from services.skills.selection import SkillSelection
from services.skills.feedback import skill_step
from services.handlers.chat import content_tools, tool_argument_repairs
from services.task_execution import content_objects
from services.task_execution.model_directed import SAVE_IMAGE_PLAN


@dataclass(frozen=True)
class ChatExecutionRequest:
    content: list[ContentPart]
    user_id: str
    conversation_id: str
    task_id: str
    message_id: str
    model_id: str
    context_anchor: Any
    model_request_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    selected_skill: SkillSelection | None = None
    permission_mode: str = "auto"
    needs_google_search: bool = False
    calculate_credits: bool = True
    execution_scope: Any = None
    replay_context: dict[str, Any] | None = None
    thinking_effort: str | None = None
    thinking_mode: str | None = None
    retry_context: Any = None
    on_model_retry: Callable[[str, int], Awaitable[None]] | None = None
    steer_reader: Callable[[], str | None] | None = None
    on_cancel: Callable[
        [list[dict[str, Any]], list[dict[str, Any]], str, str, str],
        Awaitable[None],
    ] | None = None


@dataclass(frozen=True)
class ChatExecutionResult:
    parts: list[ContentPart]
    content_blocks: list[dict[str, Any]]
    usage: dict[str, Any]
    credits_cost: int
    tool_digest: dict[str, Any] | None
    replay_context: dict[str, Any] | None = None
    model_id: str | None = None
    retry_context: Any = None


_REPEATED_TOOL_CALL_NUDGE = (
    "[运行时提示] 你刚刚再次请求了完全相同的工具调用。"
    "请基于已经返回的工具结果继续推理或直接给出结论；"
    "只有工具或参数发生变化时才可以继续调用工具。"
)
_REPEATED_TOOL_CALL_STOP = (
    "检测到模型连续重复相同的工具调用，已停止执行以避免重复副作用。"
    "请基于已经获得的工具结果调整后续处理。"
)


@dataclass
class _RepeatedToolCallGuard:
    """追踪连续相同的工具批次；状态可随 Actor checkpoint 恢复。"""

    _last_fingerprint: str = ""
    _consecutive_count: int = 0
    _input_revision: str | None = None

    @classmethod
    def from_replay_context(
        cls, replay_context: dict[str, Any] | None,
    ) -> "_RepeatedToolCallGuard":
        state = (replay_context or {}).get("repeated_tool_call_guard")
        if not isinstance(state, dict):
            return cls()
        fingerprint = state.get("fingerprint")
        count = state.get("consecutive_count")
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count not in {1, 2}
        ):
            return cls()
        revision = state.get('input_revision')
        return cls(fingerprint, count, revision if isinstance(revision, str) else None)

    def observe(self, calls: list[dict[str, Any]], *, input_revision: str | None = None) -> str:
        """返回 continue、nudge 或 stop；只阻止连续的同一批调用。"""
        if input_revision is not None and input_revision != self._input_revision:
            self._last_fingerprint = ""
            self._consecutive_count = 0
            self._input_revision = input_revision
        fingerprint = self._fingerprint(calls)
        if fingerprint == self._last_fingerprint:
            self._consecutive_count += 1
        else:
            self._last_fingerprint = fingerprint
            self._consecutive_count = 1
        if self._consecutive_count >= 3:
            return "stop"
        if self._consecutive_count == 2:
            return "nudge"
        return "continue"

    def replay_state(self) -> dict[str, Any]:
        if not self._last_fingerprint or self._consecutive_count < 1:
            return {}
        return {
            "fingerprint": self._last_fingerprint,
            "consecutive_count": min(self._consecutive_count, 2),
            **({"input_revision": self._input_revision} if self._input_revision is not None else {}),
        }

    @staticmethod
    def _fingerprint(calls: list[dict[str, Any]]) -> str:
        normalized_calls = []
        for call in calls:
            arguments = call.get("arguments", "")
            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError):
                arguments = str(arguments)
            # A concurrency fence is still validated at execution. Refreshing it
            # is not progress when the model keeps asking for the same results.
            from services.task_execution.direct_loop import READ_CONTROLS
            if call.get("name") in READ_CONTROLS and isinstance(arguments, dict):
                arguments = {k: v for k, v in arguments.items() if k != "expected_revision"}
            normalized_calls.append({
                "name": str(call.get("name", "")),
                "arguments": arguments,
            })
        normalized_calls.sort(key=lambda value: json.dumps(value, sort_keys=True, default=str))
        raw = json.dumps(
            normalized_calls,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def execute_chat(
    *,
    handler: Any,
    request: ChatExecutionRequest,
    cancellation_event: asyncio.Event | None = None,
    sink: ExecutionSink | None = None,
    runtime: ConversationTurnRuntime | None = None,
) -> ChatExecutionResult:
    """执行固定上下文的一次生成，不提交任务、消息或 revision 终态。"""
    event = cancellation_event or asyncio.Event()
    if runtime:
        from services.tools.mcp_checkpoint import restore_invocations
        runtime.mcp_invocations = restore_invocations(request.replay_context, runtime)
    output = sink or CollectingExecutionSink()
    if runtime:
        checkpoint = getattr(output, "flush_progress", None)
        if checkpoint is not None:
            runtime.set_checkpoint_callback(checkpoint)
    prepared = await prepare_chat_stream(
        handler=handler,
        content=request.content,
        user_id=request.user_id,
        conversation_id=request.conversation_id,
        task_id=request.task_id,
        model_id=request.model_id,
        model_request_id=request.model_request_id,
        permission_mode=request.permission_mode,
        needs_google_search=request.needs_google_search,
        params=request.params,
        context_anchor=request.context_anchor,
        replay_context=request.replay_context,
        cancellation_event=event,
        retry_context=request.retry_context,
        on_model_retry=request.on_model_retry,
    )
    if runtime is not None:
        runtime.knowledge_input_provider = None
    if runtime is not None and getattr(prepared, 'knowledge_input_facts', None) is not None:
        # Use the existing opaque checkpoint extension, including checkpoints
        # produced by task/Skill safe points outside this module.
        runtime.knowledge_input_provider = lambda: prepared.knowledge_input_facts.checkpoint(
            changed=getattr(runtime, 'knowledge_input_changed', False) is True,
            knowledge_used=(getattr(handler, '_knowledge_run', None) is not None
                            and getattr(handler._knowledge_run, 'knowledge_used', True)),
        )
    model_gateway = _get_model_gateway(prepared)
    handler._adapter = model_gateway
    previous_sink = getattr(handler, "_execution_sink", None)
    handler._execution_sink = output
    handler._pending_emit_payloads = []
    handler._pending_form_block = None
    handler._terminal_form_pending = False
    handler._terminal_image_pending = False
    handler._pending_change_set_blocks = []
    handler._terminal_change_set_pending = False
    handler._tool_result_stop_reason = ""
    handler._request_response_stop = False
    # 保留 T3 的跨 Task 关联出口；模型 retry 已在 Gateway 内执行。
    handler._last_model_attempt_context = None
    totals = StreamTotals()
    from services.task_execution.replay import restore_task_usage
    usage=restore_task_usage(request.replay_context,task_id=request.task_id)
    if usage is not None:totals.usage=usage
    blocks: list[dict[str, Any]] = _initial_replay_blocks(
        request.replay_context,
    )
    repeated_tool_call_guard = _RepeatedToolCallGuard.from_replay_context(
        request.replay_context,
    )
    try:
        # Start the existing delivery stream before Skill/workflow IO. This only
        # acknowledges execution; activation success is reported separately.
        await output.start()
        skills = None
        selection_error = None
        from services.agent.image.ecommerce_planner.workflow import EcommerceWorkflow
        prepared.ecom_workflow = None
        if runtime is not None:
            saved_skill_state = (request.replay_context or {}).get('skill_runtime')
            runtime.skill_coordination = not request.replay_context or (
                isinstance(saved_skill_state, dict) and saved_skill_state.get('version') == 2)
            runtime.committed_composition_revision = ((saved_skill_state or {}).get('composition') or {}).get('revision', 0)
        planning_entry = (prepared.execution_context is not None and prepared.execution_context.feature_flags.get("ecom_image_planning_enabled") is True
                          and prepared.execution_context.context_scope == 'user')
        pending_media = False
        if runtime is not None:
            from services.task_execution.direct_loop import content_followup_state
            pending_state = runtime.task_execution_state or content_followup_state(runtime)
            groups = content_objects.catalog(pending_state,for_model=True) if pending_state else getattr(runtime,'task_content_groups',())
            pending_media = any(g.get('confirmation_tool') and any(i.get('awaiting_confirmation') for i in g.get('items',())) for g in groups)
        from services.task_execution.request_resolution import context as request_context
        if (runtime is not None and hasattr(handler, "db") and prepared.execution_context is not None
                and (planning_entry or pending_media or request_context(runtime) is not None)
                and prepared.execution_context.execution_mode == "interactive"):
            from services.agent.image.ecommerce_planner.workflow import KEY as ECOM_WORKFLOW_KEY, SKILL as SERVER_PLANNER
            workflow = EcommerceWorkflow(handler, request, runtime, prepared)
            workflow.allow_server_planning = planning_entry
            if (runtime.skill_coordination or (runtime.task_bootstrap is None and runtime.task_execution_state is None)
                    or request.params.get(ECOM_WORKFLOW_KEY)
                    or (request.selected_skill and request.selected_skill.skill_id == SERVER_PLANNER)
                    or (not request.replay_context and content_tools.selects_previous_item(request, runtime))):
                prepared.ecom_workflow = workflow
                await workflow.initialize()
                handler._ecom_workflow_pin = workflow.value.skill if workflow.requires_server_binding() else None
            else:
                prepared.ecom_workflow_candidate = workflow
        if runtime is None and '_skill_intent' in request.params:
            from services.skills.retry import parse_intent
            from services.skills.runtime import SkillBindingError
            if parse_intent(request.params['_skill_intent']).required:
                raise SkillBindingError('当前聊天执行入口无法加载原任务 Skill，请检查设置后重试。')
        if runtime is not None or (request.replay_context or {}).get("skill_runtime") is not None:
            from services.skills.runtime import create_skill_runtime, SkillReplayError, SkillBindingError
            try:
                skills = await create_skill_runtime(
                    handler=handler, context=prepared.execution_context, runtime=runtime,
                    replay_context=request.replay_context, selection=request.selected_skill,
                    **({'intent': request.params['_skill_intent'], 'retry': request.params.get('_skill_retry') is True}
                       if '_skill_intent' in request.params else {}),
                )
            except (SkillReplayError, SkillBindingError):
                raise
            except Exception:
                if request.selected_skill is None or request.replay_context:
                    raise
                selection_error = control_result("SKILL_LOAD_UNAVAILABLE")
            if skills is not None:
                _apply_skill_context(prepared, skills)
        if runtime is not None and runtime.task_execution_state is not None:
            from services.task_execution.methods import validate_method_replay
            await validate_method_replay(runtime.task_execution_state, skills)
        if runtime is not None and (runtime.task_bootstrap is not None or runtime.task_execution_state is not None):
            from services.task_execution.image_resources import create_resolver
            runtime.task_image_resource_resolver=create_resolver(handler,request,prepared.execution_context)
            from services.task_execution.plan_fact_sources import create_source_provider
            runtime.task_plan_source_provider=create_source_provider(handler,request,prepared.execution_context)
            async def result_context():
                from services.tools.runtime_context import refresh_context
                from services.tools.catalog import build_capability_catalog
                from services.task_execution.contracts import TaskError
                executor=getattr(handler,'_tool_executor',None)
                if executor is None:
                    # A receipt continuation can precede the first business
                    # call in this Actor. Revalidate through the same identity
                    # and resource boundary; do not construct/dispatch a tool.
                    from types import SimpleNamespace
                    executor=SimpleNamespace(db=handler.db,execution_mode=prepared.execution_context.execution_mode,
                        execution_scope=getattr(handler,'execution_scope',None),
                        channel_scope_id=getattr(getattr(handler,'execution_scope',None),'channel_scope_id',None),
                        knowledge_run=getattr(handler,'_knowledge_run',None),
                        resource_manifest_loader=getattr(handler,'_tool_resource_manifest_loader',None),
                        resource_manifest=getattr(handler,'_resource_manifest',None),
                        resource_access_boundary=getattr(handler,'_resource_access_boundary',None))
                return await refresh_context(executor,prepared.execution_context,build_capability_catalog())
            runtime.task_result_context_provider=result_context
        completed_phase_ids = set()
        if runtime is not None and runtime.task_execution_state is not None:
            from services.task_execution.methods import completed_phase_bindings
            completed_phase_ids = {binding.skill_key for binding in completed_phase_bindings(runtime.task_execution_state)}
            if skills is not None:
                completed_phase_ids.update(skills.resident)
        workflow = getattr(prepared, "ecom_workflow", None)
        if workflow and not (skills and (skills.manual_skill_id == "ecommerce-main-images"
                or "ecommerce-main-images" in skills.session_skill_ids)):
            question = workflow.entry_question()
            if question and not any(b.get("ecom_entry_question") is True for b in blocks):
                block = {"type": "text", "text": question, "ecom_entry_question": True}
                blocks.append(block)
                await output.on_block(block)
        if skills is not None and not request.replay_context:
            if prepared.ecom_workflow:
                # Pin the action before a pause can freeze the initial empty set.
                await prepared.ecom_workflow.coordinate(skills)
            # Freeze session configuration before the first pause/cancel
            # boundary, including the empty binding set. Resume never rereads it.
            await runtime.safe_point(
                SafePoint.BEFORE_MODEL,
                replay_payload=_build_replay_context(
                    prepared.messages, blocks, prepared.budget.turns_used,
                    next_model_round=0,
                    repeated_tool_call_guard=repeated_tool_call_guard,
                ),
            )
        if skills is not None:
            # Freeze configuration before loading bodies; activate the complete
            # locked set together, including when resuming its initial snapshot.
            manual_finished = any(b.get('step_id') == 'manual-skill' for b in blocks)
            if skills.checkpoint_version == 2 and not manual_finished:
                required = await skills.activate_required(**(
                    {'completed_phase_ids': completed_phase_ids} if completed_phase_ids else {}))
                if not required['ok']:
                    reason = skill_step(required, skills, step_id='required-skills')['reason']
                    if skills.session_skill_ids:
                        raise SkillBindingError('会话固定的 Skill 无法启用：' + reason)
                    if required.get('code') in {'SKILL_WORKFLOW_CONFLICT', 'SKILL_DEPENDENCY_UNAVAILABLE'}:
                        raise SkillBindingError(reason)
                    # Preserve the existing safe manual-selection feedback. A
                    # failed load must not be retried or silently called active.
                    selection_error = required
                if prepared.ecom_workflow:
                    await prepared.ecom_workflow.coordinate(skills)
                _apply_skill_context(prepared, skills)
            for skill_id in skills.session_skill_ids:
                if skill_id in completed_phase_ids:
                    continue
                step_id = "session-skill:" + skill_id
                if any(b.get('step_id') == step_id for b in blocks):
                    continue
                await runtime.safe_point(SafePoint.BEFORE_TOOL)
                await _check_cancelled(event, request, prepared.messages, blocks, totals, "before_skill")
                result = await skills.activate_session(skill_id)
                block = skill_step(result, skills, step_id=step_id)
                if not result.get("ok"):
                    # Never continue without the bound restriction. No model or
                    # business IO has started. Do not checkpoint partial failure.
                    raise SkillBindingError("会话固定的 Skill 无法启用：" + block["reason"])
                blocks.append(block)
                _apply_skill_context(prepared, skills)
                await runtime.safe_point(
                    SafePoint.AFTER_SKILL_ACTIVATION,
                    replay_payload=_build_replay_context(
                        prepared.messages, blocks, prepared.budget.turns_used,
                        next_model_round=_initial_model_round(request.replay_context),
                        repeated_tool_call_guard=repeated_tool_call_guard,
                    ),
                )
                await output.on_block(block)
        if request.selected_skill is not None and request.selected_skill.skill_id not in completed_phase_ids and not any(
            b.get("type") == "skill_step" and b.get("step_id") == "manual-skill" for b in blocks
        ):
            if runtime:
                await runtime.safe_point(SafePoint.BEFORE_TOOL)
            await _check_cancelled(event, request, prepared.messages, blocks, totals, "before_skill")
            result = selection_error or control_result("SKILL_RUNTIME_DISABLED")
            if skills is not None and selection_error is None:
                result = await skills.activate_manual(request.selected_skill)
            block = skill_step(result, skills, step_id="manual-skill")
            blocks.append(block)
            if skills is not None:
                _apply_skill_context(prepared, skills)
            if not result.get('ok'):
                boundary = next((i for i, message in enumerate(prepared.messages)
                                 if message.get('role') != 'system'), len(prepared.messages))
                prepared.messages.insert(boundary, {'role': 'system', 'content': (
                    '用户本轮指定的 Skill 未能启用，原因：' + block['reason']
                    + ' 不得声称按该 Skill 或其附件完成任务。若当前请求依赖此 Skill，'
                    '说明需要重新选择或修复；仅在请求可独立完成时按普通聊天处理。'
                )})
            if runtime:
                await runtime.safe_point(
                    SafePoint.AFTER_SKILL_ACTIVATION,
                    replay_payload=_build_replay_context(
                        prepared.messages, blocks, prepared.budget.turns_used,
                        next_model_round=_initial_model_round(request.replay_context),
                        repeated_tool_call_guard=repeated_tool_call_guard,
                    ),
                )
            await output.on_block(block)
        workflow = getattr(prepared, "ecom_workflow", None)
        if workflow and workflow.requires_server_binding():
            if skills is None:
                raise SkillBindingError("原主图工作流的 Skill 当前不可用，已停止执行。")
            if workflow.server_skill_key() is None:
                await runtime.safe_point(SafePoint.BEFORE_TOOL)
                await _check_cancelled(event, request, prepared.messages, blocks, totals, "before_workflow_skill")
                key = workflow.value.skill.get("skill_key", "ecommerce-main-images")
                result = await skills.activate(json.dumps({"skill_id": key}))
                if not result.get("ok"):
                    raise SkillBindingError("主图入口 Skill 无法启用，已停止执行：" + result.get("code", "SKILL_UNAVAILABLE"))
                block = skill_step(result, skills, step_id="workflow-skill")
                blocks.append(block)
                await output.on_block(block)
                _apply_skill_context(prepared, skills)
                await runtime.safe_point(SafePoint.AFTER_SKILL_ACTIVATION, replay_payload=_build_replay_context(
                    prepared.messages, blocks, prepared.budget.turns_used,
                    next_model_round=_initial_model_round(request.replay_context),
                    repeated_tool_call_guard=repeated_tool_call_guard,
                ))
        if workflow:
            await workflow.activated()
        finalized_parts = None

        async def finalize_output(form_hint=None):
            nonlocal finalized_parts
            if finalized_parts is None:
                finalized_parts = await _finalize_output_parts(handler=handler, prepared=prepared,
                    totals=totals, blocks=blocks, sink=output, cancellation_event=event,
                    runtime=runtime, form_hint=form_hint)
            return finalized_parts

        form_hint = await _run_loop(
            handler=handler,
            request=request,
            prepared=prepared,
            cancellation_event=event,
            sink=output,
            totals=totals,
            blocks=blocks,
            runtime=runtime,
            model_round=_initial_model_round(request.replay_context),
            repeated_tool_call_guard=repeated_tool_call_guard,
            finalize_task=finalize_output,
        )
        parts = await finalize_output(form_hint)
        await output.flush()
        return ChatExecutionResult(
            model_id=getattr(model_gateway, "model_id", request.model_id),
            retry_context=getattr(model_gateway, "retry_context", None),
            parts=parts,
            content_blocks=blocks,
            usage=totals.usage,
            credits_cost=(
                handler._calculate_credits(totals.usage)
                if request.calculate_credits else 0
            ),
            tool_digest=_build_digest(
                prepared.messages,
                request.conversation_id,
                prepared.budget.turns_used,
            ) if parts or getattr(runtime, 'task_image_completion', None) is None else None,
            replay_context=_build_replay_context(
                prepared.messages,
                blocks,
                prepared.budget.turns_used,
                repeated_tool_call_guard=repeated_tool_call_guard,
                skill_runtime=runtime.skill_runtime if runtime else None,
            ),
        )
    except BaseException as error:
        handler._last_model_attempt_context = getattr(
            model_gateway,
            "last_attempt_context",
            None,
        )
        from services.model_gateway import ModelGatewayError

        if isinstance(error, ModelGatewayError) and error.result.partial_output:
            # 错误终态仍由 Web/Actor 原机制负责；先保存不足一个批次的 partial。
            flush_progress = getattr(output, "flush_progress", None)
            if flush_progress is not None:
                await flush_progress()
        raise
    finally:
        candidates = getattr(prepared, 'knowledge_candidates', None)
        if candidates is not None:
            await candidates.close()
            if getattr(handler, '_knowledge_candidates', None) is candidates:
                handler._knowledge_candidates = None
        from services.knowledge_workspace.chat import KnowledgeRun
        knowledge_run=getattr(handler,'_knowledge_run',None)
        if isinstance(knowledge_run,KnowledgeRun):
            from services.knowledge_workspace.materialize import cleanup
            try:
                await asyncio.to_thread(cleanup,knowledge_run)
            except Exception:
                logger.warning("knowledge input cleanup failed")
        if getattr(handler, "_tool_executor_scope", None) == (request.task_id, request.conversation_id, request.user_id, handler.org_id):
            handler._tool_executor = None
            handler._tool_executor_scope = None
        await model_gateway.close()
        if getattr(handler, "_adapter", None) is model_gateway:
            handler._adapter = None
        if getattr(handler, "_execution_sink", None) is output:
            handler._execution_sink = previous_sink


async def _finalize_output_parts(*, handler, prepared, totals, blocks, sink,
                                 cancellation_event, runtime, form_hint=None):
    """Canonical output finalization, shared by normal return and logical commit."""
    from services.knowledge_workspace.chat import KnowledgeRun
    knowledge_run = getattr(handler, '_knowledge_run', None)
    if isinstance(knowledge_run, KnowledgeRun):
        # Budget synthesis also receives retained source messages.
        await asyncio.to_thread(knowledge_run.revalidate)
    # New tasks already saved an explicit bounded partial result; do not invoke
    # an extra uncounted synthesis model after their task budget is exhausted.
    if not any(block.get('content_delivery_final') is True for block in blocks) and (
        runtime is None or (runtime.task_execution_state is None and getattr(runtime,'task_requirement_review',None) is None)
    ):
        await _apply_budget_stop(prepared, totals, blocks, sink,
            cancellation_event=cancellation_event, runtime=runtime)
    await _consume_emit_payloads(handler, blocks, sink)
    if (getattr(runtime, 'task_image_completion', None) is not None
            and runtime.task_execution_state.status == 'completed'
            and not blocks and not totals.text and not totals.thinking and not form_hint):
        return []
    parts = build_content_parts(blocks, fallback_text=totals.text, fallback_thinking=totals.thinking)
    if isinstance(knowledge_run, KnowledgeRun):
        for part in parts:
            if isinstance(part, TextPart):
                part.text = await asyncio.to_thread(knowledge_run.normalize_citations, part.text)
        totals.text = await asyncio.to_thread(knowledge_run.normalize_citations, totals.text)
        for block in blocks:
            if block.get('type') == 'text' and isinstance(block.get('text'), str):
                block['text'] = await asyncio.to_thread(knowledge_run.normalize_citations, block['text'])
        footer = await asyncio.to_thread(knowledge_run.evidence_footer)
        if footer:
            parts.append(TextPart(text=footer))
            totals.text += footer
            if blocks:
                evidence = {'type': 'text', 'text': footer}
                blocks.append(evidence)
                await sink.on_block(evidence)
            else:
                await sink.on_text(footer)
    if form_hint:
        parts.append(TextPart(text=form_hint))
    return parts


async def _run_loop(
    *,
    handler: Any,
    request: ChatExecutionRequest,
    prepared: Any,
    cancellation_event: asyncio.Event,
    sink: ExecutionSink,
    totals: StreamTotals,
    blocks: list[dict[str, Any]],
    runtime: ConversationTurnRuntime | None,
    model_round: int = 0,
    repeated_tool_call_guard: _RepeatedToolCallGuard | None = None,
    finalize_task=None,
) -> str | None:
    from services.task_execution.model_directed import enabled as direct_enabled
    if direct_enabled(runtime):
        from services.task_execution.inline_image_action import read_record
        read_record(runtime)
        if any(block.get('content_delivery_final') is True for block in blocks):
            return None
        stopped = content_tools.checkpoint_state(runtime).get('stop_message')
        if stopped:
            blocks.append({'type': 'text', 'text': stopped})
            totals.text += stopped
            await sink.on_block(blocks[-1])
            return None
    if runtime and runtime.task_execution_state is not None:
        from services.task_execution.user_changes import activate_for_delivery
        from services.task_execution.requirement_changes import refresh as refresh_user_changes
        await refresh_user_changes(runtime)
        candidate = activate_for_delivery(runtime.task_execution_state, getattr(runtime,'task_user_changes',None),unreviewed=bool(runtime.task_unreviewed_changes))
        if candidate is not runtime.task_execution_state:
            from services.task_execution.model_bridge import commit_projection
            await commit_projection(runtime=runtime,candidate=candidate,prepared=prepared,blocks=blocks,
                point=SafePoint.BEFORE_MODEL,build_checkpoint=_build_replay_context,
                turn=prepared.budget.turns_used,next_model_round=model_round,
                repeated_tool_call_guard=repeated_tool_call_guard)
    from .native_plan_delivery import deliver as deliver_native_plan
    if await deliver_native_plan(runtime,request,blocks,sink,totals):
        await runtime.safe_point(SafePoint.AFTER_TOOL,replay_payload=_build_replay_context(
            prepared.messages,blocks,prepared.budget.turns_used,next_model_round=model_round,
            repeated_tool_call_guard=repeated_tool_call_guard))
        return None
    if runtime and runtime.task_execution_state is not None and runtime.task_execution_state.status != 'active':
        # A crash may occur after logical completion was saved and before the
        # executor's commit_ready save. Reuse the saved blocks without sampling.
        return None
    if runtime and (runtime.task_execution_state is not None or runtime.task_bootstrap is not None):
        runtime.task_usage=totals.usage
    async def terminal_task_result():
        from schemas.message import serialize_content_parts
        parts = (await finalize_task() if finalize_task is not None else build_content_parts(
            blocks, fallback_text=totals.text, fallback_thinking=totals.thinking))
        return {'checkpoint_kind': 'commit_ready',
                'result_content': serialize_content_parts(parts),
                'usage': dict(totals.usage),
                'credits_cost': handler._calculate_credits(totals.usage) if request.calculate_credits else 0,
                'tool_digest': _build_digest(prepared.messages, request.conversation_id, prepared.budget.turns_used)
                    if parts or getattr(runtime, 'task_image_completion', None) is None else None}
    async def finish_inline_receipt(turn_index, next_round):
        from services.task_execution import request_resolution, inline_image_action
        from services.task_execution.content_delivery import input_revision
        from services.task_execution.model_bridge import review_model_completion
        if (request_resolution.context(runtime) is None
                or content_tools.requires_continued_delivery(request, runtime)):
            return False
        target = request_resolution.binding(runtime)
        state = runtime.task_execution_state
        record = inline_image_action.read_record(runtime)
        if (not target or target['input_revision'] != input_revision(runtime, request)
                or state is None):
            return False
        from services.task_execution.reducer import digest
        single_saved = target['target']['kind'] == 'content' and len(target['target']['item_indices']) == 1
        calls = [b for b in state.call_bindings if b.tool_name == 'generate_image'
                 and b.input_sha256 == digest(sorted(getattr(runtime, 'task_direct_seen', ())))
                 and (record is not None and b.call_id == record.call_id or single_saved)]
        accepted = [op for op in state.operations if op.step_id in {b.step_id for b in calls} and op.status == 'accepted'
                    and str(op.owner_task_id) == target['execution_task_id'] and op.receipt_ref]
        if len(accepted) != 1:
            return False
        status_key = accepted[0].id
        if not any(block.get('image_receipt_status') == status_key for block in blocks):
            notice = {'type': 'text', 'text': request_resolution.image_status_reply(runtime, request),
                      'image_receipt_status': status_key}
            blocks.append(notice); totals.text += notice['text']
            await sink.on_block(notice)
        await review_model_completion(runtime=runtime, prepared=prepared, blocks=blocks,
            build_checkpoint=_build_replay_context, turn=turn_index, next_model_round=next_round,
            repeated_tool_call_guard=repeated_tool_call_guard, sink=sink, terminal_result=terminal_task_result)
        return True
    thinking_mode = request.thinking_mode
    entry_question = next((b for b in blocks if b.get("ecom_entry_question") is True), None)
    if entry_question:
        totals.text += entry_question["text"]
        return None
    empty_output_retried = False
    from services.task_execution.content_delivery import input_revision as request_input_revision
    response = getattr(runtime, 'checkpoint_base', {}).get('_request_response_v1')
    if response and response['input_revision'] == request_input_revision(runtime, request):
        if not any(block.get('request_response_final') for block in blocks):
            notice = {'type': 'text', 'text': response.get('rendered_text', response['text'] + '\n\n本轮未提交新的图片任务。'),
                      'request_response_final': True}
            blocks.append(notice)
            totals.text += notice['text']
            await sink.on_block(notice)
        return None
    repeated_tool_call_guard = repeated_tool_call_guard or _RepeatedToolCallGuard()
    from services.handlers.chat.image_argument_correction import ImageArgumentCorrection
    image_arguments = ImageArgumentCorrection(blocks)
    prepared.image_arguments = image_arguments
    from services.agent.image.ecommerce_planner.arguments import PlannerArgumentCorrection
    prepared.ecom_arguments = PlannerArgumentCorrection(blocks)
    repair_checkpoint = tool_argument_repairs.read(runtime) if runtime is not None else None
    if runtime and (runtime.task_execution_state is not None or repair_checkpoint):
        from services.task_execution.pending_batch import recover_pending_batch
        await recover_pending_batch(prepared=prepared, runtime=runtime, request=request, handler=handler,
            sink=sink, totals=totals, blocks=blocks, cancellation_event=cancellation_event,
            model_round=model_round, execute_tools=_execute_tools, repeated_tool_call_guard=repeated_tool_call_guard)
        if await finish_inline_receipt(prepared.budget.turns_used, model_round):
            return None
        if runtime.task_execution_state is not None and runtime.task_execution_state.status != 'active':
            return None
    while not prepared.budget.stop_reason:
        correcting = image_arguments.start_round(model_round)
        prepared.ecom_arguments.before_model()
        if prepared.ecom_arguments.stop_message:
            blocks.append({"type": "text", "text": prepared.ecom_arguments.stop_message})
            totals.text += prepared.ecom_arguments.stop_message
            await sink.on_block(blocks[-1])
            return None
        workflow = getattr(prepared, "ecom_workflow", None)
        prepared.defer_ecom_narrative = False
        if workflow:
            if (runtime is not None and runtime.skill_runtime is not None and hasattr(workflow, 'coordinate')
                    and not getattr(workflow, '_coordinated', False)):
                await workflow.coordinate(runtime.skill_runtime)
            prepared.defer_ecom_narrative = await workflow.needs_planner(blocks)
            message = workflow.context_message()
            prepared.messages[:] = [m for m in prepared.messages if not (
                m.get("role") == "system" and isinstance(m.get("content"), str)
                and m["content"].startswith("[Server main-image workflow]"))]
            if message:
                prepared.messages.append(message)
        if image_arguments.stop_message:
            totals.usage["image_argument_metrics"] = image_arguments.summary()
            blocks.append({"type": "text", "text": image_arguments.stop_message})
            totals.text += image_arguments.stop_message
            await sink.on_block(blocks[-1])
            return None
        if runtime:
            if runtime.skill_runtime is not None:
                _apply_skill_context(prepared, runtime.skill_runtime)
            from services.task_execution.model_bridge import controller_for
            task_controller = controller_for(runtime)
            from services.task_execution.model_directed import enabled as direct_enabled
            if direct_enabled(runtime):
                from services.task_execution.direct_loop import context_message as direct_context
                prepared.messages[:]=[m for m in prepared.messages if not (
                    m.get('role')=='system' and isinstance(m.get('content'),str)
                    and m['content'].startswith('[Model-directed execution]'))]
                prepared.messages.append(direct_context(runtime))
            if task_controller is None and runtime.task_bootstrap is not None and not direct_enabled(runtime):
                instruction = {'role': 'system', 'content': '[Task initialization]\n'
                    '先理解用户完整目标。需要多阶段或多项交付时先调用start_task_execution整理全部要求，'
                    '再规划执行；不要因商品词自动切换到另一方法。用户已选择的方法优先完成其适用阶段。'
                    '单步问答或查询直接处理，无需为使用编排而额外规划。'}
                if instruction not in prepared.messages:
                    prepared.messages.append(instruction)
            if task_controller is not None:
                if runtime.task_execution_state.model_rounds_used >= runtime.task_execution_state.model_round_limit:
                    from services.task_execution.model_bridge import review_model_completion
                    await review_model_completion(runtime=runtime, prepared=prepared, blocks=blocks,
                        build_checkpoint=_build_replay_context, turn=prepared.budget.turns_used,
                        next_model_round=model_round, repeated_tool_call_guard=repeated_tool_call_guard,
                        exhausted=True, sink=sink, terminal_result=terminal_task_result)
                    return None
                from services.task_execution.model_bridge import model_round_candidate
                task_candidate = model_round_candidate(runtime)
                prepared.messages[:] = [m for m in prepared.messages if not (
                    m.get('role') == 'system' and isinstance(m.get('content'), str)
                    and m['content'].startswith('[Current task execution facts]'))]
                if not direct_enabled(runtime):
                    prepared.messages.append(controller_for(runtime, state=task_candidate).context_message())
                else:
                    prepared.messages[:]=[m for m in prepared.messages if not (
                        m.get('role')=='system' and isinstance(m.get('content'),str)
                        and m['content'].startswith('[Model-directed execution]'))]
                    prepared.messages.append(direct_context(runtime,state=task_candidate))
            runtime.set_state(ConversationState.RUNNING_MODEL)
            if task_controller is not None:
                from services.task_execution.model_bridge import commit_projection
                await commit_projection(runtime=runtime, candidate=task_candidate,
                    prepared=prepared, blocks=blocks, point=SafePoint.BEFORE_MODEL,
                    build_checkpoint=_build_replay_context, turn=prepared.budget.turns_used,
                    next_model_round=model_round, repeated_tool_call_guard=repeated_tool_call_guard)
            else:
                await runtime.safe_point(
                    SafePoint.BEFORE_MODEL,
                    replay_payload=_build_replay_context(
                        prepared.messages,
                        blocks,
                        prepared.budget.turns_used,
                        next_model_round=model_round,
                        repeated_tool_call_guard=repeated_tool_call_guard,
                    ),
                )
            _inject_subtask_completions(
                prepared.messages,
                runtime.consume_subtask_completions(),
            )
            had_steers = bool(getattr(runtime, '_steer_messages', ()))
            await _inject_runtime_steers(runtime, prepared)
            if had_steers:
                await runtime.safe_point(SafePoint.BEFORE_MODEL, replay_payload=_build_replay_context(
                    prepared.messages, blocks, prepared.budget.turns_used, next_model_round=model_round,
                    repeated_tool_call_guard=repeated_tool_call_guard))
        await _refresh_knowledge_after_steer(handler, request, prepared)
        await _check_cancelled(
            cancellation_event, request, prepared.messages, blocks,
            totals, "loop_top",
        )
        from services.task_execution.requirement_changes import refresh as refresh_user_changes
        if runtime is not None:
            await refresh_user_changes(runtime)
            from services.task_execution.followup import settle_images
            await settle_images(runtime,prepared,blocks,turn=prepared.budget.turns_used,model_round=model_round,build_checkpoint=_build_replay_context)
            from services.task_execution.model_directed import enabled as direct_enabled
            if direct_enabled(runtime) and getattr(runtime,'task_native_event_pending',False):
                from services.task_execution.direct_loop import advance_results
                from services.task_execution.model_bridge import commit_projection
                candidate=await advance_results(runtime,prepared,reconcile_pending=True)
                if candidate is not runtime.task_execution_state:
                    await commit_projection(runtime=runtime,candidate=candidate,prepared=prepared,blocks=blocks,
                        point=SafePoint.BEFORE_MODEL,build_checkpoint=_build_replay_context,turn=prepared.budget.turns_used,
                        next_model_round=model_round,repeated_tool_call_guard=repeated_tool_call_guard)
                runtime.task_native_event_pending=False
        if runtime is not None and tool_argument_repairs.stopped(runtime, request):
            notice=tool_argument_repairs.stop_message(runtime)
            blocks.append({'type': 'text', 'text': notice})
            totals.text += notice
            await sink.on_block(blocks[-1])
            return None
        if direct_enabled(runtime) and content_objects.save_repair_exhausted(prepared.messages):
            break
        prepared.budget.use_turn()
        turn = prepared.budget.turns_used - 1
        followup=getattr(runtime,'task_followup_candidate',None) if runtime else None
        if followup is not None and runtime.task_execution_state is None and followup.get('state',{}).get('model_directed') is not True:
            import copy
            from services.task_execution.followup import review_messages as followup_messages,apply as apply_followup
            isolated=copy.copy(prepared);isolated.messages=followup_messages(followup)
            text,_thinking,review_calls,_preview=await _read_turn(isolated,[],cancellation_event,CollectingExecutionSink(),
                StreamTotals(usage=totals.usage),[],runtime,request=request,model_round=model_round,task_review=True)
            model_round+=1
            await apply_followup(runtime,prepared,blocks,text,bool(review_calls),model_round=model_round,turn=turn,build_checkpoint=_build_replay_context)
            continue
        from services.task_execution.requirement_review import RequirementReview,review_messages,apply_review,reusable_observation
        requirement_review = getattr(runtime,'task_requirement_review',None) if runtime else None
        if isinstance(requirement_review,RequirementReview) and requirement_review.pending is not None:
            cached = reusable_observation(requirement_review, prepared.messages)
            if cached is not None:
                await apply_review(runtime=runtime,prepared=prepared,blocks=blocks,
                    text=cached.observation.model_dump_json(),has_tool_calls=False,
                    model_round=cached.model_round,next_model_round=model_round,
                    build_checkpoint=_build_replay_context,turn=turn,sink=sink)
                continue
            import copy
            isolated=copy.copy(prepared)
            isolated.messages=review_messages(requirement_review)
            review_totals=StreamTotals(usage=totals.usage)
            text,_thinking,review_calls,_preview=await _read_turn(
                isolated,[],cancellation_event,CollectingExecutionSink(),review_totals,[],runtime,
                request=request,model_round=model_round,task_review=True)
            model_round+=1
            await apply_review(runtime=runtime,prepared=prepared,blocks=blocks,text=text,
                has_tool_calls=bool(review_calls),model_round=model_round-1,next_model_round=model_round,
                build_checkpoint=_build_replay_context,turn=turn,sink=sink)
            continue
        from services.task_execution.requirement_review import clarification_question
        question=clarification_question(requirement_review,prepared.messages)
        if question:
            notice={'type':'text','text':question,'task_status':'waiting_input'}
            blocks.append(notice)
            await sink.on_block(notice)
            return None
        if (isinstance(requirement_review,RequirementReview) and not requirement_review.approved_proposal_sha256
                and len(requirement_review.attempts)>=3):
            notice={'type':'text','text':'任务交付要求尚未核对完整，已保留原请求与核对记录，未启动外部执行。',
                'task_status':'partial'}
            blocks.append(notice)
            await sink.on_block(notice)
            return None
        from services.task_execution.requirement_changes import pending as revision_pending,apply_review as apply_revision_review
        if revision_pending(runtime):
            import copy
            from services.task_execution.requirement_changes import review_messages as revision_messages
            isolated=copy.copy(prepared)
            isolated.messages=revision_messages(runtime.task_execution_state,runtime.task_revision_review)
            text,_thinking,review_calls,_preview=await _read_turn(isolated,[],cancellation_event,
                CollectingExecutionSink(),StreamTotals(usage=totals.usage),[],runtime,request=request,
                model_round=model_round,task_review=True)
            model_round+=1
            await apply_revision_review(runtime=runtime,prepared=prepared,blocks=blocks,text=text,has_tool_calls=bool(review_calls),
                model_round=model_round-1,next_model_round=model_round,build_checkpoint=_build_replay_context,turn=turn)
            continue
        from services.task_execution.requirement_changes import exhausted as revision_exhausted
        if revision_exhausted(runtime):
            from services.task_execution.model_bridge import review_model_completion
            await review_model_completion(runtime=runtime,prepared=prepared,blocks=blocks,
                build_checkpoint=_build_replay_context,turn=turn,next_model_round=model_round,
                exhausted=True,sink=sink)
            return None
        from services.task_execution.plan_facts import pending_review
        if pending_review(runtime):
            import copy
            from services.task_execution.plan_facts import review_messages as plan_review_messages,apply_review as apply_plan_review
            pending=runtime.task_plan_reviews.pending
            source_error=None;text='';review_calls=[]
            try:
                _sources,pictures=await runtime.task_plan_source_provider(snapshot=pending.sources,images=True)
            except (ValueError,PermissionError,OSError) as error:
                source_error=type(error).__name__
            if source_error is None:
                isolated=copy.copy(prepared)
                previous_attempt=next((a for a in reversed(runtime.task_plan_reviews.attempts)
                    if a.request.artifact_id==pending.artifact_id and a.request.artifact_sha256==pending.artifact_sha256
                    and a.request.sources.get('sha256')==pending.sources.get('sha256')),None)
                isolated.messages=plan_review_messages(runtime.task_execution_state,pending,pictures,
                    repair_issues=previous_attempt.issues if previous_attempt and previous_attempt.status=='uncertain' else ())
                text,_thinking,review_calls,_preview=await _read_turn(
                    isolated,[],cancellation_event,CollectingExecutionSink(),StreamTotals(usage=totals.usage),[],runtime,
                    request=request,model_round=model_round,task_review='plan_facts')
            model_round+=1
            await apply_plan_review(runtime=runtime,prepared=prepared,blocks=blocks,text=text,
                has_tool_calls=bool(review_calls),model_round=model_round-1,next_model_round=model_round,
                build_checkpoint=_build_replay_context,turn=turn,sink=sink,repeated_tool_call_guard=repeated_tool_call_guard,
                source_error=source_error)
            continue
        if getattr(handler, '_knowledge_run', None) is not None and prepared.execution_context is not None:
            # Access to retained source content is still checked before sending
            # it to the model; this is not an answer review or display policy.
            await asyncio.to_thread(handler._knowledge_run.revalidate)
            from dataclasses import replace
            prepared.execution_context = replace(prepared.execution_context, feature_flags={
                **prepared.execution_context.feature_flags, 'knowledge_refs_present': True})
        discovered_names = set(prepared.tool_context.discovered_tools)
        if runtime:
            discovered_names.update(content_tools.checkpoint_state(runtime).get("names", ()))
            if (content_tools.enabled(runtime, prepared.execution_context)
                    and not runtime.checkpoint_base.get('_continuation_input')):
                discovered_names.update(content_tools.requested_delivery_tools(request, runtime))
                from services.task_execution import item_confirmation
                media = item_confirmation.directive(request, runtime)
                confirmed_indices = media.approved if media.confirmation_only else []
                if any(index is not None for index in confirmed_indices):
                    from services.task_execution.direct_loop import content_followup_state
                    state = runtime.task_execution_state or content_followup_state(runtime)
                    discovered_names.update(row['confirmation_tool'] for row in content_objects.catalog(state, for_model=True)
                        if row.get('confirmation_tool') and any(item['item_index'] in confirmed_indices and item.get('awaiting_confirmation')
                                                               for item in row['items']))
        tools = prepare_tool_turn(
            core_tools=prepared.core_tools,
            discovered_names=discovered_names,
            org_id=handler.org_id,
            turn=turn,
            messages=prepared.messages,
            tool_context=prepared.tool_context,
            permission=prepared.permission,
            execution_context=prepared.execution_context,
        )
        if (runtime and runtime.skill_runtime is not None and runtime.skill_runtime.allows_dynamic_activation
                and runtime.skill_runtime.model_candidates()):
            tools.append(ACTIVATE_SKILL_SCHEMA)
            if any(key != runtime.skill_runtime.manual_skill_id and key not in runtime.skill_runtime.session_skill_ids
                   for key in runtime.skill_runtime.active):
                tools.append(DEACTIVATE_SKILL_SCHEMA)
        if runtime and runtime.skill_runtime is not None:
            if runtime.skill_runtime.has_deferred_assets:
                tools.append(READ_SKILL_ASSET_SCHEMA)
            tools = runtime.skill_runtime.filter_protocol_tools(tools)
        knowledge_plan=getattr(prepared,'knowledge_answer_plan',None)
        fast_knowledge=(knowledge_plan and knowledge_plan.get('mode') in ('answer','missing')
                        and content_tools.semantic_intent(request, runtime) == 'explain'
                        and not content_tools.requires_continued_delivery(request, runtime))
        if fast_knowledge:
            await asyncio.to_thread(handler._knowledge_run.revalidate)
            tools=[]
        from services.knowledge_workspace.answer_plan import constrain_route_tools
        tools=await constrain_route_tools(prepared,getattr(handler,'_knowledge_run',None),tools)
        from services.task_execution.model_directed import enabled as direct_enabled
        if direct_enabled(runtime) and not fast_knowledge:
            from services.task_execution.direct_loop import active_image_profiles, saved_image_plans, schemas as direct_schemas
            from services.task_execution.model_bridge import controller_for
            plans = saved_image_plans(runtime.task_execution_state)
            if active_image_profiles(runtime) or plans:
                tools = [tool for tool in tools if tool['function']['name'] != 'image_agent']
                if not plans and not content_tools.enabled(runtime, prepared.execution_context):
                    from services.task_execution.direct_loop import existing_image_source_schema
                    tools = [existing_image_source_schema(tool) if tool['function']['name'] == 'generate_image'
                             else tool for tool in tools]
            tools.extend(direct_schemas(runtime,controller_for(runtime)))
        elif runtime and runtime.task_execution_state is not None and not fast_knowledge:
            from services.task_execution.model_bridge import controller_for
            tools.extend(controller_for(runtime).schemas())
        elif runtime and runtime.task_bootstrap is not None and not fast_knowledge:
            from services.task_execution.bootstrap import START_TASK_SCHEMA
            tools.append(START_TASK_SCHEMA)
        if runtime and (runtime.task_execution_state is not None or runtime.task_bootstrap is not None) and not direct_enabled(runtime):
            from services.task_execution.methods import blocked_tool_names
            from services.tools.catalog import build_capability_catalog
            from services.task_execution.dispatch import annotate_task_tools, plan_dispatch_blockers
            catalog = build_capability_catalog()
            tools = annotate_task_tools(tools, catalog)
            blocked = blocked_tool_names(runtime.task_execution_state, catalog, runtime.skill_runtime)
            blocked |= plan_dispatch_blockers(runtime.task_execution_state, catalog).keys()
            tools = [tool for tool in tools if tool['function']['name'] not in blocked]
        from services.task_execution.requirement_review import coverage_blocked
        if coverage_blocked(runtime):
            from services.task_execution.bootstrap import START_TASK_SCHEMA
            tools=[START_TASK_SCHEMA]
        from services.task_execution.requirement_changes import blocked as revision_blocked,SCHEMA as REVISION_SCHEMA,phase_repair
        if runtime and runtime.task_execution_state is not None and revision_blocked(runtime):
            tools=[REVISION_SCHEMA]
            if phase_repair(runtime):tools.extend([ACTIVATE_SKILL_SCHEMA,DEACTIVATE_SKILL_SCHEMA])
        prepared.content_controls_available = (content_tools.enabled(runtime, prepared.execution_context)
            and not fast_knowledge and not coverage_blocked(runtime)
            and not (runtime.task_execution_state is not None and revision_blocked(runtime)))
        prepared.content_output_available = False
        prepared.reference_text_delivery = False
        prepared.plain_image_text_delivery = False
        prepared.saved_image_repeat = False
        prepared.allow_image_call_repair = False
        prepared.image_call_repair_needed = False
        prepared.delivery_call_repair = ()
        prepared.saved_delivery_available = False
        if prepared.content_controls_available:
            from services.task_execution.direct_loop import content_followup_state
            from services.task_execution.inline_image_action import allowed_for_offer
            content_state = runtime.task_execution_state or content_followup_state(runtime)
            server_image_plan = content_tools.uses_server_image_plan(prepared)
            saved_source_available = bool(
                content_objects.objects(content_state) or getattr(runtime, 'task_content_groups', ()))
            prepared.saved_image_repeat = (saved_source_available and not server_image_plan
                                           and content_tools.repeats_saved_image(request, runtime))
            reuse_source = content_tools.selects_previous_item(request, runtime) or prepared.saved_image_repeat
            first_delivery = (getattr(request.context_anchor, 'base_revision', None) == 0
                              and not content_objects.objects(content_state)
                              and not getattr(runtime, 'task_content_groups', ()))
            inline_image = allowed_for_offer(runtime, server_image_plan=server_image_plan, reuse_source=reuse_source)
            from core.config import get_settings
            single_image_edit = (inline_image and get_settings().chat_single_image_edit_enabled
                                 and not content_tools.has_newer_image_input(runtime)
                                 and content_tools.is_single_generated_image_edit(request, runtime))
            explicit_edit = (content_tools.semantic_intent(request, runtime) == 'edit'
                and len(content_objects.current_source_images(request)) == 1)
            single_image_edit = single_image_edit or (inline_image and explicit_edit
                and get_settings().chat_single_image_edit_enabled and not content_tools.has_newer_image_input(runtime))
            previous_image_reference = None
            if inline_image and content_tools.requests_generated_image_edit(request, runtime=runtime):
                from services.task_execution.inline_image_action import previous_result_reference
                previous_image_reference = await asyncio.to_thread(previous_result_reference,
                    content_state, handler, request, prepared.execution_context)
            from services.task_execution.image_reference_catalog import for_runtime
            tools = [*content_tools.model_tools(
                tools, selectable_source_images=content_objects.has_selectable_source_images(content_state),
                saved_text_available=saved_source_available,
                server_image_plan=server_image_plan, reuse_source=reuse_source,
                planned_image_count=getattr(getattr(workflow,'value',None),'image_count',None),
                inline_image=inline_image, single_image_edit=single_image_edit,
                inline_only=(inline_image and (single_image_edit or explicit_edit)
                             and not content_tools.authored_text_request(request, runtime)
                             and not content_tools.requested_delivery_tools(request, runtime)),
                generated_image_edit=(content_tools.requests_generated_image_edit(request, runtime=runtime)
                    and not any(str(a.source_task_id) == str(runtime.task_id) for a in content_objects.objects(content_state))),
                previous_image_reference=previous_image_reference,
                current_images=content_objects.current_source_images(request),
                first_delivery=first_delivery,
                image_reference_catalog=for_runtime(runtime),
            ), *content_tools.SCHEMAS]
            from services.task_execution import item_confirmation
            media = item_confirmation.directive(request, runtime)
            waits = tuple(media.wait_for)
            wait_all = media.wait_all
            if (waits or wait_all) and not server_image_plan:
                save_tool = next((tool['function'] for tool in tools
                                  if tool['function']['name'] == content_objects.SAVE), None)
                if save_tool is not None:
                    label = '整组逐条确认' if wait_all else f'分别确认编号 {list(waits)}'
                    save_tool['description'] += (
                        f' 用户要求{label}。保存整组并保留原编号顺序；'
                        '用户要求立即执行其中任何条目时，必须设置 continue_execution=true，'
                        '保存整组后仅调用当前已获准的条目。'
                        '文生视频条目按当前工具默认参数输出，options 留空。'
                        '图片和文件作为创作参考时用 usage=analysis，镜头描述写进 body。')
            prepared.content_output_available = not server_image_plan
            prepared.reference_text_delivery = content_tools.prefers_reference_text(
                request, runtime, saved_source_available=saved_source_available, server_image_plan=server_image_plan)
            prepared.plain_image_text_delivery = content_tools.prefers_plain_image_text(
                request,runtime,saved_source_available=saved_source_available,server_image_plan=server_image_plan)
            # Direct text is the default, while the existing save tool remains
            # available to carry user-defined waiting conditions in this same call.
            if content_tools.image_writing_only_request(request, runtime):
                tools = [tool for tool in tools if tool.get('function', {}).get('name') != 'generate_image']
            prepared.saved_delivery_available = saved_source_available and not server_image_plan
            input_id = content_tools.image_input_id(request)
            input_changed = content_tools.has_newer_image_input(runtime)
            if input_changed:
                prepared.messages[:] = [message for message in prepared.messages
                                       if message != {'role': 'system', 'content': content_tools.IMAGE_CALL_REPAIR}]
            prepared.allow_image_call_repair = bool(
                input_id and not server_image_plan and not input_changed
                and getattr(runtime, 'task_image_completion', None) is None
                and content_tools.checkpoint_state(runtime).get('image_call_repair_input') != input_id
                and saved_source_available and reuse_source
                and not any(block.get('type') == 'tool_step' and block.get('tool_name') not in content_tools.CONTROLS
                            for block in blocks))
        from services.tools.model_contracts import project_model_tool
        tools = [project_model_tool(tool) for tool in tools]
        prepared.content_visible_tools = {tool['function']['name']: tool['function'] for tool in tools if 'function' in tool}
        prepared.content_visible_tool_names = set(prepared.content_visible_tools)
        current_model_round = model_round
        if direct_enabled(runtime):
            from services.task_execution.direct_loop import context_message as direct_context
            prepared.messages[:]=[m for m in prepared.messages if not (
                m.get('role')=='system' and isinstance(m.get('content'),str)
                and m['content'].startswith('[Model-directed execution]'))]
            prepared.messages.append(direct_context(runtime))
        direct_snapshot=tuple(str(i.event_id) for i in runtime.task_user_changes.items if i.delivered) if (
            direct_enabled(runtime) and runtime.task_user_changes is not None) else ()
        before_usage = dict(totals.usage) if correcting else {}
        try:
            restored = []
            if prepared.content_controls_available:
                from services.task_execution.direct_loop import restored_content_calls
                restored = restored_content_calls(runtime, prepared.content_visible_tool_names)
            from services.task_execution.content_delivery import pending_response
            proposal = pending_response(runtime, request) if runtime and not restored else None
            if proposal:
                turn_text, turn_thinking, calls, previewed_call_ids = proposal['text'], '', proposal['calls'], set()
                totals.last_finish_reason = proposal.get('finish_reason')
                prepared.last_provider_output = None
                prepared.last_reasoning_content = ''
                if turn_text:
                    totals.text += turn_text
                    await sink.on_text(turn_text)
            elif restored:
                # The original model round consumed this authenticated user input.
                # Dispatch still goes through the current scope, revision and tool gates.
                turn_text, turn_thinking, calls, previewed_call_ids = "", "", restored, set()
                prepared.last_provider_output = None
                prepared.last_reasoning_content = ""
            else:
                turn_text, turn_thinking, calls, previewed_call_ids = await _read_turn(
                    prepared, tools, cancellation_event, sink, totals, blocks, runtime,
                    request.thinking_effort, thinking_mode, request,
                    model_round=current_model_round,
                )
        finally:
            if correcting:
                image_arguments.record_usage(before_usage, totals.usage, _get_model_gateway(prepared))
            image_metrics = image_arguments.summary()
            if image_metrics is not None:
                totals.usage["image_argument_metrics"] = image_metrics
        model_round += 1
        if direct_enabled(runtime):runtime.task_direct_seen=tuple(sorted(set(runtime.task_direct_seen)|set(direct_snapshot)))
        if fast_knowledge and calls:
            raise RuntimeError('KNOWLEDGE_ANSWER_UNEXPECTED_TOOL_CALL')
        if runtime:
            await runtime.safe_point(SafePoint.AFTER_MODEL)
            inbox = runtime.task_user_changes
            if inbox is not None and any(not item.delivered for item in inbox.items):
                # The previous answer used the old input. Text and material updates both supersede it.
                for block in blocks:
                    if block.get('tool_call_id') in previewed_call_ids:
                        block.update(status='cancelled', output='已收到补充素材，按更新后的要求继续。')
                        await sink.on_block_update(block)
                continue
        if direct_enabled(runtime) and any(c['name'] == content_objects.SAVE for c in calls):
            from services.task_execution.content_delivery import stage_response
            await stage_response(runtime, request, prepared, blocks, turn, current_model_round,
                                 turn_text, calls, totals.last_finish_reason, _build_replay_context)
        if prepared.delivery_call_repair and not prepared.budget.stop_reason:
            state = content_tools.checkpoint_state(runtime)
            targets = prepared.delivery_call_repair
            runtime.checkpoint_base[content_tools.STATE] = {
                **state, 'delivery_call_repairs': sorted(set(state.get('delivery_call_repairs', ())) | set(targets)),
            }
            prepared.messages.append({'role': 'system', 'content':
                '[Delivery check] 当前有明确的交付要求且未取得产物或受理。'
                '请核对选定来源和可用工具，继续尚未执行的交付。未直接提供目标工具时先查找。已有任务先查询原回执。'})
            continue
        if prepared.delivery_call_repair:
            turn_text = content_tools.delivery_notice(prepared.delivery_call_repair, ())
            totals.text += turn_text
            await sink.on_text(turn_text)
        if prepared.image_call_repair_needed and not prepared.budget.stop_reason:
            state = content_tools.checkpoint_state(runtime)
            runtime.checkpoint_base[content_tools.STATE] = {
                **state, 'image_call_repair_input': content_tools.image_input_id(request),
            }
            prepared.messages.append({'role': 'system', 'content': content_tools.IMAGE_CALL_REPAIR})
            continue
        if prepared.image_call_repair_needed:
            turn_text = content_tools.IMAGE_NOT_SUBMITTED
            totals.text += turn_text
            await sink.on_text(turn_text)
        tagged = prepared.content_output_available and content_objects.is_content_output(turn_text)
        indexed = None
        mixed_draft = False
        if len(calls) == 1 and calls[0]['name'] == content_objects.SAVE:
            try:
                draft = json.loads(calls[0]['arguments'])
                mixed_draft = isinstance(draft, dict) and 'document' in draft and not draft.get('continue_execution')
            except ValueError:
                pass
        if (prepared.content_output_available and not tagged and totals.last_finish_reason != 'length'
                and (not calls or mixed_draft)):
            from services.task_execution.content_groups import index_document
            try:
                indexed = index_document(turn_text, allow_plain=(not prepared.reference_text_delivery
                                         and content_tools.authored_text_request(request, runtime)),
                    fenced_prompts=prepared.plain_image_text_delivery and content_tools.semantic_intent(request,runtime)=='write')
                if indexed is None and prepared.plain_image_text_delivery:
                    # Existing numbered/quoted drafts retain their exact boundaries.
                    # A format variation cannot silently lose next-turn references.
                    indexed = index_document(turn_text, allow_plain=True)
                if (indexed is None and not calls and content_tools.semantic_runtime(runtime)
                        and content_tools.semantic_intent(request,runtime) is None):
                    candidate=index_document(turn_text,allow_plain=True)
                    workflow=getattr(prepared,'ecom_workflow',None)
                    if candidate is not None and workflow is not None:
                        from services.task_execution.content_delivery import stage_response,KEY as delivery_key
                        # Persist the exact visible reply before the metadata
                        # roundtrip. Recovery never asks the author to rewrite it.
                        await stage_response(runtime,request,prepared,blocks,turn,current_model_round,
                            turn_text,[],totals.last_finish_reason,_build_replay_context,
                            source='visible_text',control_pending=True)
                        bound=await workflow.bind_authored_response(len(candidate.document.items))
                        await runtime.safe_point(SafePoint.AFTER_MODEL)
                        inbox=runtime.task_user_changes
                        if inbox and any(not item.delivered for item in inbox.items):
                            runtime.checkpoint_base[delivery_key]['phase']='superseded'
                            continue
                        if bound:
                            indexed=candidate
                        else:
                            runtime.checkpoint_base[delivery_key]['phase']='superseded'
                if indexed:
                    document = indexed.document
            except ValueError:
                indexed = None  # An unindexable answer is still the user's completed reply.
        reference_unregistered = prepared.reference_text_delivery and not calls and not indexed
        if indexed or tagged or reference_unregistered:
            await _append_turn_blocks(blocks, sink, thinking=turn_thinking, text='',
                                      thinking_duration_ms=getattr(prepared, 'last_thinking_duration_ms', None))
            # A content delivery is terminal for this round. It must not be
            # combined with a model tool request, including business effects.
            if not indexed:
                document = None
            rejection = 'mixed_tool_call' if calls else 'incomplete_output'
            if not indexed and not reference_unregistered and not calls and totals.last_finish_reason != 'length':
                try:
                    document = content_objects.document_from_output(turn_text, references=prepared.content_output_references)
                except ValueError:
                    rejection = 'invalid_structure'
            if document is not None:
                if not calls:
                    # Register exactly this completed visible reply after a crash;
                    # never ask the model to author a replacement on recovery.
                    from services.task_execution.content_delivery import stage_response
                    await stage_response(runtime, request, prepared, blocks, turn, current_model_round,
                                         turn_text, [], totals.last_finish_reason, _build_replay_context,
                                         source='visible_text')
                arguments = json.dumps({'document': document.model_dump(mode='json', exclude_unset=True)}, ensure_ascii=False)
                save_call = {'id': _stable_actor_tool_call_id(runtime.turn_id, current_model_round, 0, content_objects.SAVE, arguments),
                             'name': content_objects.SAVE, 'arguments': arguments}
                await _execute_tools(handler=handler, request=request, prepared=prepared, turn=turn, turn_text='',
                    calls=[save_call], previewed_call_ids=set(), next_model_round=model_round,
                    cancellation_event=cancellation_event, sink=sink, blocks=blocks, runtime=runtime,
                    totals=totals, repeated_tool_call_guard=repeated_tool_call_guard, content_output_registration=True,
                    indexed_gaps=indexed.gaps if indexed else ())
                if blocks and blocks[-1].get('content_delivery_final') is True:
                    return None
                rejection = 'registration_rejected'
            text = turn_text if indexed or reference_unregistered else content_objects.render_unregistered_output(turn_text) + prepared.content_output_references
            logger.info('Legacy draft shown without registration | task={} | reason={} | chars={} | finish={}',
                        request.task_id, rejection, len(turn_text), totals.last_finish_reason)
            notice = {'type': 'text', 'text': text or content_objects.SAVE_FAILED_TEXT,
                      'content_delivery_final': True, 'content_incomplete': True}
            notice['text'] += '\n\n（正文已保留，但尚未登记为可执行方案。）'
            if totals.last_finish_reason == 'length':
                notice.update(text=notice['text'] + '\n\n（本轮输出未写完。）', content_incomplete=True)
            for block in blocks:
                if block.get('tool_call_id') in previewed_call_ids:
                    block.update(status='error', output='工具未执行，已保留本轮正文。')
                    await sink.on_block_update(block)
            blocks.append(notice)
            if not indexed:
                totals.text += notice['text'][len(turn_text):] if reference_unregistered else notice['text']
            prepared.messages.append({'role': 'assistant', 'content': notice['text']})
            await sink.on_block(notice)
            return None
        saving_content = direct_enabled(runtime) and any(c['name'] == content_objects.SAVE for c in calls)
        if prepared.defer_ecom_narrative:
            if not calls:
                notice = '本轮尚未完成服务器策划，正文尚未登记为可按编号执行的方案，未提交图片。'
                if totals.last_finish_reason == 'length':
                    notice += '模型输出未写完。'
                message = (turn_text + '\n\n' if turn_text else '') + '（' + notice + '）'
                totals.text += message
                blocks.append({"type": "text", "text": message, "content_incomplete": True})
                await sink.on_block(blocks[-1])
                return None
            turn_thinking = ""
            if not saving_content:
                turn_text = ""
        if prepared.content_output_available and totals.last_finish_reason == 'length':
            await _append_turn_blocks(blocks, sink, thinking=turn_thinking, text='',
                                      thinking_duration_ms=getattr(prepared, 'last_thinking_duration_ms', None))
            for block in blocks:
                if block.get('tool_call_id') in previewed_call_ids:
                    block.update(status='error', output='本轮输出未写完，工具未执行。')
                    await sink.on_block_update(block)
            notice = {'type': 'text', 'text': turn_text + '\n\n（本轮输出未写完。）',
                      'content_incomplete': True, 'content_delivery_final': True}
            blocks.append(notice)
            await sink.on_block(notice)
            return None
        await _append_turn_blocks(
            blocks,
            sink,
            thinking=turn_thinking,
            thinking_duration_ms=getattr(prepared, 'last_thinking_duration_ms', None),
            text='' if saving_content and prepared.defer_ecom_narrative else turn_text,
        )
        if not calls:
            if direct_enabled(runtime) and content_objects.retry_pending(prepared.messages):
                # A prose claim cannot close a failed structured delivery.
                break
            if runtime and runtime.task_execution_state is not None:
                from services.task_execution.model_bridge import review_model_completion
                if turn_text:
                    prepared.messages.append({'role': 'assistant', 'content': turn_text})
                if await review_model_completion(runtime=runtime, prepared=prepared, blocks=blocks,
                        build_checkpoint=_build_replay_context, turn=turn, next_model_round=model_round,
                        repeated_tool_call_guard=repeated_tool_call_guard, sink=sink,
                        exhausted=bool(fast_knowledge), terminal_result=terminal_task_result):
                    continue
                return None
            if not turn_text and prepared.budget.turns_used > 1:
                if not empty_output_retried:
                    empty_output_retried = True
                    if thinking_mode != 'disabled':
                        thinking_mode = None
                    prepared.messages.append({
                        "role": "user",
                        "content": "请根据刚才的工具执行结果，直接告诉我结论。",
                    })
                    continue
                fallback = _last_tool_output(blocks)
                fallback_text = (
                    "抱歉，我在整理回复时遇到了问题。以下是工具返回的原始结果：\n\n"
                    + (fallback or "（无工具输出）")
                )
                totals.text += fallback_text
                blocks.append({"type": "text", "text": fallback_text})
                await sink.on_block(blocks[-1])
            return None
        guard_calls=calls
        if direct_enabled(runtime):
            from services.task_execution.direct_loop import plan_guard_calls
            guard_calls=plan_guard_calls(calls)
        from services.task_execution.content_delivery import input_revision
        repeated_tool_call_action = repeated_tool_call_guard.observe(
            guard_calls, input_revision=input_revision(runtime, request))
        if repeated_tool_call_action == "stop":
            await _stop_repeated_tool_calls(
                calls=calls,
                previewed_call_ids=previewed_call_ids,
                blocks=blocks,
                sink=sink,
                totals=totals,
            )
            return None
        form_hint = await _execute_tools(
            handler=handler,
            request=request,
            prepared=prepared,
            turn=turn,
            turn_text=turn_text,
            calls=calls,
            previewed_call_ids=previewed_call_ids,
            next_model_round=model_round,
            cancellation_event=cancellation_event,
            sink=sink,
            blocks=blocks,
            runtime=runtime,
            totals=totals,
            repeated_tool_call_guard=repeated_tool_call_guard,
            repeated_tool_call_nudge=(
                _REPEATED_TOOL_CALL_NUDGE
                if repeated_tool_call_action == "nudge" else None
            ),
        )
        if blocks and blocks[-1].get('content_delivery_final') is True:
            return None
        if saving_content and content_objects.save_repair_exhausted(prepared.messages):
            break
        executor = getattr(handler, "_tool_executor", None)
        image_metrics = image_arguments.summary()
        if image_metrics is not None:
            totals.usage["image_argument_metrics"] = image_metrics
        resource_stop = getattr(getattr(executor, "_tool_runtime", None), "resource_stop_reason", "")
        if await finish_inline_receipt(turn, model_round):
            return None
        result_stop = getattr(handler, "_tool_result_stop_reason", "")
        if getattr(handler, '_request_response_stop', False):
            response = getattr(runtime, 'checkpoint_base', {}).get('_request_response_v1')
            if response and response['input_revision'] != request_input_revision(runtime, request):
                handler._request_response_stop = False
                handler._tool_result_stop_reason = ''
                continue  # A delivered answer/attachment superseded the question.
        if getattr(handler, "_terminal_image_pending", False) and not resource_stop and not direct_enabled(runtime):
            return None
        stop_message = resource_stop or result_stop
        if isinstance(stop_message, str) and stop_message:
            blocks.append({"type": "text", "text": stop_message})
            if getattr(runtime, 'checkpoint_base', {}).get('_request_response_v1'):
                blocks[-1]['request_response_final'] = True
            totals.text += stop_message
            await sink.on_block(blocks[-1])
            return None
        # FormBlockResult 是一个完整的交付物，不再发起额外的模型回合。
        # 这样既避免重复文案，也保证表单是该消息唯一的确认入口。
        if getattr(handler, "_terminal_form_pending", False):
            return None
        if getattr(handler, "_terminal_change_set_pending", False):
            return None
        if form_hint:
            return form_hint
    if direct_enabled(runtime) and content_objects.retry_pending(prepared.messages):
        notice = {'type': 'text', 'text': content_objects.SAVE_FAILED_TEXT, 'content_delivery_final': True, 'content_incomplete': True}
        blocks.append(notice)
        totals.text += notice['text']
        await sink.on_block(notice)
        return None
    if runtime and runtime.task_execution_state is not None:
        from services.task_execution.model_bridge import review_model_completion
        await review_model_completion(runtime=runtime, prepared=prepared, blocks=blocks,
            build_checkpoint=_build_replay_context, turn=prepared.budget.turns_used,
            next_model_round=model_round, repeated_tool_call_guard=repeated_tool_call_guard,
            exhausted=True, sink=sink, terminal_result=terminal_task_result)
    elif runtime and getattr(runtime,'task_requirement_review',None) is not None:
        notice={'type':'text','text':'已达到本次执行预算，任务交付要求尚未核对完成；原请求与核对记录已保留，未启动外部执行。',
            'task_status':'partial'}
        blocks.append(notice)
        await sink.on_block(notice)
    return None


_IMAGE_DELIVERY_COMPLETE = '<image-delivery-complete/>'


def _image_delivery_can_finish_silently(runtime):
    """Only an applied image event with a complete physical outcome can close silently."""
    event = getattr(runtime, 'task_image_completion', None)
    if event is None or not event.event_artifact_ids or runtime.task_unreviewed_changes:
        return False
    state = runtime.task_execution_state
    if not state.model_directed:
        return False
    plan = state.working_plan
    if plan and any(item.status != 'completed' for item in plan.steps):
        return False
    from services.task_execution.direct_loop import end_candidate

    return end_candidate(state).status == 'completed'


async def _read_turn(
    prepared: Any, tools: list[dict[str, Any]], cancellation_event: asyncio.Event,
    sink: ExecutionSink, totals: StreamTotals, blocks: list[dict[str, Any]],
    runtime: ConversationTurnRuntime | None, thinking_effort: str | None = None,
    thinking_mode: str | None = None, request: ChatExecutionRequest | None = None,
    model_round: int = 0, task_review: bool | str = False,
) -> tuple[str, str, list[dict[str, Any]], set[str]]:
    from services.agent.observability.model_request import capture_requests
    rows = []
    with capture_requests(rows):
        try:
            return await _read_turn_impl(prepared, tools, cancellation_event, sink, totals, blocks,
                                         runtime, thinking_effort, thinking_mode, request, model_round, task_review)
        finally:
            if runtime is not None and rows:
                # Only bounded tool schemas enter the private checkpoint.
                # Scalar logs retain digests/sizes, never prompts or schemas.
                runtime.checkpoint_base['_model_wire_v1'] = [
                    {**row, 'requested_mode': thinking_mode, 'requested_effort': thinking_effort} for row in rows]


async def _read_turn_impl(
    prepared: Any,
    tools: list[dict[str, Any]],
    cancellation_event: asyncio.Event,
    sink: ExecutionSink,
    totals: StreamTotals,
    blocks: list[dict[str, Any]],
    runtime: ConversationTurnRuntime | None,
    thinking_effort: str | None = None,
    thinking_mode: str | None = None,
    request: ChatExecutionRequest | None = None,
    model_round: int = 0,
    task_review: bool | str = False,
) -> tuple[str, str, list[dict[str, Any]], set[str]]:
    turn_text = ""
    turn_thinking = ""
    calls: dict[int, dict[str, Any]] = {}
    prepared.last_provider_output = None
    prepared.last_reasoning_content = ""
    prepared.content_output_references = ""
    totals.last_finish_reason = None
    builtin_sources: dict[str, str] = {}
    previewed_indices: set[int] = set()
    preview_ids: dict[int, str] = {}
    model_gateway = _get_model_gateway(prepared)
    # Old in-band delivery markers must never be emitted as text. Ordinary
    # response chunks use the chat stream directly, independently of tools.
    probing_legacy = not task_review and getattr(prepared, 'content_output_available', False)
    from services.task_execution.direct_loop import followup_source
    awaiting_content_owner = runtime is not None and runtime.task_execution_state is None and followup_source(runtime) is not None
    defer_narrative = getattr(prepared, "defer_ecom_narrative", False)
    image_completion = not task_review and getattr(runtime, 'task_image_completion', None) is not None
    image_reply = (not task_review and not image_completion and request is not None and not defer_narrative
                   and (content_tools.image_execution_request(request, runtime)
                        or getattr(prepared, 'saved_image_repeat', False)))
    silent_delivery = image_completion and _image_delivery_can_finish_silently(runtime)
    native_continuation = bool(getattr(runtime, 'checkpoint_base', {}).get('_continuation_input'))
    delivery = (content_tools.requested_delivery_tools(request, runtime)
                if not task_review and not native_continuation and not image_completion
                and request is not None and not defer_narrative else {})
    delivery_status = {tool: content_tools.delivery_status(runtime, prepared, blocks, tool) for tool in delivery}
    missing_delivery = {tool: label for tool, label in delivery.items() if delivery_status[tool] is None}
    video_receipts = set(getattr(prepared, 'video_acceptance_by_task', {}).values()) if (
        not task_review and not native_continuation) else set()
    video_pending = delivery_status.get('generate_video') == 'accepted' or bool(video_receipts)
    defer_narrative = defer_narrative or image_reply or silent_delivery or bool(missing_delivery) or video_pending
    from services.handlers.interrupt_anchor import project_interrupt_history

    skills = runtime.skill_runtime if runtime and not task_review else None
    messages = project_interrupt_history(prepared.messages)
    if not task_review and getattr(prepared, 'reference_text_delivery', False):
        messages = [*messages, {'role': 'system', 'content': content_tools.REFERENCE_TEXT_INSTRUCTION}]
    elif not task_review and getattr(prepared, 'plain_image_text_delivery', False):
        messages = [*messages, {'role': 'system', 'content': content_tools.PLAIN_IMAGE_TEXT_INSTRUCTION}]
    if image_completion:
        from services.task_execution.image_continuation import model_recovery_facts

        instruction = (
            '这是已核验的图片任务完成事件。图片结果已由系统发布，按完成事件处理原目标剩余的要求。'
            '如有失败，按已记录的结果说明，不自动重提交失败的图片。'
        )
        failures = model_recovery_facts(runtime)
        if failures:
            instruction += (' 以下为原任务的失败判定。受理未知时查询原任务；'
                            '继续可独立完成的交付并保留已发布成果。\n'
                            + json.dumps(failures, ensure_ascii=False))
        if silent_delivery:
            instruction += (
                '如果已发布的图片已满足用户全部要求，且没有待处理的追加要求，'
                '只返回 ' + _IMAGE_DELIVERY_COMPLETE + '，无需再发完成通知。'
                '如果用户还要求其他交付，直接完成那些工作。'
            )
        messages = [*messages, {'role': 'system', 'content': instruction}]
    model_messages = skills.model_messages(messages, tools) if skills else messages
    if not task_review and (visual_notice := content_tools.visual_context_notice(model_messages)):
        model_messages = [*model_messages, {'role': 'system', 'content': visual_notice}]
    from services.task_execution.image_reference_catalog import for_runtime
    image_references = for_runtime(runtime) if not task_review else None
    if image_references is not None:
        model_messages = image_references.messages(model_messages)
        tools = image_references.tools(tools, plain_text_registration=getattr(prepared, 'plain_image_text_delivery', False))
        if content_tools.semantic_intent(request, runtime) in {'generate', 'edit', 'write_then_submit_images'}:
            model_messages.append({'role': 'system', 'content':
                '当前语义动作包含实际图片交付。先核对所选需求及其等待条件；未要求等待确认时，'
                '不要只返回提示词正文或只保存后结束。单图可用generate_image的编辑或draft形式；'
                '需要先保存完整方案再执行时，用save_task_text显式continue_execution=true后按真实保存回执提交。'
                '有用户等待条件时仍须遵守，不把这条说明当作批准，也不能执行无关历史稿。'})
    stream_kwargs = {
        "messages": model_messages,
        "tools": tools,
        "reasoning_effort": thinking_effort,
        "thinking_mode": thinking_mode,
        **prepared.stream_kwargs,
    }
    if task_review:
        from services.model_reasoning import supports_off
        review_mode = ('disabled' if supports_off(getattr(model_gateway, 'model_id', ''),
                       getattr(model_gateway, 'provider', '')) else None)
        stream_kwargs.update(tools=[], enable_builtin_tools=False,
                             max_tokens=6000 if task_review == 'plan_facts' else 2048, thinking_mode=review_mode)
    # 旧测试/兼容入口仍可注入 adapter-shaped double；只有永久 Gateway
    # 消费本地 turn_index，不能把该内部字段转发给旧 Provider adapter。
    from services.model_gateway import ModelGatewaySession

    if isinstance(model_gateway, ModelGatewaySession):
        stream_kwargs["turn_index"] = model_round
        stream_kwargs['skill_context'] = bool(skills and skills.has_active_skills)
    from services.agent.observability.chat_stream import ModelTurnTiming
    from services.agent.observability.chat_lifecycle import chat_phase
    chat_phase('model.begin', task_id=request.task_id if request else None, model_round=model_round)
    model_timing = ModelTurnTiming()
    stream = model_gateway.stream_chat(
        **stream_kwargs,
    )
    stream_iterator = stream.__aiter__()
    while True:
        next_chunk = asyncio.create_task(stream_iterator.__anext__())
        cancel_waiter = asyncio.create_task(cancellation_event.wait())
        command_waiter = (
            asyncio.create_task(runtime.wait_for_command())
            if runtime is not None else None
        )
        wait_set = {next_chunk}
        wait_set.add(cancel_waiter)
        if command_waiter is not None:
            wait_set.add(command_waiter)
        try:
            done, _ = await asyncio.wait(
                wait_set,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            # 外层 execute_chat/Actor 在等待竞争任务时被取消，也必须收口
            # __anext__；否则 Gateway 的 Provider stream 会脱离主任务继续
            # 运行，最终产生未领取的 provider 异常或继续产生输出。
            pending_tasks = [
                task for task in (next_chunk, cancel_waiter, command_waiter)
                if task is not None
            ]
            for task in pending_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending_tasks, return_exceptions=True)
            raise

        # 命令先到时主动结束 provider stream；随后 safe_point 会刷盘并
        # 把 PAUSE/CANCEL 归约为控制流异常。不会每个 token 查询数据库。
        if cancel_waiter in done and next_chunk not in done:
            next_chunk.cancel()
            await asyncio.gather(next_chunk, return_exceptions=True)
            if command_waiter is not None:
                command_waiter.cancel()
                await asyncio.gather(command_waiter, return_exceptions=True)
            await _check_cancelled(
                cancellation_event, request, prepared.messages, blocks,
                totals, "stream",
            )

        if command_waiter is not None and command_waiter in done and next_chunk not in done:
            next_chunk.cancel()
            await asyncio.gather(next_chunk, return_exceptions=True)
            cancel_waiter.cancel()
            await asyncio.gather(cancel_waiter, return_exceptions=True)
            await runtime.safe_point(SafePoint.MODEL_CHUNK)
            break

        if command_waiter is not None and not command_waiter.done():
            command_waiter.cancel()
            await asyncio.gather(command_waiter, return_exceptions=True)
        if not cancel_waiter.done():
            cancel_waiter.cancel()
        await asyncio.gather(cancel_waiter, return_exceptions=True)

        try:
            chunk = next_chunk.result()
        except StopAsyncIteration:
            break
        if runtime:
            await runtime.safe_point(SafePoint.MODEL_CHUNK)
            if runtime.task_user_changes and any(item.attachments and not item.delivered
                                                 for item in runtime.task_user_changes.items):
                _accumulate_usage(totals, chunk)
                close = getattr(stream_iterator, 'aclose', None)
                if close is not None:
                    await close()
                break
        await _check_cancelled(
            cancellation_event, request, prepared.messages, blocks,
            totals, "stream",
        )
        provider_output = getattr(chunk, "provider_output", None)
        if isinstance(provider_output, dict):
            prepared.last_provider_output = provider_output
        event = getattr(chunk, "builtin_tool_event", None)
        if isinstance(event, dict) and event:
            from services.handlers.chat.builtin_tools import present_builtin_event
            block = present_builtin_event(event)
            existing = next((i for i, b in enumerate(blocks) if b.get("tool_call_id") == block["tool_call_id"]), None)
            if existing is None:
                blocks.append(block)
                await sink.on_block(block)
            else:
                blocks[existing] = block
                await sink.on_block_update(block)
            for source in event.get("sources", []):
                builtin_sources[source["url"]] = source.get("title") or source["url"]
        if chunk.thinking_content:
            model_timing.on_thinking()
            turn_thinking += chunk.thinking_content
            if not defer_narrative:
                totals.thinking += chunk.thinking_content
                await sink.on_thinking(chunk.thinking_content)
        if chunk.content:
            model_timing.on_text()
            if model_timing.chunks == 1:
                chat_phase('model.first_body', task_id=request.task_id if request else None, model_round=model_round)
            turn_text += chunk.content
            if not defer_narrative:
                visible = chunk.content
                if probing_legacy:
                    prefix = turn_text.lstrip()
                    if content_objects.OUTPUT_START.startswith(prefix) or content_objects.is_content_output(prefix):
                        visible = ''
                    else:
                        probing_legacy = False
                        visible = turn_text
                if visible:
                    totals.text += visible
                    await sink.on_text(visible)
        if chunk.tool_calls:
            accumulate_tool_call_delta(calls, chunk.tool_calls)
            if runtime is not None and not task_review:
                for index, call in calls.items():
                    if (index in previewed_indices or not call.get("name")
                            or call["name"] in SKILL_CONTROL_TOOLS
                            or call["name"] in {SAVE_IMAGE_PLAN, content_objects.SAVE, "get_conversation_context"}
                            or call["name"] in content_tools.CONTROLS
                            or (awaiting_content_owner and call["name"] in content_tools.INPUT_TOOLS)):
                        continue
                    preview_id = _actor_tool_preview_id(
                        runtime.turn_id, model_round, index,
                    )
                    call["id"] = preview_id
                    preview_ids[index] = preview_id
                    previewed_indices.add(index)
                    preview_block = {
                        "type": "tool_step",
                        "tool_name": call["name"],
                        "tool_call_id": preview_id,
                        "status": "running",
                    }
                    from services.task_execution.model_directed import enabled as direct_enabled
                    if direct_enabled(runtime):preview_block['presentation']='model_directed'
                    if not any(
                        block.get("tool_call_id") == preview_id
                        for block in blocks
                    ):
                        blocks.append(preview_block)
                        await sink.on_block(preview_block)
        _accumulate_usage(totals, chunk)
    from services.adapters.dashscope.responses import CACHE_MODELS
    prepared.last_reasoning_content = turn_thinking if getattr(model_gateway, "model_id", "") in CACHE_MODELS else ""
    content_output = (not task_review and getattr(prepared, 'content_output_available', False)
                      and content_objects.is_content_output(turn_text))
    if builtin_sources:
        from services.handlers.chat.builtin_tools import source_text
        references = source_text(builtin_sources)
        if content_output:
            prepared.content_output_references = references
        else:
            turn_text += references
            if not defer_narrative and not probing_legacy:
                totals.text += references
                await sink.on_text(references)
    ordered_indices = sorted(calls)
    ordered_calls = [calls[index] for index in ordered_indices]
    if probing_legacy and not content_output and not defer_narrative and turn_text:
        if turn_text.lstrip().startswith('<content-'):
            turn_text = turn_text[:len(turn_text)-len(turn_text.lstrip())]
        if turn_text:
            totals.text += turn_text
            await sink.on_text(turn_text)
    previewed_call_ids: set[str] = set()
    if runtime:
        for index, call in zip(ordered_indices, ordered_calls):
            if index in preview_ids:
                call["id"] = preview_ids[index]
                previewed_call_ids.add(call["id"])
            else:
                call["id"] = _stable_actor_tool_call_id(
                    runtime.turn_id,
                    model_round,
                    index,
                    call.get("name", ""),
                    call.get("arguments", ""),
                )
    else:
        ordered_calls.sort(key=lambda call: call.get("id", ""))
    chat_phase('model.end', task_id=request.task_id if request else None, model_round=model_round)
    prepared.last_thinking_duration_ms = model_timing.thinking_duration_ms
    timing = model_timing.summary(held=False,
                                  review=task_review)
    if not hasattr(prepared, 'model_turn_timings'):
        prepared.model_turn_timings = []
    timings = prepared.model_turn_timings
    if len(timings) < 48:
        timings.append({'round': model_round, **timing})
    logger.info('Chat model timing | task={} | round={} | metrics={}',
                request.task_id if request else None, model_round, timing)
    if image_reply and not content_output:
        from services.task_execution.request_resolution import context as request_context, image_status_reply
        if request_context(runtime) is not None:
            turn_text = '' if ordered_calls else image_status_reply(runtime, request)
        if not getattr(prepared, 'image_receipt_seen', False) and not any(
                c['name'] == content_objects.SAVE for c in ordered_calls):
            if request_context(runtime) is None or ordered_calls:
                turn_text = content_tools.unconfirmed_image_reply(turn_text, bool(ordered_calls))
            if (not ordered_calls
                    and getattr(prepared, 'allow_image_call_repair', False)
                    and totals.last_finish_reason != 'length'
                    and any(tool['function']['name'] == 'generate_image' for tool in tools)):
                prepared.image_call_repair_needed = True
                turn_text = ''
    if missing_delivery and not content_output:
        if ordered_calls:
            # Wait for the actual result instead of streaming speculative status.
            turn_text = ''
        else:
            repairs = content_tools.checkpoint_state(runtime).get('delivery_call_repairs', ()) if runtime else ()
            offered = {tool['function']['name'] for tool in tools if 'function' in tool}
            attempted = {block.get('tool_name') for block in blocks if block.get('type') == 'tool_step'}
            repairable = {tool: label for tool, label in missing_delivery.items()
                          if (tool in offered or content_tools.DISCOVER in offered)
                          and tool not in attempted and tool not in repairs}
            if (repairable and getattr(prepared, 'saved_delivery_available', False) and runtime is not None
                    and content_tools.image_input_id(request) and not content_tools.has_newer_image_input(runtime)
                    and totals.last_finish_reason != 'length'):
                prepared.delivery_call_repair = repairable
                turn_text = ''
            else:
                turn_text = '\n\n'.join(filter(None, (turn_text, content_tools.delivery_notice(missing_delivery, attempted))))
    if silent_delivery:
        if turn_text.strip() == _IMAGE_DELIVERY_COMPLETE:
            turn_text = ''
        if not turn_text.strip() and not ordered_calls:
            turn_text = turn_thinking = ''
    if video_pending and not content_output and not content_tools.semantic_runtime(runtime):
        from services.task_execution.item_confirmation import directive
        media = directive(request, runtime) if request else None
        confirmed_item = media.approved[0] if media and media.confirmation_only and len(media.approved) == 1 else None
        turn_text = content_tools.verified_video_status_text(
            turn_text, fresh=video_receipts == {'new'}, confirmed_item=confirmed_item)
    if (image_reply or silent_delivery or missing_delivery or video_pending) and not content_output and turn_text:
        totals.text += turn_text
        await sink.on_text(turn_text)
    return turn_text, turn_thinking, ordered_calls, previewed_call_ids


def _get_model_gateway(prepared: Any) -> Any:
    """读取 Gateway 会话；兼容旧测试注入的 adapter-shaped doubles。"""
    model_gateway = getattr(prepared, "model_gateway", None)
    if model_gateway is not None:
        return model_gateway
    return prepared.adapter


def _initial_model_round(replay_context: dict[str, Any] | None) -> int:
    """恢复 Actor 时从 checkpoint 继续使用稳定的模型回合号。"""
    if not replay_context:
        return 0
    next_model_round = replay_context.get("next_model_round")
    if (
        isinstance(next_model_round, int)
        and not isinstance(next_model_round, bool)
    ):
        return max(0, next_model_round)
    turn_index = replay_context.get("turn_index")
    if isinstance(turn_index, int) and not isinstance(turn_index, bool):
        # 兼容旧 checkpoint：旧 payload 只记录已完成的本地回合索引。
        return max(0, turn_index + 1)
    return 0


def _actor_tool_preview_id(
    turn_id: str,
    model_round: int,
    index: int,
) -> str:
    """Return one ID shared by a streamed call's preview and final step."""
    return f"actor-call:{turn_id}:round:{model_round}:index:{index}"


def _stable_actor_tool_call_id(
    turn_id: str,
    model_round: int,
    index: int,
    tool_name: str,
    arguments: str,
) -> str:
    """为 Actor 重试生成稳定调用 ID，避免供应商 ID 变化导致重复副作用。"""
    fingerprint = hashlib.sha256(
        f"{tool_name}\n{arguments}".encode("utf-8")
    ).hexdigest()[:24]
    return f"actor-call:{turn_id}:round:{model_round}:index:{index}:{fingerprint}"


def _actor_tool_completion_command_id(
    task_id: str,
    turn_id: str,
    tool_call_ids: list[str],
) -> str:
    """生成有界且稳定的工具批次去重键；原始 ID 仍保存在事件 payload。"""
    encoded_ids = json.dumps(
        tool_call_ids,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(encoded_ids).hexdigest()[:32]
    return f"tool-batch:{task_id}:{turn_id}:{len(tool_call_ids)}:{fingerprint}"


async def _append_turn_blocks(
    blocks: list[dict[str, Any]],
    sink: ExecutionSink,
    *,
    thinking: str,
    text: str,
    thinking_duration_ms: int | None = None,
) -> None:
    if text and not thinking and getattr(sink, "emit_empty_thinking", False):
        block = {"type": "thinking", "text": "", "duration_ms": 0}
        blocks.append(block)
        await sink.on_block(block)
    for block in (
        {"type": "thinking", "text": thinking, "duration_ms": thinking_duration_ms} if thinking else None,
        {"type": "text", "text": text} if text else None,
    ):
        if block:
            blocks.append(block)
            await sink.on_block(block)


async def _execute_tools(**kwargs):
    from services.task_execution.model_directed import enabled as direct_enabled
    runtime=kwargs.get('runtime')
    if not direct_enabled(runtime):return await _execute_tools_impl(**kwargs)
    import copy
    sequence=runtime.replay_save_sequence
    original_state=runtime.task_execution_state
    original_skills=runtime.skill_runtime
    original_checkpoint=copy.deepcopy(runtime.checkpoint_base)
    original_plan=runtime.task_direct_plan
    original_plan_owner=runtime.task_direct_plan_owner
    original_plan_id=runtime.task_direct_plan_id
    messages=copy.deepcopy(kwargs['prepared'].messages)
    blocks=copy.deepcopy(kwargs['blocks'])
    try:return await _execute_tools_impl(**kwargs)
    except BaseException:
        if runtime.replay_save_sequence==sequence:
            runtime.task_execution_state=original_state
            runtime.skill_runtime=original_skills
            runtime.checkpoint_base=original_checkpoint
            runtime.task_direct_plan=original_plan
            runtime.task_direct_plan_owner=original_plan_owner
            runtime.task_direct_plan_id=original_plan_id
            kwargs['prepared'].messages[:]=messages
            kwargs['blocks'][:]=blocks
        raise


async def _execute_tools_impl(
    *,
    handler: Any,
    request: ChatExecutionRequest,
    prepared: Any,
    turn: int,
    turn_text: str,
    calls: list[dict[str, Any]],
    previewed_call_ids: set[str],
    cancellation_event: asyncio.Event,
    sink: ExecutionSink,
    blocks: list[dict[str, Any]],
    runtime: ConversationTurnRuntime | None,
    next_model_round: int | None = None,
    totals: StreamTotals | None = None,
    repeated_tool_call_guard: _RepeatedToolCallGuard | None = None,
    repeated_tool_call_nudge: str | None = None,
    assistant_already_present: bool = False,
    recovery_only: bool = False,
    content_output_registration: bool = False,
    indexed_gaps: tuple[str, ...] = (),
) -> str | None:
    totals = totals or StreamTotals()
    from services.task_execution.model_bridge import TASK_CONTROL_TOOLS, execute_control_batch
    from services.task_execution.requirement_review import coverage_blocked
    from services.task_execution.plan_facts import pending_review
    from services.task_execution.requirement_changes import refresh as refresh_user_changes,blocked as revision_blocked,phase_repair
    if runtime is not None:await refresh_user_changes(runtime)
    from services.task_execution.image_reference_catalog import for_runtime
    image_references = for_runtime(runtime)
    if image_references is not None:
        from services.task_execution.contracts import TaskError
        from services.tools.argument_validation import decode_tool_arguments
        decoded_calls = []
        reference_error = None
        for call in calls:
            try:
                raw = decode_tool_arguments(call.get('arguments') or '{}')
                decoded = image_references.decode(raw)
                decoded_calls.append({**call, 'arguments': json.dumps(decoded, ensure_ascii=False)} if decoded != raw else call)
            except TaskError as error:
                reference_error = str(error)
                break
            except (ValueError, TypeError):
                decoded_calls.append(call)  # Existing strict argument validation owns malformed JSON.
        if reference_error:
            if any(call['name'] == content_objects.SAVE for call in calls):
                from services.task_execution.content_delivery import finish_response
                finish_response(runtime, None, {'ok': False, 'code': reference_error}, request=request)
            prepared.messages.append(_assistant_tool_message(turn_text, calls))
            for call in calls:
                output = json.dumps({'ok': False, 'accepted': False, 'code': reference_error,
                    'submission_state': 'not_dispatched', 'message': '短引用不存在、已过期或与真实ID冲突。请从当前图片目录重新选择image_ref，不猜测编号。'}, ensure_ascii=False)
                prepared.messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': output})
                block = next((b for b in blocks if b.get('tool_call_id') == call['id']), None)
                if block is not None:
                    block.update(status='error', output=output)
                    await sink.on_block_update(block)
                else:
                    block = {'type': 'tool_step', 'tool_call_id': call['id'], 'tool_name': call['name'],
                             'input': call['arguments'], 'status': 'error', 'output': output}
                    blocks.append(block)
                    await sink.on_block(block)
            await runtime.safe_point(SafePoint.AFTER_TOOL, replay_payload=_build_replay_context(
                prepared.messages, blocks, turn, next_model_round=next_model_round))
            return None
        calls = decoded_calls
    candidate_workflow = getattr(prepared, 'ecom_workflow_candidate', None)
    if candidate_workflow is not None and prepared.ecom_workflow is None:
        from services.agent.image.ecommerce_planner.workflow import SKILL as SERVER_PLANNER
        for call in calls:
            if call['name'] != ACTIVATE_SKILL:
                continue
            try:
                key = json.loads(call.get('arguments') or '{}').get('skill_id')
            except (ValueError, TypeError, AttributeError):
                continue
            skills = runtime.skill_runtime
            candidate = skills.directory.get(key) if skills is not None and isinstance(key, str) else None
            if (key == SERVER_PLANNER and candidate is not None and candidate.catalog_metadata.model_selectable
                    and not any(b.get('candidate_skill_id') == key for b in blocks)):
                await candidate_workflow.initialize(recover_previous=False)
                prepared.ecom_workflow = candidate_workflow
                break
    from services.task_execution.model_directed import enabled as direct_enabled
    direct_errors={}
    repair_batch = None
    use_repair_contract = runtime is not None and not any(
        call['name'] in {*TASK_CONTROL_TOOLS, *SKILL_CONTROL_TOOLS, *content_tools.CONTROLS,
                        content_objects.SAVE, SAVE_IMAGE_PLAN}
        for call in calls
    )
    if use_repair_contract and not recovery_only:
        repair_chat_images = (getattr(prepared, 'content_controls_available', False)
                              and not content_tools.uses_server_image_plan(prepared))
        repair_batch = tool_argument_repairs.Batch.prepare(
            runtime, request, calls, getattr(prepared, 'content_visible_tools', {}),
            repair_chat_images=repair_chat_images)
    repair_rejections = repair_batch.rejections if repair_batch else {}
    direct_reused=[]
    bound_sources = {}
    if direct_enabled(runtime):
        from services.task_execution.direct_loop import (
            can_resume_content_calls, content_followup_state, resume_from_model, retire_saved_image_method,
            stage_plan_calls, stage_native_calls, stage_image_plan_batch,
        )
        assistant=_assistant_tool_message(turn_text,calls)
        scoped_content = content_tools.enabled(runtime, prepared.execution_context)
        from services.task_execution import item_confirmation
        media = item_confirmation.directive(request, runtime)
        confirmation_items = tuple(media.wait_for)
        if content_tools.semantic_runtime(runtime) or content_tools.semantic_context(request, runtime) is not None:
            runtime.checkpoint_base.setdefault('_media_decisions_v1', {'version': 1, 'items': []})
        server_image_plan = scoped_content and content_tools.uses_server_image_plan(prepared)
        current_images = content_objects.current_source_images(request) if scoped_content else ()
        if getattr(prepared,'last_provider_output',None):assistant['_dashscope_output']=prepared.last_provider_output
        if getattr(prepared,'last_reasoning_content',''):assistant['reasoning_content']=prepared.last_reasoning_content
        from services.task_execution import inline_image_action
        inline_calls = [call for call in calls if call['id'] not in repair_rejections
                        and inline_image_action.is_inline_call(call)]
        inline_barrier = bool(inline_calls and len(calls) != 1)
        if inline_barrier and recovery_only:
            raise ConversationStopRequested('inline_image_batch_checkpoint_invalid')
        if inline_barrier or any(c['name'] in content_tools.CONTROLS for c in calls):
            if not assistant_already_present:
                prepared.messages.append(assistant)
            state = content_tools.checkpoint_state(runtime)
            names = set(state.get('names', ()))
            observed = dict(state.get('observed_history', {}))
            from services.tools.model_contracts import offered_argument_error

            context = prepared.execution_context
            visible = getattr(prepared, 'content_visible_tools', {})
            read_parameters = visible.get(content_tools.READ, {}).get('parameters', content_tools.READ_PARAMETERS)
            viewed_images = []
            delivered_images = []
            for call in calls:
                result = {'ok': False, 'code': 'CHAT_CONTENT_CONTROL_BARRIER'}
                if inline_barrier:
                    result.update(code='CHAT_INLINE_IMAGE_BATCH_REQUIRED',
                                  message='单张即时生成必须单独提交 draft。多张图使用已保存的分条方案。')
                elif call['name'] in content_tools.CONTROLS:
                    try:
                        if recovery_only or not getattr(prepared, 'content_controls_available', False):
                            raise PermissionError('CHAT_CONTENT_CONTROL_UNAVAILABLE')
                        args = json.loads(call.get('arguments') or '{}')
                        if not isinstance(args, dict):
                            raise ValueError('CHAT_CONTENT_ARGUMENTS_INVALID')
                        if call['name'] == 'respond_without_execution':
                            from services.task_execution.request_resolution import context as request_context, RESPONSE_SCHEMA
                            from services.task_execution.content_delivery import input_revision
                            if (len(calls) != 1 or request_context(runtime) is None
                                    or offered_argument_error(call['name'], args, RESPONSE_SCHEMA['function']['parameters'])):
                                raise ValueError('CHAT_RESPONSE_CONTROL_INVALID')
                            from services.task_execution.request_resolution import image_status_reply, binding
                            selected = binding(runtime) or {}
                            effect = any(op.tool_name == 'generate_image' and str(op.owner_task_id) == selected.get('execution_task_id')
                                         for op in runtime.task_execution_state.operations) if runtime.task_execution_state else False
                            rendered = (image_status_reply(runtime, request) if args['purpose'] == 'explanation'
                                        else args['text'] + '\n\n' + image_status_reply(runtime, request)) if effect else args['text'] + '\n\n本轮未提交新的图片任务。'
                            runtime.checkpoint_base['_request_response_v1'] = {
                                'input_revision': input_revision(runtime, request),
                                'purpose': args['purpose'], 'text': args['text'], 'status': 'native_effect' if effect else 'not_submitted',
                                'rendered_text': rendered}
                            handler._tool_result_stop_reason = rendered
                            handler._request_response_stop = True
                            result = {'ok': True, 'accepted': False, 'status': 'not_submitted'}
                        elif call['name'] == 'resolve_request_target':
                            from services.task_execution import request_resolution
                            if len(calls) != 1 or request_resolution.context(runtime) is None:
                                raise ValueError('CHAT_TARGET_CONTROL_INVALID')
                            rejection = offered_argument_error(call['name'], args, request_resolution.selection_schema(runtime)['function']['parameters'])
                            if rejection:
                                result = {**json.loads(rejection), 'ok': False, 'accepted': False}
                            else:
                                result = await request_resolution.select(runtime, request, prepared, args)
                        elif call['name'] == content_tools.DISCOVER and set(args) == {'query'}:
                            from services.tools.runtime_context import _check_identity
                            from types import SimpleNamespace
                            await asyncio.to_thread(_check_identity, SimpleNamespace(db=handler.db), context)
                            from services.task_execution.direct_loop import active_image_profiles, saved_image_plans
                            blocked = {'image_agent'} if (active_image_profiles(runtime)
                                or saved_image_plans(runtime.task_execution_state)) else set()
                            query = args['query']
                            if not isinstance(query, str) or not 1 <= len(query.strip()) <= 160:
                                raise ValueError('CHAT_TOOL_QUERY_INVALID')
                            exact_tool = visible.get(query.strip().casefold())
                            if exact_tool is not None:
                                result = {'ok': True, 'tools': [exact_tool], 'status': 'ready'}
                            else:
                                candidates = content_tools.discover(context, query, blocked)
                                names.update(item['name'] for item in candidates)
                                result = {'ok': True, 'tools': candidates,
                                          'status': 'check_next_turn' if candidates else 'no_match'}
                        elif call['name'] == content_tools.READ and (rejection := offered_argument_error(
                            call['name'], args, read_parameters
                        )):
                            result = {'ok': False, **json.loads(rejection)}
                        elif call['name'] == content_tools.READ and set(args) == {'content_source'}:
                            result = {'ok': True, **await content_tools.read_saved_item(
                                runtime, handler.db, context, args['content_source'])}
                        elif call['name'] == content_tools.READ and set(args) == {'view_image'}:
                            if len(viewed_images) >= content_tools.visual_budget_remaining(prepared.messages):
                                raise ValueError('CHAT_IMAGE_VISUAL_BUDGET_EXHAUSTED')
                            image, url = await asyncio.to_thread(content_tools.read_image_view,
                                handler, request, context, args['view_image'])
                            viewed_images.append(url)
                            result = {'ok': True, **image}
                        elif call['name'] == content_tools.READ and set(args) == {'show_image'}:
                            from services.task_execution.request_resolution import check_display, recover_display_selection
                            if len(calls) == 1:
                                await recover_display_selection(runtime, request, prepared, args['show_image'])
                            check_display(runtime, request, args['show_image'])
                            image = await asyncio.to_thread(content_tools.show_image,
                                handler, request, context, args['show_image'])
                            if not any(block.get('source_message_id') == image['source_message_id']
                                       and block.get('source_content_index') == image['source_content_index']
                                       for block in blocks):
                                blocks.append(image)
                                delivered_images.append(image)
                            result = {'ok': True, 'status': 'delivered_existing', 'accepted': False,
                                      'message': '已展示已有图片。没有创建新的图片任务。'}
                        elif call['name'] == content_tools.READ and set(args) == {'image_task_status'}:
                            result = {'ok': True, **await asyncio.to_thread(content_tools.read_image_task_status,
                                runtime, handler.db, context, args['image_task_status'])}
                        elif call['name'] == content_tools.READ and set(args) == {'image_task_id'}:
                            result = {'ok': True, **await asyncio.to_thread(content_tools.read_image_recipe,
                                handler, request, context, args['image_task_id'])}
                        elif (call['name'] == content_tools.READ and args.get('list_images') is True
                              and set(args) <= {'message_id', 'list_images'}):
                            result = {'ok': True, **await asyncio.to_thread(content_tools.source_text,
                                handler.db, request.task_id, context, args)}
                        elif call['name'] == content_tools.READ and set(args) <= {'message_id', 'text_exact', 'text_exacts'}:
                            result = {'ok': True, **await asyncio.to_thread(content_tools.source_text,
                                handler.db, request.task_id, context, args)}
                            for part in result.get('messages', ()):
                                if not part.get('requires_explicit_read'):
                                    observed[f"{part['message_id']}:{part['content_index']}"] = {
                                        k: part[k] for k in ('sha256', 'context_revision')
                                    }
                        else:
                            raise ValueError('CHAT_CONTENT_ARGUMENTS_INVALID')
                    except (ValueError, TypeError, PermissionError, KeyError) as error:
                        code = str(error)
                        result = {'ok': False, 'code': code if code.startswith(('CHAT_', 'REQUEST_TARGET_')) else 'CHAT_CONTENT_UNAVAILABLE'}
                        if call['name'] == 'resolve_request_target':
                            from services.task_execution.request_resolution import selection_schema
                            result.update(parameters=selection_schema(runtime)['function']['parameters'],
                                message='按参数定义复制真实目标。source_sha256、root_task_id和输入版本由后台填写；继续最近需求用active_request，不把历史图片编号当成本轮附件。')
                        if result['code'] == 'CHAT_IMAGE_VISUAL_BUDGET_EXHAUSTED':
                            result['message'] = '本轮视觉预算已用完。现有定位仍可用，但不要声称看到了未加载的图。'
                        elif call['name'] == content_tools.READ:
                            result['message'] = '无法核对所选来源。请使用当前目录中的明确引用，仍有歧义时请用户选择。'
                output = json.dumps(result, ensure_ascii=False)
                prepared.messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': output})
                for block in blocks:
                    if block.get('tool_call_id') == call['id']:
                        block.update(status='completed' if result['ok'] else 'error', output=output)
            append_tool_images(prepared.messages, viewed_images)
            runtime.checkpoint_base[content_tools.STATE] = {
                **state, 'names': sorted(names), 'observed_history': dict(list(observed.items())[-32:]),
            }
            await runtime.safe_point(SafePoint.AFTER_TOOL, replay_payload=_build_replay_context(
                prepared.messages, blocks, turn, next_model_round=next_model_round,
                repeated_tool_call_guard=repeated_tool_call_guard))
            for block in blocks:
                if block.get('tool_call_id') in previewed_call_ids:
                    await sink.on_block_update(block)
            for image in delivered_images:
                await sink.on_block(image)
            return None
        from services.task_execution import request_resolution
        target_errors = {call['id']: error for call in calls
                         if (error := request_resolution.check(runtime, request, call)) is not None}
        if target_errors and not recovery_only and not content_output_registration:
            prepared.messages.append(assistant)
            for call in calls:
                error = target_errors.get(call['id'], ('REQUEST_TARGET_BATCH_BLOCKED', '本批目标未核实，未派发任何业务操作。'))
                result = {'ok': False, 'accepted': False, 'code': error[0], 'message': error[1]}
                if call['name'] == content_objects.SAVE:
                    from services.task_execution.content_delivery import finish_response
                    finish_response(runtime, None, result, request=request)
                output = json.dumps(result, ensure_ascii=False)
                prepared.messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': output})
                for block in blocks:
                    if block.get('tool_call_id') == call['id']:
                        block.update(status='error', output=output)
            await runtime.safe_point(SafePoint.AFTER_TOOL, replay_payload=_build_replay_context(
                prepared.messages, blocks, turn, next_model_round=next_model_round))
            for block in blocks:
                if block.get('tool_call_id') in previewed_call_ids:
                    await sink.on_block_update(block)
            return None
        original_owner = getattr(runtime, 'task_followup_candidate', None)
        if scoped_content and not recovery_only and not content_output_registration:
            from services.task_execution.direct_loop import select_content_owner
            await select_content_owner(runtime, calls)
            confirmation_state = runtime.task_execution_state or content_followup_state(runtime)
            for call in calls:
                gate = item_confirmation.check(call, confirmation_state, '', semantic=media,
                    allow_new=(content_tools.semantic_intent(request,runtime) in {'generate','edit','write_then_submit_images'}
                        or call['name'] in content_tools.requested_delivery_tools(request,runtime)))
                if gate is not None:
                    direct_errors[call['id']] = gate
                elif media is not None and call['name'] in item_confirmation.MEDIA:
                    item_confirmation.record_decision(runtime, request, confirmation_state, media)
        selected_content = (
            not recovery_only and getattr(prepared, 'content_controls_available', False)
            and scoped_content and not direct_errors and not repair_rejections
            and not (turn_text.strip() and any(c['name'] == content_objects.SAVE for c in calls))
            and can_resume_content_calls(runtime, calls, request, server_image_plan=server_image_plan)
        )
        if not selected_content:
            runtime.task_followup_candidate = original_owner
        if selected_content or any(c['name']=='resume_task_execution' for c in calls):
            await resume_from_model(runtime,prepared,blocks,calls,assistant,turn=turn,
                model_round=next_model_round,build_checkpoint=_build_replay_context,selected_content=selected_content)
            return None
        if any(c['name'] == content_objects.SAVE for c in calls):
            content_save_started = time.monotonic()
            from services.task_execution.model_bridge import commit_projection
            from services.tools.catalog import build_capability_catalog
            from services.task_execution.contracts import TaskError
            candidate = runtime.task_execution_state
            if not content_output_registration:
                prepared.messages.append(assistant)
            saved_block = None
            for call in calls:
                result = {'ok':False,'code':'CONTENT_SAVE_BATCH_BARRIER'}
                if call['name'] == content_objects.SAVE and len(calls) == 1:
                    try:
                        if recovery_only or not scoped_content:
                            raise TaskError('CONTENT_SAVE_UNAVAILABLE')
                        if server_image_plan and not content_output_registration:
                            raise TaskError('ECOM_PLAN_SERVER_AUTHOR_REQUIRED')
                        save_value = content_objects.SaveContent.model_validate_json(call['arguments'])
                        if media.approved and media.confirmation_only:
                            raise TaskError('CONTENT_CONFIRMATION_EXISTING_ITEM_REQUIRED')
                        conditions = save_value.confirmation or media
                        wait_all = conditions.wait_all
                        saved_confirmation_items = confirmation_items
                        if conditions is not None:
                            saved_confirmation_items = tuple(conditions.wait_for)
                        if wait_all and save_value.document is not None:
                            saved_confirmation_items = tuple(range(1, len(save_value.document.items) + 1))
                        save_call = call
                        # Mixed immediate/waiting delivery is expressed by the same
                        # structured save (continue_execution), never guessed from prose.
                        from services.task_execution.content_delivery import KEY as delivery_key, input_revision
                        delivery_record = runtime.checkpoint_base.get(delivery_key) or {}
                        visible_draft = (delivery_record.get('visible_draft') if delivery_record.get('input_revision')
                                         == input_revision(runtime, request) else None)
                        if not content_tools.semantic_runtime(runtime) and visible_draft and (save_value.document is None
                                or visible_draft not in content_objects.render(save_value.document)):
                            raise TaskError(content_objects.DRAFT_CONFLICT)
                        visible_prefix = content_objects.matches_visible_prefix(save_value, turn_text)
                        # New turns keep assistant commentary and the published
                        # document as separate visible blocks. Only the document
                        # is registered as executable text; neither is replaced.
                        # Old replay proposals retain their original contract.
                        if (not content_tools.semantic_runtime(runtime) and turn_text.strip() and not visible_prefix
                                and (not save_value.continue_execution or save_value.show_content)):
                            raise TaskError(content_objects.DRAFT_CONFLICT)
                        related_state = candidate or content_followup_state(runtime)
                        if (save_value.document is not None and not server_image_plan
                                and content_tools.selects_previous_item(request, runtime)):
                            raise TaskError('CONTENT_EXECUTION_SOURCE_REQUIRED')
                        if (save_value.document is not None and save_value.continue_execution
                                and not save_value.new_content and not server_image_plan):
                            existing = candidate is None and bool(content_objects.objects(related_state))
                            if (not existing and not current_images
                                    and (getattr(request.context_anchor, 'base_revision', None) or 0) > 0):
                                existing = await asyncio.to_thread(content_tools.historical_image_source,
                                    handler.db, request, prepared.execution_context)
                            if existing:
                                raise TaskError('CONTENT_EXECUTION_SOURCE_REQUIRED')
                        checked_message = None
                        if save_value.message_source is not None:
                            if not getattr(prepared, 'content_controls_available', False) or server_image_plan:
                                raise TaskError('CONTENT_MESSAGE_SOURCE_UNAVAILABLE')
                            checked_message = await asyncio.to_thread(content_tools.prepare_message_content, handler.db,
                                request.task_id, prepared.execution_context, save_value.message_source,
                                content_tools.checkpoint_state(runtime).get('observed_history', {}))
                            if candidate is None and content_objects.message_artifact(related_state, checked_message[2]):
                                raise TaskError('CONTENT_MESSAGE_SOURCE_ALREADY_REGISTERED')
                        registration_images=content_objects.registration_source_images(request,runtime)
                        candidate, artifact, document, value = content_objects.save(
                            runtime, save_call, build_capability_catalog(),
                            has_image_attachments=bool(registration_images),
                            source_images=registration_images,
                            checked_message=checked_message, display_gaps=indexed_gaps,
                            author_request=content_tools.visual_author_request(request, content_output_registration, runtime),
                            allow_implicit_execution_materials=server_image_plan,
                            confirmation_items=saved_confirmation_items,
                            request_context=(getattr(getattr(getattr(prepared,'ecom_workflow',None),'value',None),'request_context',None)
                                or content_tools.semantic_context(request, runtime)),
                            confirmation_tool=conditions.tool
                                if saved_confirmation_items else None,
                        )
                        result = {'ok':True,'object':next(row for row in content_objects.catalog(candidate, for_model=True)
                                                         if row['artifact_id']==artifact.id)}
                        if not value.continue_execution or value.show_content:
                            record = content_objects.ContentRecord.model_validate_json(artifact.inline_text)
                            saved_block = {'type': 'text', 'text': content_objects.render(document, record.display_gaps),
                                           'content_artifact_id': artifact.id, 'content_delivery_final': value.delivery_complete and not value.continue_execution}
                    except (ValueError,TypeError,KeyError,PermissionError) as error:
                        code = str(error)
                        result = {'ok':False,'code':code if isinstance(error,TaskError) or code.startswith(
                            ('CONTENT_MESSAGE_SOURCE_', 'CHAT_SOURCE_', 'CHAT_INPUT_RANGE_')) else content_objects.INVALID_OBJECT}
                        if result['code'] == 'CONTENT_CONFIRMATION_EXISTING_ITEM_REQUIRED':
                            result['message'] = '用户确认的是已保存的条目。请读取该条目并按原内容执行。'
                        if result['code'] == 'ECOM_PLAN_SERVER_AUTHOR_REQUIRED':
                            result['message'] = ('当前电商工作流由 plan_ecommerce_images 生成并保存方案；'
                                '请调用该工具，完整正文与可执行编号由后台交付，无需另行登记普通文本。')
                        if result['code'] == 'CONTENT_CONFIRMATION_ITEMS_REQUIRED':
                            result['message'] = '请按用户编号保存整组内容，包括尚未确认的条目，保留每条实际编号和顺序。'
                        if result['code'] == content_objects.DRAFT_CONFLICT:
                            result['message'] = (
                                '上一条回复同时含聊天草稿和登记内容，尚未保存。请重新独立登记，'
                                '把草稿中原版可见结构放进对应display_prefix，每条完整执行正文和限制放进body，'
                                '按原条目保留素材和规格。不要再输出聊天正文。'
                            )
                        if result['code'] == content_objects.ATTACHMENT_USAGE_REQUIRED:
                            result.update(
                                stage='validate', recovery={'action': 'repair_arguments'},
                                issues=[{'path': f'$.document.items[{index}].materials', 'reason': '缺少素材用途'}
                                        for index, item in enumerate(save_value.document.items) if not item.materials][:16],
                                references=content_tools.material_candidates(current_images),
                                message='请补全缺失条目的 materials。' + content_objects.MATERIAL_GUIDANCE
                                        + '保留完整正文和规格。素材用途不明确时先询问。',
                            )
                            if result['references']:
                                result['material_example'] = {
                                    'reference': result['references'][0], 'role': '编辑底图', 'usage': 'input',
                                }
                        if result['code'] == 'CONTENT_IMAGE_OPTIONS_INVALID':
                            from services.handlers.image_size_requirements import SIZE_GUIDANCE
                            result.update(stage='validate', recovery={'action': 'repair_arguments'}, message=SIZE_GUIDANCE)
                        if result['code'] == content_objects.PRESENTATION_INCOMPLETE:
                            from services.task_execution.methods import REFERENCE_PROMPTS_PRESENTATION
                            result['message'] = REFERENCE_PROMPTS_PRESENTATION
                        if result['code'] in {'CONTENT_EXECUTION_SOURCE_REQUIRED', 'CONTENT_MESSAGE_SOURCE_ALREADY_REGISTERED'}:
                            result['message'] = (
                                '执行已有方案请从成果目录选择；尚无成果时，读取并登记原消息整组。'
                                '只有本轮明确独立制作才用 document 和 new_content=true。'
                            )
                        if result['code'] in {'CONTENT_OBJECT_TOO_LARGE', 'TASK_INLINE_ARTIFACT_BUDGET'}:
                            result.update(stage='save', recovery={'action': 'retain_draft'},
                                message='完整原稿已保留在本轮记录中。保留各条原文，按容量分次保存，'
                                        '前几次delivery_complete=false，全部写完最后一次才为true。'
                                        '不要把继续写稿改为continue_execution=true；任务总预算不足时明确说明未完成。')
                        if result['code'] == content_objects.INVALID_OBJECT:
                            result.update(stage='validate', recovery={'action': 'repair_arguments'})
                            result['message'] = (
                                '按当前工具定义提交有效 JSON。新建用 document（标题及条目的标题和完整正文），'
                                '修订用 base 和 updates。'
                            )
                        if hasattr(error,'errors'):
                            result['issues']=error.errors(include_input=False,include_url=False,include_context=False)[:12]
                            if any('CONTENT_NEW_SOURCE_INVALID' in issue.get('msg', '') for issue in result['issues']):
                                result['message'] = ('本轮仅交付文字时必须删除 new_content 字段；它只用于明确获准的后续执行。'
                                                     '保持 document 的全部原文不变，重新登记；不要改成 continue_execution=true。')
                            if any('materials' in issue['loc'] for issue in result['issues']):
                                result['message'] += content_objects.MATERIAL_GUIDANCE
                        from services.task_execution.image_reference_catalog import for_runtime
                        references = for_runtime(runtime)
                        if references is not None and result['code'] == content_objects.INVALID_OBJECT and not content_output_registration:
                            offered = getattr(prepared, 'content_visible_tools', {}).get(content_objects.SAVE)
                            references.save_repair(result, offered)
                if not content_output_registration:
                    prepared.messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result,ensure_ascii=False)})
                blocks[:] = [b for b in blocks if b.get('tool_call_id') != call['id']]
            if saved_block is not None:
                prefix = save_value.document.display_prefix if save_value.document is not None else None
                text_index = next((i for i in range(len(blocks) - 1, -1, -1)
                                   if blocks[i].get('type') == 'text'), None)
                replaced = blocks[text_index].get('text') if text_index is not None else None
                if replaced and replaced == prefix and not blocks[text_index].get('content_artifact_id'):
                    # Explicit prefix identity is independent of intervening
                    # thinking/status blocks and survives a corrective round.
                    saved_block['content_replaces_text'] = replaced
                    blocks.pop(text_index)
                    if totals.text.endswith(replaced):
                        totals.text = totals.text[:-len(replaced)]
                blocks.append(saved_block)
                if content_output_registration:
                    assistant.pop('tool_calls', None)
                    prepared.messages.append({**assistant, 'content': saved_block['text']})
            from services.task_execution.content_delivery import finish_response
            finish_response(runtime, saved_block, result, request=request,
                delivery_complete=value.delivery_complete if result.get('ok') else True,
                source='visible_text' if content_output_registration else 'tool')
            original_skills=runtime.skill_runtime
            runtime.skill_runtime=retire_saved_image_method(runtime,candidate)
            await commit_projection(runtime=runtime,candidate=candidate,prepared=prepared,blocks=blocks,
                point=SafePoint.AFTER_TOOL,build_checkpoint=_build_replay_context,turn=turn,
                next_model_round=next_model_round,repeated_tool_call_guard=repeated_tool_call_guard)
            if runtime.skill_runtime is not original_skills:
                _apply_skill_context(prepared,runtime.skill_runtime)
            from services.agent.observability.chat_lifecycle import chat_phase
            chat_phase('content.registration.end', task_id=request.task_id,
                       elapsed_ms=(time.monotonic() - content_save_started) * 1000, metrics={'ok': result.get('ok') is True})
            if saved_block is not None:
                if not indexed_gaps:
                    totals.text += saved_block['text']
                await sink.on_block(saved_block)
                chat_phase('content.published', task_id=request.task_id,
                           elapsed_ms=(time.monotonic() - content_save_started) * 1000,
                           metrics={'rendered_bytes': len(saved_block['text'].encode('utf-8'))})
            return None
        if (any(c['name'] == SAVE_IMAGE_PLAN for c in calls)
                and not any(c['name'] in SKILL_CONTROL_TOOLS for c in calls)):
            from services.task_execution.model_bridge import commit_projection
            from services.tools.catalog import build_capability_catalog
            if not recovery_only:
                prepared.messages.append(assistant)
                for call in calls:
                    prepared.messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps({
                        'ok': False, 'code': 'CONTENT_SAVE_PATH_RETIRED', 'tool': content_objects.SAVE,
                    }, ensure_ascii=False)})
                await runtime.safe_point(SafePoint.AFTER_TOOL,replay_payload=_build_replay_context(prepared.messages,blocks,turn,next_model_round=next_model_round))
                return None
            candidate = stage_image_plan_batch(runtime, prepared, blocks, calls, assistant, build_capability_catalog(),
                assistant_already_present=assistant_already_present)
            original_skills = runtime.skill_runtime
            runtime.skill_runtime = retire_saved_image_method(runtime, candidate)
            await commit_projection(runtime=runtime, candidate=candidate, prepared=prepared, blocks=blocks,
                point=SafePoint.AFTER_TOOL, build_checkpoint=_build_replay_context, turn=turn,
                next_model_round=next_model_round, repeated_tool_call_guard=repeated_tool_call_guard)
            if runtime.skill_runtime is not original_skills:
                _apply_skill_context(prepared, runtime.skill_runtime)
            for block in blocks:
                if block.get('tool_call_id') in previewed_call_ids:
                    await sink.on_block_update(block)
            return None
        calls,staged=stage_plan_calls(runtime,prepared,blocks,calls,assistant)
        assistant_already_present=assistant_already_present or staged
        planned_marker = None
        if staged:
            from services.task_execution.progress import plan_marker
            planned_marker = plan_marker(blocks, runtime)
            if planned_marker is not None and calls:
                if repair_batch:
                    repair_batch.record(runtime, prepared.messages, blocks)
                await runtime.safe_point(SafePoint.BEFORE_TOOL, replay_payload=_build_replay_context(
                    prepared.messages, blocks, turn, next_model_round=next_model_round,
                    repeated_tool_call_guard=repeated_tool_call_guard))
                await sink.on_block(planned_marker)
        if not calls:
            await runtime.safe_point(SafePoint.AFTER_TOOL,replay_payload=_build_replay_context(
                prepared.messages,blocks,turn,next_model_round=next_model_round,
                repeated_tool_call_guard=repeated_tool_call_guard))
            if planned_marker is not None:await sink.on_block(planned_marker)
            return None
        from services.tools.model_contracts import REVIEWED_MODEL_TOOLS, decode_model_arguments, offered_argument_error

        projected_calls = []
        media_slots = {}
        for call in calls:
            if call['name'] == 'generate_image' and content_tools.image_writing_only_request(request, runtime):
                direct_errors[call['id']] = ('IMAGE_GENERATION_NOT_REQUESTED',
                    '本轮用户只要求文字策划、方案或提示词，未创建图片任务。请交付完整文字，等待用户明确要求出图。')
                projected_calls.append(call)
                continue
            if call['id'] in repair_rejections or call['id'] in direct_errors:
                projected_calls.append(call)
                continue
            if inline_calls:
                from services.tools.argument_validation import decode_tool_arguments
                from services.tools.catalog import build_capability_catalog

                record = inline_image_action.read_record(runtime)
                if recovery_only and (record is None or record.call_id != call['id']):
                    raise ConversationStopRequested('inline_image_checkpoint_unconfirmed')
                inline_candidate = None
                try:
                    draft_args = decode_tool_arguments(call['arguments'])
                    if record and record.call_id == call['id']:
                        reference, started = inline_image_action.restore(runtime, record, call, draft_args)
                        if not started:
                            if record.input_sha256 != inline_image_action.input_version(runtime, request):
                                raise ValueError('CHAT_INLINE_IMAGE_INPUT_CHANGED')
                            recovery_only = False  # Proven prepared with no native reservation.
                    else:
                        offer = getattr(prepared, 'content_visible_tools', {}).get(call['name'], {})
                        properties = offer.get('parameters', {}).get('properties', {})
                        input_type = inline_image_action.request_type(properties, draft_args)
                        if (not scoped_content or input_type is None
                                or not inline_image_action.allowed_for_offer(runtime, server_image_plan=server_image_plan,
                                    reuse_source=content_tools.selects_previous_item(request, runtime))):
                            raise PermissionError('CHAT_INLINE_IMAGE_UNAVAILABLE')
                        version = inline_image_action.input_version(runtime, request)
                        if record and record.input_sha256 == version:
                            raise ValueError('CHAT_INLINE_IMAGE_ALREADY_PREPARED')
                        inline_candidate, record = await inline_image_action.prepare(
                            runtime, handler, request, prepared, call, draft_args, build_capability_catalog(), version=version)
                        reference = record.reference.model_dump(mode='json', exclude_none=True)
                except (ValueError, TypeError, PermissionError, KeyError, OSError, RecursionError) as error:
                    if recovery_only:
                        raise ConversationStopRequested('inline_image_checkpoint_unconfirmed') from None
                    code = str(error)
                    from services.handlers.image_size_requirements import ImageSizeError
                    from services.task_execution.contracts import TaskError
                    trusted_code = isinstance(error, TaskError) and re.fullmatch(r'[A-Z][A-Z0-9_]{1,100}', code)
                    code = code if isinstance(error, ImageSizeError) or trusted_code or code.startswith('CHAT_INLINE_IMAGE_') else 'CHAT_INLINE_IMAGE_INPUT_INVALID'
                    guidance = getattr(error, 'guidance', None) or '本次没有派发。请按本轮工具定义核对完整要求、素材和输出规格。'
                    if code == 'CHAT_INLINE_IMAGE_ALREADY_PREPARED':
                        guidance = '已记录本轮的图片动作。请查看原任务状态。多张图请使用分条方案。'
                    if getattr(error, 'issues', None):
                        guidance += ' ' + '；'.join(f"{issue['path']} {issue['reason']}" for issue in error.issues[:4])
                    direct_errors[call['id']] = (code, code + '：' + guidance)
                    projected_calls.append(call)
                    continue
                if inline_candidate is not None:
                    if not assistant_already_present:
                        prepared.messages.append(assistant)
                        assistant_already_present = True
                    from services.task_execution.model_bridge import commit_projection
                    await commit_projection(
                        runtime=runtime, candidate=inline_candidate, prepared=prepared, blocks=blocks,
                        point=SafePoint.BEFORE_TOOL, build_checkpoint=_build_replay_context, turn=turn,
                        next_model_round=next_model_round, repeated_tool_call_guard=repeated_tool_call_guard,
                        checkpoint_overlay=lambda: {inline_image_action.KEY: record.model_dump(mode='json')})
                args = {'content_source': reference}
            else:
                args = None
            check_format = (call['name'] in REVIEWED_MODEL_TOOLS
                            or (scoped_content and not server_image_plan and call['name'] == 'generate_image'))
            try:
                if not inline_calls:
                    if not recovery_only and check_format:
                        args = decode_model_arguments(call['name'], call.get('arguments'))
                    else:
                        args = json.loads(call.get('arguments') or '{}')
            except (ValueError, TypeError) as error:
                if not recovery_only and check_format:
                    direct_errors[call['id']] = ('MODEL_TOOL_ARGUMENTS_INVALID', str(error))
                    projected_calls.append(call)
                    continue
                args = None
            content_state = content_tools.checkpoint_state(runtime)
            saved_binding = content_state.get('bindings', {}).get(call['id'], {})
            if isinstance(args,dict) and saved_binding.get('kind') == 'content_object' and 'content_source' not in args:
                from services.task_execution.reducer import digest
                if digest(args) == saved_binding.get('arguments_sha256'):
                    args = {**saved_binding['input_arguments'], 'content_source':saved_binding['reference']}
            if (recovery_only and saved_binding.get('kind')=='input_mapping'
                    and isinstance(args,dict)):
                from services.task_execution.reducer import digest
                if digest(args)==saved_binding.get('arguments_sha256'):
                    from services.handlers.chat_input_handoff import rebind_mapping
                    original=saved_binding['input_arguments']
                    resolved,source=await asyncio.to_thread(rebind_mapping,handler.db,
                        request.task_id,prepared.execution_context,call['name'],original,saved_binding['mapping'])
                    bound_sources[call['id']]=(original,source,resolved)
                    projected_calls.append({**call,'arguments':json.dumps(resolved,ensure_ascii=False)})
                    continue
            try:
                if not recovery_only and isinstance(args, dict):
                    untrusted_proof = args.get('source_prompt')
                    if isinstance(untrusted_proof, dict) and 'content_object' in untrusted_proof:
                        raise ValueError('CONTENT_SOURCE_REQUIRED')
                if not recovery_only and not use_repair_contract and call['name'] in REVIEWED_MODEL_TOOLS:
                    offer = getattr(prepared, 'content_visible_tools', {}).get(call['name'])
                    if offer:
                        error = offered_argument_error(call['name'], args, offer['parameters'])
                        if error:
                            direct_errors[call['id']] = ('MODEL_TOOL_ARGUMENTS_INVALID', error)
                            projected_calls.append(call)
                            continue
                if scoped_content and (not recovery_only or call['name'] == 'image_agent'):
                    content_tools.validate_model_input(call['name'], args, server_image_plan=server_image_plan)
                if isinstance(args,dict) and 'content_source' in args:
                    if not scoped_content or not (recovery_only or inline_calls or getattr(prepared, 'content_controls_available', False)):
                        raise PermissionError('CONTENT_SOURCE_DENIED')
                    original = dict(args); reference = original.pop('content_source')
                    if call['name'] == 'generate_image' and not inline_calls:
                        if recovery_only:
                            variant = saved_binding.get('input_arguments', {}).get('variant_id')
                            if variant is not None:
                                original['variant_id'] = variant
                        else:
                            reference, action = content_tools.bind_image_selection(
                                runtime, request, runtime.task_execution_state, reference)
                            original.update(action)
                    resolved, source = content_objects.compile_input(
                        runtime.task_execution_state, call['name'], original, reference,
                        allow_old=recovery_only and not inline_calls)
                    bound_sources[call['id']] = (original,source,resolved)
                    slot = item_confirmation.slot(runtime.task_execution_state, call['name'], source['reference'])
                    if slot is not None:
                        media_slots[call['id']] = slot
                        item_confirmation.record_slot(runtime, runtime.task_execution_state, call, source['reference'])
                    projected_calls.append({**call,'arguments':json.dumps(resolved,ensure_ascii=False)})
                    continue
                projected_calls.append(call)
            except (ValueError, TypeError, PermissionError, KeyError) as error:
                code = str(error)
                code = code if code.startswith(('CHAT_INPUT_', 'CHAT_SOURCE_', 'CONTENT_', 'ECOM_PLAN_')) else 'CHAT_INPUT_MAPPING_INVALID'
                guidance = {
                    'CONTENT_IMAGE_REGENERATION_UNCONFIRMED': '未提交新的图片任务。请明确要重新生成哪个方案，或要查看哪张已有图片。',
                    'CONTENT_VIDEO_OPTIONS_UNSUPPORTED': '本次未提交视频。请检查该条 options，当前聊天视频工具固定采用横屏及默认参数。用户要求不同规格时请确认；其余镜头设想写入正文。',
                    'CONTENT_MATERIAL_NOT_SUPPORTED_BY_TOOL': '本次未派发。当前视频工具只接收文字脚本。素材仅作创作参考时，请修订该条，把素材用途设为 analysis 并保留正文后再提交；用户要求将原图作为视频输入时，请说明能力限制。',
                }.get(code, '本次未派发，输入核验失败：' + code)
                direct_errors[call['id']] = (code, guidance)
                projected_calls.append(call)
        calls = projected_calls
        visible = getattr(prepared, 'content_visible_tool_names', None)
        if not recovery_only and visible is not None:
            for call in calls:
                if call['name'] == 'generate_video' and call['name'] not in visible and call['id'] not in direct_errors:
                    direct_errors[call['id']] = ('CHAT_TOOL_DISCOVERY_REQUIRED',
                        '请先查找可用的视频工具并按下一轮的实际定义调用。')
        if use_repair_contract and not recovery_only:
            repair_batch = tool_argument_repairs.Batch.with_sources(
                runtime, request, calls, direct_errors, repair_batch)
            repair_rejections = repair_batch.rejections if repair_batch else {}
        if bound_sources:
            state = content_tools.checkpoint_state(runtime)
            proofs = dict(state.get('bindings', {}))
            from services.task_execution.reducer import digest
            for call_id, (_args, source, resolved) in bound_sources.items():
                proof = {k: v for k, v in source.items() if k != 'text'}
                proof['arguments_sha256'] = digest(resolved)
                if call_id in proofs and proofs[call_id] != proof:
                    direct_errors[call_id] = ('CHAT_SOURCE_CHANGED', '引用或参数与已保存的调用不一致。')
                else:
                    proofs[call_id] = proof
            runtime.checkpoint_base[content_tools.STATE] = {**state, 'bindings': proofs}
        if not any(c['name'] in SKILL_CONTROL_TOOLS or c['name'] in TASK_CONTROL_TOOLS for c in calls):
            from services.tools.catalog import build_capability_catalog
            candidate, native_errors = stage_native_calls(runtime, prepared,
                [call for call in calls if call['id'] not in direct_errors and call['id'] not in repair_rejections], build_capability_catalog(),
                server_image_plan=server_image_plan, media_slots=media_slots)
            direct_errors.update(native_errors)
            if candidate is not runtime.task_execution_state:
                if not assistant_already_present:
                    prepared.messages.append(assistant);assistant_already_present=True
                if repair_batch:
                    repair_batch.record(runtime, prepared.messages, blocks)
                from services.task_execution.model_bridge import commit_projection
                from services.task_execution.progress import marker
                progress_marker=marker(blocks,candidate,plan_id=runtime.task_direct_plan_id)
                await commit_projection(runtime=runtime,candidate=candidate,prepared=prepared,blocks=blocks,
                    point=SafePoint.BEFORE_TOOL,build_checkpoint=_build_replay_context,turn=turn,
                    next_model_round=next_model_round,repeated_tool_call_guard=repeated_tool_call_guard)
                if progress_marker is not None:await sink.on_block(progress_marker)
    # Exact native reuse takes place before effect reservation and charging.
    # Keep the original assistant tool call: its real result explains reuse.
    source_calls=calls
    if (runtime and not direct_enabled(runtime) and runtime.task_execution_state is not None and runtime.task_execution_state.image_binding_version>=3
            and runtime.task_execution_state.execution_identity.context_scope=='user' and not revision_blocked(runtime) and not pending_review(runtime) and not coverage_blocked(runtime)
            and hasattr(getattr(runtime,'task_artifact_reader',None),'reusable_images')):
        from services.task_execution.image_reuse import reuse,ReuseImageInput
        from services.task_execution.contracts import TaskError
        routed=[]
        for call in calls:
            mapped=call
            if call['name']=='generate_image':
                from services.tools.argument_validation import decode_image_arguments
                try:args=decode_image_arguments(call.get('arguments',''))
                except (ValueError,TypeError):args=None
                if isinstance(args,dict):
                    value=ReuseImageInput(expected_revision=runtime.task_execution_state.revision,arguments=args)
                    route=True
                    try:
                        await reuse(runtime.task_execution_state,value,reader=runtime.task_artifact_reader,
                            resolver=runtime.task_image_resource_resolver,owner_task_id=runtime.task_id)
                    except TaskError as error:
                        route=str(error) not in {'TASK_REUSABLE_IMAGE_NOT_FOUND','TASK_IMAGE_REUSE_REGENERATION_REQUEST','TASK_IMAGE_REUSE_SIZE_UNRESOLVED','IMAGE_TOOL_ARGUMENTS_INVALID'}
                    # A denied/unverifiable existing output is not permission to
                    # buy a replacement silently. Return its real control error.
                    if route:mapped={**call,'name':'reuse_task_image','arguments':value.model_dump_json()}
            routed.append(mapped)
        calls=routed
    if (runtime and (runtime.task_execution_state is not None or runtime.task_bootstrap is not None)
            and (any(call['name'] in TASK_CONTROL_TOOLS for call in calls) or coverage_blocked(runtime) or pending_review(runtime) or (revision_blocked(runtime) and not (
                phase_repair(runtime)
                and all(c['name'] in SKILL_CONTROL_TOOLS for c in calls))))):
        assistant = _assistant_tool_message(turn_text, source_calls)
        if getattr(prepared, 'last_provider_output', None):
            assistant['_dashscope_output'] = prepared.last_provider_output
        if getattr(prepared, 'last_reasoning_content', ''):
            assistant['reasoning_content'] = prepared.last_reasoning_content
        await execute_control_batch(prepared=prepared, runtime=runtime, calls=calls,
            assistant=assistant, blocks=blocks, turn=turn, next_model_round=next_model_round,
            build_checkpoint=_build_replay_context, sink=sink,
            repeated_tool_call_guard=repeated_tool_call_guard,assistant_already_present=assistant_already_present)
        return None
    if runtime and any(call['name'] in SKILL_CONTROL_TOOLS for call in calls):
        # Honor queued cancellation before resource IO, without checkpointing
        # an assistant whose control results do not exist yet.
        runtime.set_state(ConversationState.WAITING_TOOL)
        await runtime.safe_point(SafePoint.BEFORE_TOOL)
        # Method controls read pinned bodies but dispatch no
        # business IO. Save the complete batch, rather than a pending assistant
        # tool_calls checkpoint which a resumed model cannot consume safely.
        import copy
        saved_messages, saved_blocks = copy.deepcopy(prepared.messages), copy.deepcopy(blocks)
        sequence = runtime.replay_save_sequence
        assistant = _assistant_tool_message(turn_text, calls)
        if getattr(prepared, 'last_provider_output', None):
            assistant['_dashscope_output'] = prepared.last_provider_output
        if getattr(prepared, 'last_reasoning_content', ''):
            assistant['reasoning_content'] = prepared.last_reasoning_content
        if not assistant_already_present:prepared.messages.append(assistant)
        for call in calls:
            if call['name'] not in SKILL_CONTROL_TOOLS:
                blocks.append(build_running_step(call))
        try:
            await _execute_skill_batch(prepared=prepared, calls=calls, runtime=runtime,
                blocks=blocks, sink=sink, start_times={}, turn=turn,
                next_model_round=next_model_round, repeated_tool_call_guard=repeated_tool_call_guard)
        except BaseException:
            if runtime.replay_save_sequence == sequence:
                prepared.messages[:] = saved_messages
                blocks[:] = saved_blocks
            raise
        return None
    coordination_error = None
    task_blocked_names = frozenset()
    plan_blockers = {}
    image_operations, image_binding_errors = None, {}
    if runtime and (runtime.task_execution_state is not None or runtime.task_bootstrap is not None):
        from services.task_execution.methods import blocked_tool_names
        from services.tools.catalog import build_capability_catalog
        from services.task_execution.dispatch import plan_dispatch_blockers
        catalog = build_capability_catalog()
        if direct_enabled(runtime):
            from services.task_execution.direct_loop import bind_image_recipes
            candidate,recipe_errors=await bind_image_recipes(runtime,
                [c for c in calls if c['id'] not in direct_errors],catalog,server_image_plan=server_image_plan)
            direct_errors.update(recipe_errors)
            if candidate is not runtime.task_execution_state:
                from services.task_execution.model_bridge import commit_projection
                await commit_projection(runtime=runtime,candidate=candidate,prepared=prepared,blocks=blocks,
                    point=SafePoint.BEFORE_TOOL,build_checkpoint=_build_replay_context,turn=turn,
                    next_model_round=next_model_round,repeated_tool_call_guard=repeated_tool_call_guard)
            from services.task_execution.direct_loop import duplicate_image_plan_items, reuse_images
            candidate,direct_reused,reused_parts,reuse_errors=await reuse_images(runtime,
                [c for c in calls if c['id'] not in direct_errors])
            direct_errors.update(reuse_errors)
            if candidate is not runtime.task_execution_state:
                blocks.extend(reused_parts)
                await commit_projection(runtime=runtime,candidate=candidate,prepared=prepared,blocks=blocks,
                    point=SafePoint.BEFORE_TOOL,build_checkpoint=_build_replay_context,turn=turn,
                    next_model_round=next_model_round,repeated_tool_call_guard=repeated_tool_call_guard)
                for part in reused_parts:await sink.on_block(part)
            reused_ids = {result[0]['id'] for result in direct_reused}
            direct_errors.update(duplicate_image_plan_items(runtime.task_execution_state,
                [call for call in calls if call['id'] not in direct_errors and call['id'] not in reused_ids]))
        if not direct_enabled(runtime):
            task_blocked_names = blocked_tool_names(runtime.task_execution_state, catalog, runtime.skill_runtime)
            plan_blockers = plan_dispatch_blockers(runtime.task_execution_state, catalog)
        from services.task_execution.image_operations import prepare_image_operations
        image_operations, image_binding_errors = prepare_image_operations(runtime,
            [c for c in calls if c['name'] not in task_blocked_names and c['name'] not in plan_blockers and c['id'] not in direct_errors
                and c['id'] not in repair_rejections and c['id'] not in {r[0]['id'] for r in direct_reused}], catalog)
        image_binding_errors.update(direct_errors)
    if (runtime and runtime.skill_runtime is not None and runtime.skill_runtime.checkpoint_version == 2
            and not any(c['name'] in SKILL_CONTROL_TOOLS for c in calls)):
        from services.skills.contracts import SkillError
        try:
            runtime.skill_runtime.prepare_tool_action([c for c in calls
                if c['name'] not in task_blocked_names and c['name'] not in plan_blockers
                and c['id'] not in image_binding_errors and c['id'] not in repair_rejections])
            _apply_skill_context(prepared, runtime.skill_runtime)
        except SkillError as exc:
            coordination_error = control_result(str(exc), state_unchanged=True,
                conflicting_skills=list(getattr(exc, 'keys', ())))
    assistant = _assistant_tool_message(turn_text, calls)
    if getattr(prepared, "last_provider_output", None):
        assistant["_dashscope_output"] = prepared.last_provider_output
    if getattr(prepared, "last_reasoning_content", ""):
        assistant["reasoning_content"] = prepared.last_reasoning_content
    if not assistant_already_present:
        prepared.messages.append(assistant)
    await _sink_tool_calls(sink, [c for c in calls if c["name"] not in SKILL_CONTROL_TOOLS and c["name"] != SAVE_IMAGE_PLAN], turn + 1)
    start_times: dict[str, float] = {}
    for call in calls:
        if call["name"] in SKILL_CONTROL_TOOLS or call["name"] == SAVE_IMAGE_PLAN:
            continue
        block = build_running_step(call)
        start_times[call["id"]] = time.monotonic()
        if call["id"] in previewed_call_ids:
            for index, existing in enumerate(blocks):
                if existing.get("tool_call_id") == call["id"]:
                    blocks[index] = block
                    break
            else:
                blocks.append(block)
            await sink.on_block_update(block)
        else:
            blocks.append(block)
            await sink.on_block(block)
    if repair_batch:
        repair_batch.record(runtime, prepared.messages, blocks)
    guard = getattr(prepared, "image_arguments", None)
    recorded_recovery_ids = ({op.invocation_id for op in runtime.task_execution_state.operations
        if op.tool_name == 'generate_image' and str(op.owner_task_id) == str(runtime.task_id)}
        if recovery_only and runtime and runtime.task_execution_state is not None else set())
    if coordination_error:
        from services.agent.agent_result import AgentResult
        message = skill_step(coordination_error, runtime.skill_runtime, step_id='action-coordination')['reason']
        ready_calls = []
        rejected = [(call, AgentResult(message, status='error', error_message=coordination_error['code']),
                     True, message) for call in calls]
    else:
        if guard and recorded_recovery_ids:
            fresh, rejected = guard.filter_calls([c for c in calls if c['id'] not in recorded_recovery_ids])
            allowed = recorded_recovery_ids | {c['id'] for c in fresh}
            ready_calls = [c for c in calls if c['id'] in allowed]
        else:
            ready_calls, rejected = guard.filter_calls(calls) if guard else (calls, [])
    if repair_rejections:
        rejected = [item for item in rejected if item[0]['id'] not in repair_rejections]
        rejected.extend((call, repair_rejections[call['id']], True, tool_argument_repairs.display_rejection(repair_rejections[call['id']]))
                        for call in calls if call['id'] in repair_rejections)
        ready_calls = [call for call in ready_calls if call['id'] not in repair_rejections]
    if task_blocked_names:
        from services.agent.agent_result import AgentResult
        message = '请先完成并保存所选方法要求的完整规划和逐图提示词，再执行图片生成。'
        rejected.extend((call, AgentResult(message, status='error', error_message='TASK_PREREQUISITE_INCOMPLETE'),
                         True, message) for call in ready_calls if call['name'] in task_blocked_names)
        ready_calls = [call for call in ready_calls if call['name'] not in task_blocked_names]
    if plan_blockers:
        from services.agent.agent_result import AgentResult
        from services.task_execution.dispatch import blocker_message
        for call in ready_calls:
            code = plan_blockers.get(call['name'])
            if code:
                message = blocker_message(code)
                rejected.append((call, AgentResult(message, status='error', error_message=code), True, message))
        ready_calls = [call for call in ready_calls if call['name'] not in plan_blockers]
    if image_binding_errors:
        from services.agent.agent_result import AgentResult
        for call in ready_calls:
            if call['id'] in image_binding_errors:
                code, message = image_binding_errors[call['id']]
                rejected.append((call, AgentResult(message, status='error', error_message=code), True, message))
        ready_calls = [call for call in ready_calls if call['id'] not in image_binding_errors]
    if direct_reused:
        ready_calls=[c for c in ready_calls if c['id'] not in {r[0]['id'] for r in direct_reused}]
    ecom_arguments = getattr(prepared, "ecom_arguments", None)
    if ecom_arguments:
        ready_calls, ecom_rejected = ecom_arguments.filter_calls(ready_calls)
        rejected.extend(ecom_rejected)
        ecom_arguments.reserve(ready_calls)
        if ecom_arguments.state and ecom_arguments.state.get("dispatch_reserved") and runtime is None:
            raise RuntimeError("ECOM_ARGUMENT_CORRECTION_CHECKPOINT_REQUIRED")
    workflow = getattr(prepared, "ecom_workflow", None)
    if workflow and not any(c["name"] in SKILL_CONTROL_TOOLS for c in calls):
        barrier = await workflow.barrier(ready_calls)
        if barrier:
            from services.agent.agent_result import AgentResult
            message, terminal = barrier
            rejected.extend((call, AgentResult(message, status="error", error_message="ECOM_WORKFLOW_ENTRY_REQUIRED",
                metadata={"stop_workflow": terminal}), True, message) for call in ready_calls)
            ready_calls = []
            if terminal:
                handler._tool_result_stop_reason = message
    if guard:
        guard.reserve_dispatch([c for c in ready_calls if c['id'] not in recorded_recovery_ids])
        if guard.repair and guard.repair.get("dispatch_reserved") and runtime is None:
            raise RuntimeError("IMAGE_ARGUMENT_CORRECTION_CHECKPOINT_REQUIRED")
    if runtime:
        runtime.set_state(ConversationState.WAITING_TOOL)
        if (repair_batch or runtime.task_execution_state is not None or direct_enabled(runtime)
                or (runtime.skill_runtime is not None and runtime.skill_runtime.checkpoint_version == 2)
                or (guard and guard.repair and guard.repair.get("dispatch_reserved"))
                or (ecom_arguments and ecom_arguments.state and ecom_arguments.state.get("dispatch_reserved"))):
            # Commit the paid correction boundary before any business IO. A
            # restart stops here; it cannot change the batch index/call ID.
            await runtime.safe_point(SafePoint.BEFORE_TOOL, replay_payload=_build_replay_context(
                prepared.messages, blocks, turn, next_model_round=next_model_round,
                repeated_tool_call_guard=repeated_tool_call_guard,
            ))
        else:
            await runtime.safe_point(SafePoint.BEFORE_TOOL)
    await _check_cancelled(
        cancellation_event, request, prepared.messages, blocks,
        totals, "before_tool",
    )
    if any(call["name"] in SKILL_CONTROL_TOOLS for call in calls):
        await _execute_skill_batch(
            prepared=prepared, calls=calls, runtime=runtime, blocks=blocks,
            sink=sink, start_times=start_times, turn=turn,
            next_model_round=next_model_round, repeated_tool_call_guard=repeated_tool_call_guard,
        )
        guard = getattr(prepared, "image_arguments", None)
        if guard and guard.repair and guard.repair.get("used"):
            guard.observe([])
            handler._tool_result_stop_reason = guard.stop_message
        return None
    # Only this task's authenticated replay blocks supply browse provenance.
    # No new checkpoint fields and no parsing of model prose/tool output.
    handler._tool_selection_history_task_id = request.task_id
    handler._tool_selection_history = tuple(
        dict(block) for block in blocks
        if block.get("type") == "tool_step" and block.get("tool_name") == "file_search"
        and block.get("status") == "completed"
    )
    async def persist_workflow_binding(value):
        workflow = getattr(prepared, 'ecom_workflow', None)
        if workflow is None or runtime is None:
            raise RuntimeError('SKILL_COMPOSITION_OWNER_UNAVAILABLE')
        await workflow.commit_binding(value)
        await runtime.safe_point(SafePoint.BEFORE_TOOL, replay_payload=_build_replay_context(
            prepared.messages, blocks, turn, next_model_round=next_model_round,
            repeated_tool_call_guard=repeated_tool_call_guard))

    results = await handler._execute_tool_calls(
        ready_calls,
        request.task_id,
        request.conversation_id,
        request.message_id,
        request.user_id,
        turn + 1,
        messages=prepared.messages,
        budget=prepared.budget,
        cancellation_event=cancellation_event,
        permission_mode=prepared.permission.mode.value,
        agent_domain=prepared.execution_context.agent_domain,
        **({'task_image_operations': image_operations} if image_operations is not None else {}),
        **({'recovery_only': True} if recovery_only else {}),
        **({'chat_source_bindings': bound_sources} if direct_enabled(runtime) and bound_sources else {}),
        **({"authorized_tool_names": prepared.execution_context.authorized_tool_names,
            "image_skill_snapshot": tuple({"skill_key": active.skill_key, "revision": active.revision,
                "body_sha256": active.body_sha256, "rendered_sha256": active.rendered_sha256}
                for active in runtime.skill_runtime.active.values())}
           if runtime and runtime.skill_runtime is not None and runtime.skill_runtime.has_active_skills
           else {}),
        **({'image_composition': runtime.skill_runtime.checkpoint()['composition'],
            'image_binding_writer': persist_workflow_binding}
           if runtime and runtime.skill_runtime is not None and runtime.skill_runtime.checkpoint_version == 2 else {}),
    ) if ready_calls else []
    if rejected or direct_reused:
        by_call = {item[0]["id"]: item for item in [*results, *rejected,*direct_reused]}
        results = [by_call[call["id"]] for call in calls]
    if ecom_arguments:
        ecom_arguments.observe(results)
        if ecom_arguments.stop_message:
            handler._tool_result_stop_reason = ecom_arguments.stop_message
    for call, result, is_error, _display in results:
        if not is_error:
            continue
        code = str(getattr(getattr(result, 'error', None), 'message', None)
                   or getattr(result, 'error_message', None) or getattr(result, 'exception', ''))
        if code.startswith(('CHAT_INPUT_', 'CHAT_SOURCE_')):
            direct_errors.setdefault(call['id'], (code, '执行输入未通过核验，本次停止自动提交：' + code))
    if any(code.startswith(('CHAT_INPUT', 'CHAT_SOURCE', 'CONTENT_', 'IMAGE_SOURCE', 'IMAGE_ORIGINAL', 'IMAGE_REFERENCE',
                            'IMAGE_QUOTED', 'IMAGE_ASSET', 'IMAGE_CONTENT', 'IMAGE_LEGACY',
                            'RESOURCE_', 'TASK_IMAGE_INPUT', 'TASK_IMAGE_RESOURCE',
                            'IMAGE_SIZE', 'IMAGE_ASPECT', 'IMAGE_EXACT', 'IMAGE_CANVAS', 'IMAGE_DIMENSIONS',
                            'IMAGE_RESOLUTION')) and call_id not in repair_rejections
           for call_id, (code, _message) in direct_errors.items()):
        reasons = list(dict.fromkeys(message for _code, message in direct_errors.values()))
        handler._tool_result_stop_reason = '本轮已停止提交。' + '\n'.join(reasons)
        if direct_enabled(runtime):
            state = content_tools.checkpoint_state(runtime)
            runtime.checkpoint_base[content_tools.STATE] = {**state, 'stop_message': handler._tool_result_stop_reason}
    for call, result, is_error, _display in results:
        if (call["name"] == "plan_ecommerce_images" and is_error
                and getattr(result, "metadata", {}).get("stop_workflow") is True):
            handler._tool_result_stop_reason = getattr(result, "summary", None) or _display
    # Confirmed child placeholders are the final delivery; never synthesize
    # another acknowledgement. Errors and mixed tool batches keep their flow.
    accepted_images = [call['id'] for call, result, is_error, _ in results
                       if call['name'] == 'generate_image' and not is_error
                       and getattr(result, 'metadata', {}).get('accepted') is True
                       and getattr(result, 'metadata', {}).get('task_id')]
    native_receipts = {call['name'] for call, result, is_error, _ in results
                       if call['name'] in content_tools.MEDIA_TOOLS and not is_error
                       and getattr(result, 'metadata', {}).get('accepted') is True
                       and getattr(result, 'metadata', {}).get('task_id')}
    if native_receipts:
        prepared.delivery_receipts = set(getattr(prepared, 'delivery_receipts', ())) | native_receipts
        video_acceptance = dict(getattr(prepared, 'video_acceptance_by_task', {}))
        for call, result, is_error, _ in results:
            if call['name'] != 'generate_video' or is_error:
                continue
            kind = content_tools.video_receipt_kind(result)
            if kind is not None:
                task_id = result.metadata['task_id']
                if video_acceptance.get(task_id) != 'new':
                    video_acceptance[task_id] = kind
        prepared.video_acceptance_by_task = video_acceptance
    if any(call['name'] == 'generate_image' for call, *_ in results):
        prepared.image_receipt_seen = len(accepted_images) == sum(call['name'] == 'generate_image' for call, *_ in results)
    handler._terminal_image_pending = bool(results) and len(accepted_images) == len(results)
    from services.task_execution.image_binding import managed_image_task
    managed_images = bool(runtime and managed_image_task(runtime.task_execution_state))
    if managed_images:
        from services.task_execution.image_binding import image_dependencies_waiting
        handler._terminal_image_pending = image_dependencies_waiting(runtime.task_execution_state)
    if guard:
        guard.observe(results)
        if guard.stop_message:
            handler._tool_result_stop_reason = getattr(handler, "_tool_result_stop_reason", "") or guard.stop_message
    if runtime:
        tool_call_ids = [call["id"] for call in calls]
        command_id = _actor_tool_completion_command_id(
            request.task_id,
            runtime.turn_id,
            tool_call_ids,
        )
        command = ConversationCommand(
            command_id=command_id,
            command_type=CommandType.TOOL_COMPLETED,
            conversation_id=request.conversation_id,
            task_id=request.task_id,
            turn_id=runtime.turn_id,
            payload={"tool_call_ids": tool_call_ids},
        )
        if runtime.command_store and runtime.execution_token:
            append = getattr(runtime.command_store, "append", None)
            if append is not None:
                await append(
                    conversation_id=request.conversation_id,
                    task_id=request.task_id,
                    turn_id=runtime.turn_id,
                    command_type=CommandType.TOOL_COMPLETED,
                    dedupe_key=command_id,
                    payload=command.payload or {},
                )
        runtime.push(command)
    await _check_cancelled(
        cancellation_event, request, prepared.messages, blocks,
        totals, "post_tool",
    )
    from .native_plan_delivery import stage as stage_native_plan
    stage_native_plan(runtime,request,results)
    image_urls = apply_tool_results(
        tool_results=results,
        messages=prepared.messages,
        content_blocks=blocks,
        start_times=start_times,
        tool_context=prepared.tool_context,
        recorded_tool_call_ids=repair_rejections.keys(),
    )
    append_tool_images(prepared.messages, image_urls)
    if direct_enabled(runtime):
        from services.task_execution.direct_loop import advance_results,retire_not_dispatched
        runtime.task_execution_state=retire_not_dispatched(runtime.task_execution_state,{r[0]['id'] for r in rejected})
        runtime.task_execution_state=await advance_results(runtime,prepared)
        if getattr(runtime,'task_direct_result_issues',None):
            notice={'type':'text','text':'部分工具成果未通过当前可用性核验，未计入有效成果；执行记录已保留，将依据真实结果继续处理。'}
            blocks.append(notice)
            await sink.on_block(notice)
            runtime.task_direct_result_issues=[]
    if runtime:
        await runtime.safe_point(SafePoint.AFTER_TOOL)
        await _inject_runtime_steers(runtime, prepared)
    elif getattr(request, "steer_reader", None) is not None:
        steer_message = request.steer_reader()
        if steer_message:
            _inject_steer_messages(prepared.messages, [steer_message], prepared=prepared)
    if repeated_tool_call_nudge:
        prepared.messages.append({
            "role": "user",
            "content": repeated_tool_call_nudge,
        })
    form_hint = await _consume_emit_payloads(handler, blocks, sink)
    from .native_plan_delivery import deliver as deliver_native_plan,KEY as NATIVE_PLAN_DELIVERY_KEY
    if runtime and runtime.checkpoint_base.get(NATIVE_PLAN_DELIVERY_KEY,{}).get('phase')=='pending':
        # Freeze the exact server-authored body and complete tool pair before
        # page publication; recovery must not ask the model to recreate it.
        await runtime.safe_point(SafePoint.AFTER_TOOL,replay_payload=_build_replay_context(
            prepared.messages,blocks,turn,next_model_round=next_model_round,
            repeated_tool_call_guard=repeated_tool_call_guard))
    await deliver_native_plan(runtime,request,blocks,sink,totals)
    await compact_tool_context(
        messages=prepared.messages,
        conversation_source=handler._get_conv_source(request.conversation_id),
        turn=turn,
        context_window=getattr(getattr(prepared, "model_gateway", None), "effective_context_window", None),
    )
    if runtime:
        from services.task_execution.scheduled_operations import pending_schedule
        waiting_schedule=pending_schedule(runtime.task_execution_state,force=bool(
            getattr(handler,'_terminal_form_pending',False) or getattr(handler,'_terminal_change_set_pending',False)))
        from services.task_execution.video_operations import pending as pending_video
        waiting_video=pending_video(runtime.task_execution_state)
        if waiting_schedule or (not direct_enabled(runtime) and ((managed_images and handler._terminal_image_pending) or waiting_video)):
            from services.task_execution.contracts import TaskState
            from services.task_execution.model_bridge import commit_projection
            current = runtime.task_execution_state
            candidate = TaskState.model_validate({**current.model_dump(), 'revision': current.revision + 1,
                'status': 'waiting_dependencies' if (managed_images and handler._terminal_image_pending) or waiting_video else 'waiting_approval'})
            await commit_projection(runtime=runtime, candidate=candidate, prepared=prepared, blocks=blocks,
                point=SafePoint.AFTER_TOOL, build_checkpoint=_build_replay_context, turn=turn,
                next_model_round=next_model_round, tool_call_ids=[call['id'] for call in calls],
                repeated_tool_call_guard=repeated_tool_call_guard)
        else:
            await runtime.safe_point(SafePoint.AFTER_TOOL, replay_payload=_build_replay_context(
                prepared.messages, blocks, turn, tool_call_ids=[call['id'] for call in calls],
                next_model_round=next_model_round, repeated_tool_call_guard=repeated_tool_call_guard))
    if repair_batch and repair_batch.state.stopped and tool_argument_repairs.stopped(runtime, request):
        handler._tool_result_stop_reason = tool_argument_repairs.stop_message(runtime)
    logger.info(
        f"Headless tool turn complete | task={request.task_id} | "
        f"turn={turn + 1} | tools={[call['name'] for call in calls]}"
    )
    return form_hint


def _apply_skill_context(prepared: Any, skills: Any) -> None:
    if not getattr(prepared, "skill_host_scope_captured", False):
        prepared.skill_host_scope_captured = True
        prepared.skill_host_authorized_tool_names = prepared.execution_context.authorized_tool_names
    if skills.has_active_skills or getattr(prepared, "skill_context_applied", False):
        prepared.skill_context_applied = True
        current = prepared.skill_host_authorized_tool_names
        if current is not None:
            skills.restrict_tools(current)
        snapshot = dict(prepared.execution_context.authorization_snapshot)
        if skills.scheduled_snapshot is not None:
            required = set(snapshot.get("required_permissions") or ())
            required.update(code for c in skills.directory.values() for code in c.catalog_metadata.required_permissions)
            snapshot["required_permissions"] = sorted(required)
        prepared.execution_context = replace(
            prepared.execution_context,
            authorized_tool_names=skills.effective_allowed_tool_names,
            authorization_snapshot=snapshot,
        )
        if prepared.execution_context.execution_mode == "interactive":
            # Registry/Policy still filter this already authorized ceiling.
            prepared.tool_context.discovered_tools.update(skills.effective_allowed_tool_names)
    skills.ensure_messages(prepared.messages)


async def _execute_skill_batch(**kwargs) -> None:
    """Do not expose staged state if preparation or persistence fails."""
    import copy
    runtime, prepared = kwargs['runtime'], kwargs['prepared']
    original = runtime.skill_runtime if runtime else None
    original_task = runtime.task_execution_state if runtime else None
    save_sequence = runtime.replay_save_sequence if runtime else 0
    messages = copy.deepcopy(prepared.messages)
    blocks = copy.deepcopy(kwargs['blocks'])
    context = prepared.execution_context
    discovered = set(prepared.tool_context.discovered_tools)
    workflow = getattr(prepared, 'ecom_workflow', None)
    old_workflow = {name: copy.deepcopy(getattr(workflow, name))
                    for name in ('value', 'route', '_coordinated', 'ready_execution') if hasattr(workflow, name)}
    request_params = getattr(getattr(workflow, 'request', None), 'params', None)
    old_params = copy.deepcopy(request_params) if request_params is not None else None
    context_flags = {name: getattr(prepared, name) for name in (
        'skill_host_scope_captured', 'skill_host_authorized_tool_names', 'skill_context_applied')
        if hasattr(prepared, name)}
    projection = copy.deepcopy(getattr(runtime, 'workflow_projection', None)) if runtime else None
    try:
        await _execute_skill_batch_impl(**kwargs)
    except BaseException:
        if runtime and runtime.replay_save_sequence != save_sequence:
            # Commands may stop execution after the new projection was saved.
            # Keep its task facts, Skill facts and closed call/result pairs.
            raise
        if runtime:
            runtime.skill_runtime = original
            runtime.task_execution_state = original_task
            runtime.workflow_projection = projection
        prepared.messages[:] = messages
        kwargs['blocks'][:] = blocks
        prepared.execution_context = context
        prepared.tool_context.discovered_tools.clear()
        prepared.tool_context.discovered_tools.update(discovered)
        if workflow is not None:
            for name in ('value', 'route', '_coordinated', 'ready_execution'):
                if name in old_workflow:
                    setattr(workflow, name, old_workflow[name])
                elif hasattr(workflow, name):
                    delattr(workflow, name)
        if request_params is not None:
            request_params.clear()
            request_params.update(old_params)
        for name in ('skill_host_scope_captured', 'skill_host_authorized_tool_names', 'skill_context_applied'):
            if name in context_flags:
                setattr(prepared, name, context_flags[name])
            elif hasattr(prepared, name):
                delattr(prepared, name)
        raise


async def _execute_skill_batch_impl(
    *, prepared, calls, runtime, blocks, sink, start_times, turn,
    next_model_round, repeated_tool_call_guard,
) -> None:
    """Whole model batch barrier, including invalid/disabled activations."""
    skills = runtime.skill_runtime if runtime else None
    original_skills = skills
    save_sequence = runtime.replay_save_sequence if runtime else 0
    if skills is not None and hasattr(skills, 'staged'):
        skills = skills.staged()
        runtime.skill_runtime = skills
    results = []
    skill_blocks = []
    rejected_candidates = {b['candidate_skill_id'] for b in blocks if b.get('candidate_skill_id')}
    ordered_calls = skills.ordered_control_calls(calls) if skills is not None else calls
    for call in ordered_calls:
        key = None
        if call["name"] not in SKILL_CONTROL_TOOLS:
            result = control_result("SKILL_ACTIVATION_BARRIER", message="请下一轮重新请求")
        elif prepared.execution_context.execution_mode in {"scheduled", "preflight"}:
            result = control_result("SKILL_SCHEDULED_ACTIVATION_FORBIDDEN")
        elif skills is None:
            result = control_result("SKILL_RUNTIME_DISABLED")
        else:
            workflow = getattr(prepared, "ecom_workflow", None)
            try:
                key = json.loads(call.get("arguments", ""))["skill_id"]
            except (ValueError, TypeError, KeyError):
                key = None
            if call["name"] == READ_SKILL_ASSET:
                result = await skills.read_asset(call.get('arguments', ''))
            elif call["name"] == DEACTIVATE_SKILL:
                task_state = runtime.task_execution_state
                bound = next((b for b in task_state.method_bindings if b.skill_key == key), None) if task_state else None
                if bound is not None:
                    result = control_result('TASK_METHOD_PHASE_BOUND', message='当前方法仍关联任务交付，请先完成该阶段。')
                elif (workflow and workflow.value and workflow.value.kind == "main_images"
                        and key in {workflow.server_skill_key(), workflow.value.skill.get('skill_key')}):
                    result = control_result("SKILL_BINDING_LOCKED")
                else:
                    old_messages = skills.messages()
                    result = await skills.deactivate(call.get("arguments", ""))
                    if result["ok"]:
                        prepared.messages[:] = [m for m in prepared.messages if m not in old_messages]
            elif isinstance(key, str) and key in rejected_candidates:
                result = control_result('SKILL_CANDIDATE_ALREADY_REJECTED', state_unchanged=True,
                    message='当前候选已拒绝，请继续使用现有方法或选择不同的候选。')
            elif (key == "ecommerce-main-images" and workflow
                    and not await workflow.allows_planning()):
                result = control_result("SKILL_ROUTING_UNAVAILABLE" if workflow.route and workflow.route["kind"] == "unavailable" else "SKILL_NOT_APPLICABLE",
                    message="电商策划路由暂不可用。" if workflow.route and workflow.route["kind"] == "unavailable" else "已有提示词使用通用图片工具。")
            else:
                from services.task_execution.methods import completed_phase_bindings
                retired = ({b.skill_key for b in completed_phase_bindings(runtime.task_execution_state)}
                           if runtime.task_execution_state is not None else set())
                if key in retired:
                    result = control_result('TASK_METHOD_PHASE_COMPLETED',
                        message='该方法阶段已经交付，请复用已保存的完整成果继续后续步骤。')
                else:
                    skills.task_execution_state = runtime.task_execution_state
                    activation_options = {}
                    if key == "ecommerce-main-images" and workflow and skills.action_context.action == 'other':
                        from services.skills.coordination import ActionContext
                        activation_options['action'] = ActionContext(action='plan', source='workflow')
                    result = await skills.activate(call.get("arguments", ""), **activation_options)
                    if result.get('ok') and runtime.task_execution_state is not None:
                        from services.task_execution.methods import append_active_methods
                        runtime.task_execution_state = append_active_methods(runtime.task_execution_state, skills,
                            activated_skill_id=result['skill_id'])
                        result = {**result, 'task_revision': runtime.task_execution_state.revision}
        if call["name"] == ACTIVATE_SKILL and result.get('code') not in {
            'SKILL_CANDIDATE_ALREADY_REJECTED',
        }:
            block = skill_step(result, skills, step_id=call["id"])
            if (not result['ok'] and isinstance(key, str) and skills is not None
                    and key in skills.directory and key != skills.manual_skill_id and key not in skills.session_skill_ids):
                # Missing dependencies can be repaired by a later activation.
                if result['code'] != 'SKILL_DEPENDENCY_UNAVAILABLE':
                    block['candidate_skill_id'] = key
                    rejected_candidates.add(key)
            skill_blocks.append(block)
        text = json.dumps(result, ensure_ascii=False)
        results.append((call, text, not result["ok"], text))
    apply_tool_results(
        tool_results=results, messages=prepared.messages, content_blocks=blocks,
        start_times=start_times, tool_context=prepared.tool_context,
    )
    blocks.extend(skill_blocks)
    if skills is not None:
        _apply_skill_context(prepared, skills)
        workflow = getattr(prepared, 'ecom_workflow', None)
        if workflow and hasattr(workflow, 'coordinate') and any(b.get('status') == 'completed' for b in skill_blocks):
            await workflow.coordinate(skills)
            await workflow.activated()
    if runtime:
        # Save completed call/result pairs, rendered text and scope atomically
        # before pause/cancel reduction or any external delivery can interrupt.
        try:
            await runtime.safe_point(
                SafePoint.AFTER_SKILL_ACTIVATION,
                replay_payload=_build_replay_context(
                    prepared.messages, blocks, turn, tool_call_ids=[c["id"] for c in calls],
                    next_model_round=next_model_round,
                    repeated_tool_call_guard=repeated_tool_call_guard,
                ),
            )
        except BaseException:
            if runtime.replay_save_sequence == save_sequence:
                runtime.skill_runtime = original_skills
            raise  # No business IO may follow an unconfirmed composition save.
        if original_skills is not None and skills is not original_skills:
            original_skills.adopt(skills)
            runtime.skill_runtime = original_skills
        await _inject_runtime_steers(runtime, prepared)
    for block in skill_blocks:
        await sink.on_block(block)
    call_ids = {call["id"] for call in calls}
    for block in blocks:
        if block.get("tool_call_id") in call_ids:
            await sink.on_block_update(block)


def _assistant_tool_message(
    text: str,
    calls: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": call["arguments"],
                },
            }
            for call in calls
        ],
    }


def _build_replay_context(
    messages: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    turn_index: int,
    *,
    tool_call_ids: list[str] | None = None,
    next_model_round: int | None = None,
    repeated_tool_call_guard: _RepeatedToolCallGuard | None = None,
    skill_runtime: Any = None,
) -> dict[str, Any]:
    """构造模型可重放上下文；不把 token 级 DeliveryProgress 当 checkpoint。"""
    def legacy_default(value):
        from services.tools.result import ToolResult
        if isinstance(value, ToolResult):
            raise TypeError("Project ToolResult into model messages/content blocks before checkpoint")
        return str(value)

    payload = {
        "messages": json.loads(
            json.dumps(messages, ensure_ascii=False, default=legacy_default),
        ),
        "content_blocks": json.loads(
            json.dumps(blocks, ensure_ascii=False, default=legacy_default),
        ),
        "turn_index": turn_index,
        "tool_call_ids": list(tool_call_ids or []),
    }
    if next_model_round is not None:
        payload["next_model_round"] = next_model_round
    if skill_runtime is not None:
        payload["skill_runtime"] = skill_runtime.checkpoint()
    if repeated_tool_call_guard is not None:
        guard_state = repeated_tool_call_guard.replay_state()
        if guard_state:
            payload["repeated_tool_call_guard"] = guard_state
    return payload


async def _stop_repeated_tool_calls(
    *,
    calls: list[dict[str, Any]],
    previewed_call_ids: set[str],
    blocks: list[dict[str, Any]],
    sink: ExecutionSink,
    totals: StreamTotals,
) -> None:
    """终止已被 Actor 预览的第三次相同调用，并交付可见停止说明。"""
    for call in calls:
        call_id = call["id"]
        if call_id not in previewed_call_ids:
            continue
        for block in blocks:
            if block.get("tool_call_id") != call_id:
                continue
            block.update({
                "status": "error",
                "output": _REPEATED_TOOL_CALL_STOP,
                "elapsed_ms": 0,
            })
            await sink.on_block_update(block)
            break
    block = {"type": "text", "text": _REPEATED_TOOL_CALL_STOP}
    blocks.append(block)
    totals.text += _REPEATED_TOOL_CALL_STOP
    await sink.on_block(block)


def _inject_subtask_completions(
    messages: list[dict[str, Any]],
    completions: list[dict[str, Any]],
) -> None:
    """把安全点归约后的子任务结果注入下一次模型输入。

    子任务不是模型 tool_call，因此使用受控 system 事件承载结果，
    避免伪造未发生的 tool_call/tool_result 配对；消费列表由 Runtime
    一次性清空，重复 Redis/控制事件不会重复注入。
    """
    for completion in completions:
        result = json.dumps(
            completion.get("result") or {},
            ensure_ascii=False,
            default=str,
        )
        status = completion.get("status") or "failed"
        error_message = completion.get("error_message") or ""
        suffix = f"；错误：{error_message}" if error_message else ""
        messages.append({
            "role": "system",
            "content": (
                "[子任务完成回传] "
                f"child_task_id={completion.get('child_task_id')} "
                f"status={status} result={result}{suffix}"
            ),
        })


def _initial_replay_blocks(
    replay_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if replay_context is None:
        return []
    raw_blocks = replay_context.get("content_blocks", [])
    if not isinstance(raw_blocks, list) or not all(
        isinstance(block, dict) for block in raw_blocks
    ):
        raise RuntimeError("ACTOR_REPLAY_CONTEXT_BLOCKS_INVALID")
    return [dict(block) for block in raw_blocks]


async def _consume_emit_payloads(
    handler: Any,
    blocks: list[dict[str, Any]],
    sink: ExecutionSink,
) -> str | None:
    from services.handlers.emit_payloads import build_block_from_payload

    for payload in handler._pending_emit_payloads:
        block = build_block_from_payload(payload)
        if block:
            blocks.append(block)
            await sink.on_block(block)
    handler._pending_emit_payloads = []
    for reference in getattr(handler, "_pending_change_set_blocks", []):
        identity = ("proposal_id", reference.get("proposal_id")) if reference.get("type") == "skill_proposal" else ("change_set_id", reference.get("change_set_id"))
        if not any(block.get("type") == reference.get("type") and block.get(identity[0]) == identity[1]
                   for block in blocks):
            blocks.append(reference)
            await sink.on_block(reference)
    handler._pending_change_set_blocks = []
    form = getattr(handler, "_pending_form_block", None)
    form_hint = None
    if form:
        blocks.append(form)
        await sink.on_block(form)
        handler._pending_form_block = None
        form_hint = "请在上方表单中确认信息后点击提交。"
        await sink.on_text(form_hint)
    return form_hint


async def _apply_budget_stop(
    prepared: Any,
    totals: StreamTotals,
    blocks: list[dict[str, Any]],
    sink: ExecutionSink,
    *,cancellation_event=None,runtime=None,
) -> None:
    if not prepared.budget.stop_reason:
        return
    from services.agent.stop_policy import synthesize_wrap_up
    from services.handlers.chat.stream_finalize import stop_message

    skills = getattr(runtime, 'skill_runtime', None)
    synthesis = await synthesize_wrap_up(
        model_gateway=_get_model_gateway(prepared),
        messages=prepared.messages,
        content_blocks=blocks,
        reason=stop_message(prepared.budget.stop_reason),
        **({'skill_context': True} if skills and skills.has_active_skills else {}),
    )
    if synthesis:
        totals.text = synthesis
        block = {"type": "text", "text": synthesis}
        blocks.append(block)
        await sink.on_block(block)
    elif not totals.text:
        raise RuntimeError("CHAT_BUDGET_EXHAUSTED_WITHOUT_OUTPUT")
    else:
        warning = (
            f"\n\n> ⚠️ 已达到执行上限（{stop_message(prepared.budget.stop_reason)}），"
            "以上为部分结果。"
        )
        totals.text += warning
        block = {"type": "text", "text": warning}
        blocks.append(block)
        await sink.on_block(block)


def _accumulate_usage(totals: StreamTotals, chunk: Any) -> None:
    totals.usage["prompt_tokens"] += chunk.prompt_tokens or 0
    totals.usage["completion_tokens"] += chunk.completion_tokens or 0
    for key in ("cached_tokens", "cache_creation_input_tokens"):
        value = getattr(chunk, key, 0)
        if type(value) is int and value:
            totals.usage[key] = totals.usage.get(key, 0) + value
    for name, count in (getattr(chunk, "builtin_tool_usage", None) or {}).items():
        key = f"{name}_calls"
        totals.usage[key] = totals.usage.get(key, 0) + count
    if chunk.credits_consumed is not None:
        totals.usage["api_credits"] = chunk.credits_consumed
    if chunk.finish_reason:
        totals.last_finish_reason = chunk.finish_reason


def _build_digest(
    messages: list[dict[str, Any]],
    conversation_id: str,
    turns_used: int,
) -> dict[str, Any] | None:
    if turns_used <= 1:
        return None
    from services.handlers.tool_digest import build_tool_digest

    try:
        return build_tool_digest(messages, conversation_id)
    except Exception as error:
        logger.warning(f"Tool digest build failed | error={error}")
        return None


async def _check_cancelled(
    event: asyncio.Event,
    request: ChatExecutionRequest | None,
    messages: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    totals: StreamTotals,
    location: str,
) -> None:
    if not event.is_set():
        return
    if request is not None and request.on_cancel is not None:
        await request.on_cancel(
            messages,
            blocks,
            totals.text,
            totals.thinking,
            location,
        )
    raise asyncio.CancelledError


async def _sink_tool_calls(
    sink: ExecutionSink,
    calls: list[dict[str, Any]],
    turn: int,
) -> None:
    callback = getattr(sink, "on_tool_calls", None)
    if callback is not None:
        await callback(calls, turn)


async def _inject_runtime_steers(runtime, prepared):
    inbox = getattr(runtime, 'task_user_changes', None)
    pending = [item for item in inbox.items if not item.delivered] if inbox else []
    steers = runtime.consume_steer_messages()
    if not any(item.attachments for item in pending):
        _inject_steer_messages(prepared.messages, steers, prepared=prepared)
        workflow = getattr(prepared, 'ecom_workflow', None)
        if workflow is not None and hasattr(workflow, 'refresh_action'):
            await workflow.refresh_action()
        return
    if steers != [item.message for item in pending]:
        raise ConversationStopRequested('steer_attachment_checkpoint_mismatch')
    from types import SimpleNamespace
    from services.task_execution.steer_attachments import project

    context = prepared.execution_context
    if context.context_scope != 'user' or context.execution_mode != 'interactive':
        raise ConversationStopRequested('steer_attachment_scope_mismatch')
    owner = SimpleNamespace(user_id=context.actor_user_id, workspace_user_id=context.workspace_owner_id,
        org_id=context.org_id, conversation_id=context.conversation_id, context_scope=context.context_scope)
    for item in pending:
        _inject_steer_messages(prepared.messages, [item.message], prepared=prepared)
        if item.attachments:
            notice, images = await asyncio.to_thread(project, owner, item.attachments,
                visual_budget=content_tools.visual_budget_remaining(prepared.messages))
            text = item.message + notice
            prepared.messages[-1]['content'] = [{'type': 'text', 'text': text}, *images] if images else text

    workflow = getattr(prepared, 'ecom_workflow', None)
    if workflow is not None and hasattr(workflow, 'refresh_action'):
        await workflow.refresh_action()


def _inject_steer_messages(
    messages: list[dict[str, Any]],
    steer_messages: list[str],
    *, prepared=None,
) -> None:
    if prepared is not None and any(steer_messages):
        prepared.knowledge_steer_pending = True
        prepared.knowledge_pending_directives = [
            *getattr(prepared, 'knowledge_pending_directives', []),
            *(message for message in steer_messages if message),
        ]
    for message in steer_messages:
        if message:
            messages.append({"role": "user", "content": message})


async def _refresh_knowledge_after_steer(handler, request, prepared):
    if not getattr(prepared, 'knowledge_steer_pending', False):
        return
    # Only authenticated runtime directives change the question. Attachment
    # locators, preferences and other rendered packets are not directives.
    directives = getattr(prepared, 'knowledge_pending_directives', [])
    question = directives[-1] if directives else ''
    prepared.knowledge_question = question or getattr(prepared, 'text_content', '')
    if getattr(prepared, 'knowledge_input_facts', None) is not None:
        from dataclasses import replace
        prepared.knowledge_input_facts = replace(prepared.knowledge_input_facts,
            question=prepared.knowledge_question, changed=True)
    candidates = getattr(prepared, 'knowledge_candidates', None)
    if candidates is not None:
        await candidates.close()
        prepared.knowledge_candidates = None
        if getattr(handler, '_knowledge_candidates', None) is candidates:
            handler._knowledge_candidates = None
    # Actual bound knowledge remains bound. A new directive alone never creates
    # a default audit guard or starts an automatic knowledge tool chain.
    if getattr(prepared, 'knowledge_answer_plan', None):
        from services.knowledge_workspace.answer_plan import invalidate_for_steer
        invalidate_for_steer(prepared, handler._knowledge_run)
    prepared.knowledge_steer_pending = False
    prepared.knowledge_pending_directives = []


def _last_tool_output(blocks: list[dict[str, Any]]) -> str:
    for block in reversed(blocks):
        text = block.get("output") or block.get("text")
        if block.get("type") in {"tool_result", "tool_step"} and text:
            return str(text)[:2000]
    return ""
