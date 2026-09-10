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
    model_gateway: Any
    permission: Any
    permission_mode: str
    core_tools: list[dict[str, Any]]
    stream_kwargs: dict[str, Any]
    tool_context: Any
    budget: Any
    execution_context: Any = None

    @property
    def adapter(self) -> Any:
        """兼容旧的内部 PreparedChatStream 读取方。"""
        return self.model_gateway


async def prepare_chat_stream(
    *,
    handler: Any,
    content: list[Any],
    user_id: str,
    conversation_id: str,
    task_id: str,
    model_id: str,
    model_request_id: str | None = None,
    permission_mode: str,
    needs_google_search: bool,
    params: dict[str, Any],
    context_anchor: Any,
    replay_context: dict[str, Any] | None = None,
    cancellation_event: Any = None,
    retry_context: Any = None,
    on_model_retry: Any = None,
) -> PreparedChatStream:
    """准备一次固定上下文的 Chat 流执行，不读取或写入任务终态。"""
    started_at = time.monotonic()
    text_content = handler._extract_text_content(content)
    permission_mode = _normalize_permission_mode(permission_mode)
    if replay_context is not None:
        raw_messages = replay_context.get("messages")
        if not isinstance(raw_messages, list) or not all(
            isinstance(message, dict) for message in raw_messages
        ):
            raise RuntimeError("ACTOR_REPLAY_CONTEXT_INVALID")
        # ReplayCheckpoint 已经冻结了完整模型上下文；继续时不能再次从
        # interrupted history 读取，否则会把旧 partial 当成新的上下文。
        messages = [dict(message) for message in raw_messages]
    else:
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
    from services.handlers.context_snapshot import ContextAnchor
    if isinstance(context_anchor, ContextAnchor):
        import asyncio
        from services.handlers.resource_manifest import build_resource_manifest
        input_content = [part.model_dump(mode="json") if hasattr(part, "model_dump") else part
                         for part in content]

        async def load_resources():
            return await asyncio.to_thread(
                build_resource_manifest, handler.db, task_id=context_anchor.task_id,
                input_message_id=context_anchor.input_message_id,
                conversation_id=context_anchor.conversation_id, turn_id=context_anchor.turn_id,
                org_id=context_anchor.org_id, input_content=input_content,
            )

        handler._tool_resource_manifest_loader = load_resources
        if replay_context is not None:
            handler._resource_manifest = await load_resources()
    logger.info(
        f"Pre-stream timing | task={task_id} | memory=0ms | "
        f"context={int((context_ready_at - started_at) * 1000)}ms"
    )

    from services.model_gateway import ModelCallRequest, get_model_gateway
    budget = _prepare_budget()
    retry_policy = handler._build_model_retry_policy(
        params=params, content=content, task_id=task_id,
        conversation_id=conversation_id, user_id=user_id,
        retry_context=retry_context, on_retry=on_model_retry,
    )

    def prepare_attempt(session, kwargs):
        # 保留工具循环传入的工具列表；Provider 特有搜索工具按每次实际模型添加。
        tools = list(kwargs.get("tools") or [])
        _prepare_provider_tools(session, tools, needs_google_search, session.model_id, task_id)
        return {**kwargs, "tools": tools} if tools else kwargs

    retry_policy.prepare_stream = prepare_attempt

    model_gateway = get_model_gateway().open_chat(
        ModelCallRequest(
            model_id=model_id,
            org_id=handler.org_id,
            db=handler.db,
            task_id=task_id,
            request_id=model_request_id,
            cancel_token=cancellation_event,
            budget=budget,
            retry_policy=retry_policy,
        )
    )
    try:
        logger.info(
            f"Stream generate starting | model={model_id} | "
            f"adapter={model_gateway.adapter_name} | task={task_id} | "
            f"setup_total={int((time.monotonic() - started_at) * 1000)}ms"
        )

        from services.tools.runtime_context import chat_context
        execution_context = chat_context(
            handler, user_id=user_id, conversation_id=conversation_id, task_id=task_id,
            permission_mode=permission_mode, budget=budget, cancellation=cancellation_event,
        )
        permission, core_tools = _prepare_permission_and_tools(
            permission_mode,
            handler.org_id,
            getattr(handler, "_personal_context_allowed", True),
            execution_context=execution_context,
        )
        stream_kwargs = {}
        tool_context = _prepare_request_context(
            handler,
            user_id,
            getattr(handler, "_workspace_user_id", user_id),
            conversation_id,
            task_id,
        )
        return PreparedChatStream(
            text_content=text_content,
            messages=messages,
            model_gateway=model_gateway,
            permission=permission,
            permission_mode=permission_mode,
            core_tools=core_tools,
            stream_kwargs=stream_kwargs,
            tool_context=tool_context,
            budget=budget,
            execution_context=execution_context,
        )
    except BaseException:
        await model_gateway.close()
        raise


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
    *, execution_context=None,
) -> tuple[Any, list[dict[str, Any]]]:
    from services.tools import ToolContext, ToolPolicy, LegacyAdvertisement, build_legacy_catalog
    from services.handlers.permission_mode import PermissionMode

    permission = PermissionMode(mode=permission_mode)
    logger.info(f"Permission mode | mode={permission.mode.value}")
    if execution_context is None:
        from services.tools.runtime_context import catalog_context
        execution_context = catalog_context(org_id, permission.mode.value, personal_context_allowed)
    registry = build_legacy_catalog()
    tools = registry.resolve(execution_context, policy=ToolPolicy(registry),
                             advertisement=LegacyAdvertisement()).advertised_schemas()
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
