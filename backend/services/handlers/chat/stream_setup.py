"""Chat 流式执行前的上下文、Provider、工具和预算准备。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from services.agent.runtime.context import ContextBudget


@dataclass
class PreparedChatStream:
    text_content: str
    messages: list[dict[str, Any]]
    adapter: Any
    permission: Any
    permission_mode: str
    core_tools: list[dict[str, Any]]
    stream_kwargs: dict[str, Any]
    tool_context: Any
    budget: Any
    context_budget: ContextBudget
    runtime_state: Any


async def prepare_chat_stream(
    *,
    handler: Any,
    content: list[Any],
    user_id: str,
    conversation_id: str,
    task_id: str,
    model_id: str,
    permission_mode: str,
    needs_google_search: bool,
    params: dict[str, Any],
    context_anchor: Any,
) -> PreparedChatStream:
    """准备一次固定上下文的 Chat 流执行，不读取或写入任务终态。"""
    from services.agent.runtime.context import resolve_context_budget

    started_at = time.monotonic()
    context_budget = resolve_context_budget(model_id)
    text_content = handler._extract_text_content(content)
    permission_mode = _normalize_permission_mode(permission_mode)
    messages = await handler._build_llm_messages(
        content,
        user_id,
        conversation_id,
        text_content,
        prefetched_summary=params.get("_prefetched_summary"),
        user_location=params.get("_user_location"),
        permission_mode=permission_mode,
        context_anchor=context_anchor,
        model_id=model_id,
        org_id=handler.org_id,
    )
    context_ready_at = time.monotonic()
    logger.info(
        f"Pre-stream timing | task={task_id} | memory=0ms | "
        f"context={int((context_ready_at - started_at) * 1000)}ms"
    )

    from services.adapters.factory import create_chat_adapter

    adapter = create_chat_adapter(
        model_id,
        org_id=handler.org_id,
        db=handler.db,
    )
    logger.info(
        f"Stream generate starting | model={model_id} | "
        f"adapter={type(adapter).__name__} | task={task_id} | "
        f"setup_total={int((time.monotonic() - started_at) * 1000)}ms"
    )

    permission, core_tools = _prepare_permission_and_tools(
        permission_mode,
        handler.org_id,
        getattr(handler, "_personal_context_allowed", True),
        evidence_available=bool(
            getattr(handler, "_data_context_snapshot", None)
            and handler._data_context_snapshot.evidence
        ),
    )
    stream_kwargs = _prepare_provider_tools(
        adapter,
        core_tools,
        needs_google_search,
        model_id,
        task_id,
    )
    tool_context = _prepare_request_context(
        handler,
        user_id,
        getattr(handler, "_workspace_user_id", user_id),
        conversation_id,
        task_id,
    )
    budget = _prepare_budget()
    from services.agent.runtime.runtime_contract import build_run_contract
    from services.agent.runtime.runtime_state import RuntimeState

    runtime_state = RuntimeState(
        contract=build_run_contract(params),
        observation_only=False,
        user_text=text_content,
        conversation_id=conversation_id,
        base_revision=getattr(context_anchor, "base_revision", None),
        task_id=task_id,
        model_id=model_id,
        org_id=handler.org_id,
    )
    data_context = getattr(handler, "_data_context_snapshot", None)
    if data_context is not None:
        runtime_state.restore(data_context.evidence)
    return PreparedChatStream(
        text_content=text_content,
        messages=messages,
        adapter=adapter,
        permission=permission,
        permission_mode=permission_mode,
        core_tools=core_tools,
        stream_kwargs=stream_kwargs,
        tool_context=tool_context,
        budget=budget,
        context_budget=context_budget,
        runtime_state=runtime_state,
    )


def _record_context_receipt(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    conversation_id: str,
    task_id: str,
    model_id: str,
    model_step: int = 0,
    base_revision: int = 0,
) -> dict[str, Any] | None:
    """Best-effort 记录影子回执，观测失败不得影响模型请求。"""
    try:
        from services.agent.runtime.context import (
            build_context_receipt,
            record_context_event,
        )

        receipt = build_context_receipt(
            messages=messages,
            tools=tools,
            conversation_id=conversation_id,
            task_id=task_id,
            model_id=model_id,
        )
        logger.bind(
            context_receipt=receipt.to_log_fields(),
        ).info("context_receipt_shadow")
        tokens_by_kind: dict[str, int] = {}
        for block in receipt.blocks:
            tokens_by_kind[block.content_kind] = (
                tokens_by_kind.get(block.content_kind, 0)
                + block.estimated_tokens
            )
        record_context_event(
            "context_receipt",
            conversation_id=conversation_id,
            task_id=task_id,
            model_id=model_id,
            context_estimated_tokens=receipt.estimated_prompt_tokens,
            context_tool_schema_tokens=receipt.estimated_tool_tokens,
            context_tokens_by_kind=tokens_by_kind,
            message_count=receipt.message_count,
            tool_count=receipt.tool_count,
        )
        return {
            "model_step": model_step,
            "base_revision": base_revision,
            "plan_hash": receipt.prefix_hash,
            "model": model_id,
            "block_refs": [
                {
                    "index": block.index,
                    "role": block.role,
                    "content_kind": block.content_kind,
                    "estimated_tokens": block.estimated_tokens,
                    "content_hash": block.content_hash,
                }
                for block in receipt.blocks
            ],
            "estimated_tokens": (
                receipt.estimated_prompt_tokens
                + receipt.estimated_tool_tokens
            ),
            "provider_tokens": None,
            "trimmed_refs": [],
            "compaction_id": None,
        }
    except Exception as error:
        logger.warning(
            "context_receipt_shadow_failed | "
            f"conversation_id={conversation_id} | task_id={task_id} | "
            f"model_id={model_id} | error={error}"
        )
        return None


def _normalize_permission_mode(permission_mode: Any) -> str:
    if permission_mode is True or permission_mode == "true":
        return "plan"
    if permission_mode is False or permission_mode == "false" or not permission_mode:
        return "auto"
    return str(permission_mode)


def _prepare_permission_and_tools(
    permission_mode: str,
    org_id: str | None,
    personal_context_allowed: bool,
    *,
    evidence_available: bool = False,
) -> tuple[Any, list[dict[str, Any]]]:
    from config.chat_tools import get_tools_for_mode
    from services.handlers.permission_mode import PermissionMode

    permission = PermissionMode(mode=permission_mode)
    logger.info(f"Permission mode | mode={permission.mode.value}")
    tools = list(get_tools_for_mode(permission.mode.value, org_id=org_id))
    from config.artifact_tools import build_artifact_tools

    tools.extend(build_artifact_tools())
    if not personal_context_allowed:
        tools = [
            tool for tool in tools
            if _tool_name(tool) not in _PERSONAL_TOOLS
        ]
    if evidence_available:
        from config.evidence_tools import build_evidence_tools

        tools.extend(build_evidence_tools())
    tools.sort(key=_tool_name)
    return permission, tools


def _tool_name(tool: dict[str, Any]) -> str:
    function = tool.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(tool.get("name") or "")


_PERSONAL_TOOLS = {
    "get_conversation_context",
    "manage_scheduled_task",
}


def _prepare_provider_tools(
    adapter: Any,
    core_tools: list[dict[str, Any]],
    needs_google_search: bool,
    model_id: str,
    task_id: str,
) -> dict[str, Any]:
    if not (
        needs_google_search
        and getattr(adapter, "supports_google_search", False)
    ):
        return {}
    core_tools.append(adapter.create_google_search_tool())
    logger.info(
        f"Google Search Grounding enabled | model={model_id} | task={task_id}"
    )
    return {}


def _prepare_request_context(
    handler: Any,
    user_id: str,
    workspace_user_id: str,
    conversation_id: str,
    task_id: str,
) -> Any:
    from core.config import get_settings
    from core.workspace import resolve_staging_dir
    from services.agent.observability import set_trace_id
    from services.agent.observability.langfuse_integration import create_trace
    from services.agent.tool_result_envelope import set_staging_dir
    from services.handlers.tool_loop_context import ToolLoopContext

    set_trace_id(task_id)
    logger.bind(trace_id=task_id)
    create_trace(
        name="chat_request",
        user_id=user_id,
        session_id=conversation_id,
    )
    settings = get_settings()
    set_staging_dir(
        resolve_staging_dir(
            settings.file_workspace_root,
            workspace_user_id,
            handler.org_id,
            conversation_id,
        )
    )
    return ToolLoopContext(org_id=handler.org_id, agent_domain="general")


def _prepare_budget() -> Any:
    from core.config import get_settings
    from services.agent.execution_budget import ExecutionBudget

    settings = get_settings()
    return ExecutionBudget(
        max_turns=settings.budget_max_turns,
        max_wall_time=settings.budget_max_wall_time,
    )
