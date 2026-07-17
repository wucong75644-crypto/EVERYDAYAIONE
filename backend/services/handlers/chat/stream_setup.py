"""Chat 流式执行前的上下文、Provider、工具和预算准备。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from loguru import logger


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
    started_at = time.monotonic()
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
        conversation_id,
        task_id,
    )
    budget = _prepare_budget()
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
    )


def _normalize_permission_mode(permission_mode: Any) -> str:
    if permission_mode is True or permission_mode == "true":
        return "plan"
    if permission_mode is False or permission_mode == "false" or not permission_mode:
        return "auto"
    return str(permission_mode)


def _prepare_permission_and_tools(
    permission_mode: str,
    org_id: str | None,
) -> tuple[Any, list[dict[str, Any]]]:
    from config.chat_tools import get_tools_for_mode
    from services.handlers.permission_mode import PermissionMode

    permission = PermissionMode(mode=permission_mode)
    logger.info(f"Permission mode | mode={permission.mode.value}")
    return (
        permission,
        get_tools_for_mode(permission.mode.value, org_id=org_id),
    )


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
    conversation_id: str,
    task_id: str,
) -> Any:
    from core.config import get_settings
    from core.workspace import resolve_staging_dir
    from services.agent.observability import set_trace_id
    from services.agent.observability.langfuse_integration import create_trace
    from services.agent.tool_result_envelope import set_staging_dir
    from services.handlers.session_memory import init_session_memory
    from services.handlers.tool_loop_context import ToolLoopContext

    init_session_memory()
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
            user_id,
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
