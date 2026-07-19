"""Chat 工具轮次的结构编排、结果回填与上下文收尾。"""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger

from schemas.multimodal import FileReadResult
from services.agent.runtime.context import ContextBudget


def prepare_tool_turn(
    *,
    core_tools: list[dict[str, Any]],
    discovered_names: set[str],
    org_id: str | None,
    turn: int,
    messages: list[dict[str, Any]],
    tool_context: Any,
    permission: Any,
    runtime_state: Any = None,
) -> list[dict[str, Any]]:
    """构建本轮工具列表并追加动态上下文、退出附件与权限提醒。"""
    current_tools = list(core_tools)
    if discovered_names:
        from config.chat_tools import get_tools_by_names
        from config.tool_domains import filter_tools_for_domain

        discovered = get_tools_by_names(discovered_names, org_id=org_id)
        discovered = filter_tools_for_domain(discovered, "general")
        core_names = {tool["function"]["name"] for tool in core_tools}
        current_tools.extend(
            tool
            for tool in discovered
            if tool["function"]["name"] not in core_names
        )
        logger.info(
            f"Dynamic tools injected | turn={turn + 1} | "
            f"discovered={sorted(discovered_names)} | "
            f"total={len(current_tools)}"
        )

    if turn > 0:
        from services.handlers.context_compressor import (
            deduplicate_system_prompts,
        )

        deduplicate_system_prompts(messages)
        context_prompt = tool_context.build_context_prompt()
        if context_prompt:
            messages.append({"role": "system", "content": context_prompt})

    if permission.need_exit_attachment:
        messages.append(
            {
                "role": "system",
                "content": permission.consume_exit_attachment(),
            }
        )
    if turn > 0:
        reminder = permission.get_reminder(turn)
        if reminder:
            messages.append({"role": "system", "content": reminder})
    return current_tools


def build_running_step(call: dict[str, Any]) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_step",
        "tool_name": call["name"],
        "tool_call_id": call["id"],
        "status": "running",
    }
    raw_arguments = call.get("arguments", "")
    if raw_arguments:
        block["input"] = raw_arguments
    if call["name"] == "code_execute":
        try:
            code = json.loads(raw_arguments or "{}").get("code", "")
            if code:
                block["code"] = code
        except (TypeError, ValueError):
            pass
    return block


def apply_tool_results(
    *,
    tool_results: list[tuple[Any, Any, bool, str]],
    messages: list[dict[str, Any]],
    content_blocks: list[dict[str, Any]],
    start_times: dict[str, float],
    tool_context: Any,
    runtime_state: Any = None,
) -> list[str]:
    """把工具结果写回模型消息和 tool_step，返回待注入的图片 URL。"""
    image_urls: list[str] = []
    for call, result, is_error, display_text in tool_results:
        artifact = _observe_tool_result(
            runtime_state, call, result, is_error=is_error
        )
        tool_context.update_from_result(
            call["name"],
            display_text,
            is_error,
        )
        if (
            isinstance(result, FileReadResult)
            and result.type == "image"
            and result.image_url
        ):
            image_urls.append(result.image_url)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": _project_result(result, artifact),
            }
        )
        _complete_tool_step(
            content_blocks,
            call["id"],
            is_error,
            display_text,
            start_times.get(call["id"]),
        )
    return image_urls


def _observe_tool_result(
    runtime_state: Any,
    call: dict[str, Any],
    result: Any,
    *,
    is_error: bool,
) -> Any:
    if runtime_state is None:
        return None
    from services.agent.runtime.artifacts import normalize_tool_result
    from services.agent.runtime.artifact_collector import collect_tool_result
    from services.agent.runtime.policies.data_accuracy import (
        validate_data_evidence,
    )
    from services.agent.runtime.artifact_ledger import ArtifactKind

    artifact = normalize_tool_result(
        result,
        tool_call_id=str(call.get("id") or ""),
        tool_name=str(call.get("name") or ""),
        is_error=is_error,
        conversation_id=str(runtime_state.conversation_id or ""),
    )
    runtime_state.artifacts.add(artifact)
    for evidence in collect_tool_result(result, tool_call_id=call.get("id")):
        if (
            evidence.kind == ArtifactKind.DATA_RESULT
            and not validate_data_evidence(evidence).accepted
        ):
            continue
        runtime_state.ledger.record(evidence)
    return artifact


def _project_result(result: Any, artifact: Any) -> Any:
    if artifact is None:
        from services.handlers.chat_generate_mixin import unpack_tool_result

        return unpack_tool_result(result)
    from services.agent.runtime.artifacts import project_tool_result

    return project_tool_result(result, artifact)


def _complete_tool_step(
    blocks: list[dict[str, Any]],
    call_id: str,
    is_error: bool,
    output: str,
    started_at: float | None,
) -> None:
    elapsed_ms = (
        int((time.monotonic() - started_at) * 1000)
        if started_at else 0
    )
    for block in blocks:
        if (
            block.get("type") == "tool_step"
            and block.get("tool_call_id") == call_id
        ):
            block["status"] = "error" if is_error else "completed"
            block["elapsed_ms"] = elapsed_ms
            block["output"] = output
            return


def append_tool_images(
    messages: list[dict[str, Any]],
    image_urls: list[str],
) -> None:
    if not image_urls:
        return
    parts: list[dict[str, Any]] = [
        {"type": "text", "text": "[系统：以下是工具返回的图片]"}
    ]
    parts.extend(
        {
            "type": "image_url",
            "image_url": {"url": image_url},
        }
        for image_url in image_urls
    )
    messages.append({"role": "user", "content": parts})


async def compact_tool_context(
    *,
    messages: list[dict[str, Any]],
    context_budget: ContextBudget,
    turn: int,
    compaction_scope: str | None = None,
) -> None:
    """按当前模型预算压缩已完成工具轮次。"""
    from services.agent.runtime.context import record_context_event
    from services.handlers.context_compressor import (
        compact_loop_with_summary,
        compact_stale_by_user_turns,
        enforce_budget,
        enforce_history_budget_sync,
        enforce_tool_budget,
        estimate_tokens,
    )

    tokens_before = estimate_tokens(messages)
    compact_stale_by_user_turns(
        messages,
        keep_user_turns=3,
        capacity_trigger=(
            context_budget.soft_compaction / context_budget.usable_input
        ),
        max_tokens=context_budget.usable_input,
    )
    enforce_tool_budget(messages, context_budget.hard_compaction)
    enforce_history_budget_sync(
        messages,
        context_budget.hard_compaction,
    )
    if turn >= 3:
        summarized = await compact_loop_with_summary(
            messages,
            context_budget.usable_input,
            context_budget.hard_compaction / context_budget.usable_input,
            suppression_scope=compaction_scope,
        )
    else:
        summarized = False
    enforce_budget(messages, context_budget.emergency_trim)
    tokens_after = estimate_tokens(messages)
    record_context_event(
        "context_compaction",
        task_id=compaction_scope,
        turn=turn,
        outcome=(
            "summarized"
            if summarized else "trimmed"
            if tokens_after < tokens_before else "unchanged"
        ),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        trimmed_tokens=max(0, tokens_before - tokens_after),
    )
