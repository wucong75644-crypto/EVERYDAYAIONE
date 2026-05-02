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
) -> None:
    """模型调了隐藏的远程工具 → 从全量列表动态注入到 selected_tools（去重）

    副作用：mutate selected_tools。
    """
    if tool_name in exit_signals:
        return
    current = {t["function"]["name"] for t in selected_tools}
    if tool_name in current:
        return

    all_map = {t["function"]["name"]: t for t in all_tools}
    if tool_name in all_map:
        selected_tools.append(all_map[tool_name])
        logger.info(f"ToolLoop tool injected | {tool_name}")
    else:
        # 不在当前 Agent 的全量列表 → fallback 到全局池（带域检查）
        try:
            from config.chat_tools import get_tools_by_names
            from config.tool_domains import can_access
            extra = get_tools_by_names({tool_name}, org_id=org_id)
            # 域检查：推断当前域（all_tools 含 ERP 工具则为 erp 域）
            _has_erp = any(
                t["function"]["name"].startswith(("erp_", "local_"))
                for t in all_tools[:5]
            )
            _domain = "erp" if _has_erp else "general"
            extra = [t for t in extra if can_access(t["function"]["name"], _domain)]
            selected_tools.extend(extra)
            if extra:
                logger.info(f"ToolLoop fallback injected | {tool_name} | domain={_domain}")
        except Exception as e:
            logger.debug(
                f"ToolLoop tool injection fallback failed | "
                f"tool={tool_name} | error={e}"
            )


async def invoke_tool_with_cache(
    executor: Any,
    cache: Any,
    tool_name: str,
    args: Dict[str, Any],
    budget: Any,
    default_timeout: float,
) -> Tuple[Any, str, bool, int]:
    """缓存命中检查 → 否则执行工具（含超时控制）。

    Returns:
        (result, audit_status, is_cached, elapsed_ms)
        result: 工具返回值（AgentResult / str / 其他）
        audit_status: "success" | "timeout" | "error"
    """
    audit_start = time.monotonic()
    audit_status = "success"

    cached = cache.get(tool_name, args)
    if cached is not None:
        logger.info(f"ToolLoop cache hit | tool={tool_name}")
        elapsed_ms = int((time.monotonic() - audit_start) * 1000)
        return cached, audit_status, True, elapsed_ms

    # 超时控制（动态：min(单工具上限, 剩余预算)）
    tool_timeout = (
        budget.tool_timeout(default_timeout) if budget else default_timeout
    )
    try:
        result = await asyncio.wait_for(
            executor.execute(tool_name, args),
            timeout=tool_timeout,
        )
        cache.put(tool_name, args, result)
    except asyncio.TimeoutError:
        logger.warning(
            f"ToolLoop tool timeout | tool={tool_name} | "
            f"timeout={tool_timeout:.1f}s"
        )
        result = AgentResult(
            summary=f"工具执行超时（{int(tool_timeout)}秒），请缩小查询范围",
            status="timeout",
            error_message=f"Timeout: {int(tool_timeout)}s",
        )
        audit_status = "timeout"
    except Exception as e:
        logger.error(f"ToolLoop tool error | tool={tool_name} | error={e}")
        result = AgentResult(
            summary=f"工具执行失败: {e}",
            status="error",
            error_message=str(e),
            metadata={"retryable": False},
        )
        audit_status = "error"

    # AgentResult 状态 → audit_status 同步（工具内部返回结构化错误时）
    if isinstance(result, AgentResult) and result.is_failure:
        audit_status = "timeout" if result.status == "timeout" else "error"

    elapsed_ms = int((time.monotonic() - audit_start) * 1000)
    return result, audit_status, False, elapsed_ms
