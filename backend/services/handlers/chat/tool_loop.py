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
    model_step: int = 0,
    turns_remaining: int = 0,
) -> list[str]:
    """把工具结果写回模型消息和 tool_step，返回待注入的图片 URL。"""
    image_urls: list[str] = []
    for call, result, is_error, display_text in tool_results:
        artifact = _observe_tool_result(
            runtime_state,
            call,
            result,
            is_error=is_error,
            model_step=model_step,
            turns_remaining=turns_remaining,
            duration_ms=_elapsed_ms(start_times.get(call["id"])),
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
    model_step: int,
    turns_remaining: int,
    duration_ms: int,
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
    _observe_validation_result(
        runtime_state,
        call,
        result,
        is_error=is_error,
        model_step=model_step,
        turns_remaining=turns_remaining,
        duration_ms=duration_ms,
    )
    for evidence in collect_tool_result(result, tool_call_id=call.get("id")):
        if (
            evidence.kind == ArtifactKind.DATA_RESULT
            and not validate_data_evidence(evidence).accepted
        ):
            continue
        runtime_state.ledger.record(evidence)
    return artifact


def _observe_validation_result(
    runtime_state: Any,
    call: dict[str, Any],
    result: Any,
    *,
    is_error: bool,
    model_step: int,
    turns_remaining: int,
    duration_ms: int,
) -> None:
    validation = getattr(runtime_state, "validation", None)
    if validation is None:
        return
    try:
        validated, decision = validation.observe_result(
            result,
            tool_call_id=str(call.get("id") or ""),
            tool_name=str(call.get("name") or ""),
            model_step=model_step,
            turns_remaining=turns_remaining,
            audit_status="error" if is_error else "success",
            effect=_resolve_tool_effect(str(call.get("name") or "")),
            duration_ms=duration_ms,
        )
    except Exception as error:
        logger.warning(
            "validation_observer_failed | "
            f"task_id={runtime_state.task_id} | model_step={model_step} | "
            f"tool_call_id={call.get('id')} | tool_name={call.get('name')} | "
            f"error={type(error).__name__}"
        )
        return
    logger.info(
        "validation_result_classified | "
        f"task_id={runtime_state.task_id} | model_step={model_step} | "
        f"tool_call_id={validated.tool_call_id} | "
        f"tool_name={validated.effective_tool_name} | "
        f"result_class={validated.result_class.value} | "
        f"decision={decision.value} | observation_only=true"
    )


def _resolve_tool_effect(tool_name: str) -> Any:
    from services.agent.runtime.validation import resolve_tool_effect

    return resolve_tool_effect(tool_name)


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
    elapsed_ms = _elapsed_ms(started_at)
    for block in blocks:
        if (
            block.get("type") == "tool_step"
            and block.get("tool_call_id") == call_id
        ):
            block["status"] = "error" if is_error else "completed"
            block["elapsed_ms"] = elapsed_ms
            block["output"] = output
            return


def _elapsed_ms(started_at: float | None) -> int:
    return (
        int((time.monotonic() - started_at) * 1000)
        if started_at else 0
    )


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
    runtime_state: Any = None,
) -> None:
    """按当前模型预算压缩已完成工具轮次。"""
    from services.agent.runtime.context import (
        compact_context,
        prune_context,
        record_context_event,
    )
    from services.handlers.context_compressor import (
        enforce_budget,
        estimate_tokens,
    )

    tokens_before = estimate_tokens(messages)
    pruning_receipt = prune_context(
        messages,
        usable_input=context_budget.usable_input,
        model_step=turn + 1,
    )
    if runtime_state is not None:
        runtime_state.pruning_receipts.append(pruning_receipt.to_dict())
    if turn >= 3:
        compaction_receipt = await compact_context(
            messages,
            usable_input=context_budget.usable_input,
            trigger_ratio=(
                context_budget.hard_compaction / context_budget.usable_input
            ),
            suppression_scope=compaction_scope,
            model_step=turn + 1,
        )
    else:
        compaction_receipt = None
    if runtime_state is not None and compaction_receipt is not None:
        runtime_state.compaction_receipts.append(compaction_receipt.to_dict())
    enforce_budget(messages, context_budget.emergency_trim)
    tokens_after = estimate_tokens(messages)
    record_context_event(
        "context_compaction",
        task_id=compaction_scope,
        turn=turn,
        outcome=(
            "summarized"
            if compaction_receipt
            and compaction_receipt.outcome == "compacted" else "trimmed"
            if tokens_after < tokens_before else "unchanged"
        ),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        trimmed_tokens=max(0, tokens_before - tokens_after),
        reason=pruning_receipt.outcome,
    )
