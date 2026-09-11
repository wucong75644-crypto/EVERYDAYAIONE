"""ToolLoopExecutor 辅助函数

从 tool_loop_executor.py 拆出（V2.2 §三 500 行红线），承担：
- inject_tool：动态扩展隐藏工具到当前可见集
- invoke_tool_with_cache：缓存命中检查 + 工具执行 + 超时控制 + 状态分类

均为纯函数，无类状态依赖，可独立单测。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, FrozenSet, List, Tuple

from loguru import logger

from services.agent.agent_result import AgentResult


def inject_tool(
    tool_name: str,
    selected_tools: List[Dict[str, Any]],
    all_tools: List[Dict[str, Any]],
    exit_signals: FrozenSet[str],
    org_id: str,
    *, context=None,
) -> None:
    """模型调了隐藏的远程工具 → 从全量列表动态注入到 selected_tools（去重）

    副作用：mutate selected_tools。
    """
    if tool_name in exit_signals:
        return
    from services.tools import build_legacy_catalog, ToolPolicy, LegacyAdvertisement
    from services.tools.runtime_context import catalog_context
    registry = build_legacy_catalog()
    context = context or catalog_context(org_id)
    names = {t["function"]["name"] for t in selected_tools}
    selected_tools[:] = registry.resolve(
        context, policy=ToolPolicy(registry), advertisement=LegacyAdvertisement(names),
        discovered_names=(tool_name,),
    ).advertised_schemas()


async def invoke_tool_result_with_cache(
    executor: Any, cache: Any, tool_name: str, args: Dict[str, Any],
    budget: Any, default_timeout: float, *, call_id: str | None = None,
):
    """The live loop consumes the envelope, including cached failures and effects."""
    from dataclasses import replace
    from uuid import uuid4
    from services.tools import ToolCall, ToolResult

    started = time.monotonic()
    tool_timeout = budget.tool_timeout(default_timeout) if budget else default_timeout
    runtime = executor.tool_runtime
    call = ToolCall(call_id or str(uuid4()), tool_name, args)
    try:
        result = await asyncio.wait_for(
            runtime.execute(tool_name, args, call_id=call.call_id, cache=cache),
            timeout=tool_timeout,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        context = runtime.context(call.call_id)
        decision = runtime.policy.decide(tool_name, context, args)
        cancelled = getattr(error.__cause__, "tool_result", None)
        result = ToolResult.from_exception(
            error, call=call, context=context, decision=cancelled.decision if cancelled else decision,
            handler_started=cancelled.execution.handler_started if cancelled else False,
        )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    result = replace(result, execution=replace(result.execution, elapsed_ms=elapsed_ms),
                     audit={**result.audit, "elapsed_ms": elapsed_ms})
    if result.exception is not None:
        legacy_error = _legacy_loop_error(result, tool_timeout)
        result = result.with_model_content(
            "tool_loop", legacy_error.to_tool_content() + result.uncertainty_notice,
        )
    status = ("timeout" if result.status == "timeout" else "error") if result.is_failure else "success"
    return result, status, result.execution.cached, elapsed_ms


def _legacy_loop_error(result, timeout):
    if result.status == "timeout":
        return AgentResult(
            summary=f"工具执行超时（{int(timeout)}秒），请缩小查询范围",
            status="timeout", error_message=f"Timeout: {int(timeout)}s",
        )
    return AgentResult(
        summary=f"工具执行失败: {result.exception}", status="error",
        error_message=str(result.exception), metadata={"retryable": False},
    )


async def invoke_tool_with_cache(
    executor: Any, cache: Any, tool_name: str, args: Dict[str, Any],
    budget: Any, default_timeout: float, *, call_id: str | None = None,
) -> Tuple[Any, str, bool, int]:
    """Legacy helper API; production ToolLoop uses invoke_tool_result_with_cache."""
    result, status, cached, ms = await invoke_tool_result_with_cache(
        executor, cache, tool_name, args, budget, default_timeout, call_id=call_id,
    )
    if result.exception is not None:
        timeout = budget.tool_timeout(default_timeout) if budget else default_timeout
        return _legacy_loop_error(result, timeout), status, cached, ms
    return result.to_legacy(), status, cached, ms
