"""Chat 工具轮次的结构编排、结果回填与上下文收尾。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable

from loguru import logger

from schemas.multimodal import FileReadResult
from schemas.websocket import (
    build_content_block_add,
    build_message_chunk,
    build_tool_call,
)
from services.handlers.chat.stream_session import StreamDelivery
from services.handlers.chat_generate_mixin import unpack_tool_result
from services.handlers.emit_payloads import build_block_from_payload


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
    if runtime_state is not None:
        from config.runtime_tools import build_data_compute_tool
        from services.agent.runtime.data_compute import has_computable_data

        if has_computable_data(runtime_state):
            current_tools.append(build_data_compute_tool())
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


async def begin_tool_calls(
    *,
    completed_calls: list[dict[str, Any]],
    turn_text: str,
    turn: int,
    messages: list[dict[str, Any]],
    content_blocks: list[dict[str, Any]],
    delivery: StreamDelivery,
    websocket: Any,
    save_blocks: Callable[[str, list[dict[str, Any]]], Awaitable[None]],
) -> dict[str, float]:
    """追加 assistant tool_calls，推送调用事件并创建 running tool_step。"""
    messages.append(
        {
            "role": "assistant",
            "content": turn_text or None,
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": call["arguments"],
                    },
                }
                for call in completed_calls
            ],
        }
    )
    await websocket.send_to_task_or_user(
        delivery.task_id,
        delivery.user_id,
        build_tool_call(
            task_id=delivery.task_id,
            conversation_id=delivery.conversation_id,
            message_id=delivery.message_id,
            tool_calls=[
                {"name": call["name"], "id": call["id"]}
                for call in completed_calls
            ],
            turn=turn + 1,
        ),
    )

    start_times: dict[str, float] = {}
    for call in completed_calls:
        block = build_running_step(call)
        content_blocks.append(block)
        start_times[call["id"]] = time.monotonic()
        try:
            await websocket.send_to_task_or_user(
                delivery.task_id,
                delivery.user_id,
                build_content_block_add(
                    task_id=delivery.task_id,
                    conversation_id=delivery.conversation_id,
                    message_id=delivery.message_id,
                    block=block,
                ),
            )
        except Exception as error:
            logger.warning(
                f"tool_step push failed | tc={call['id']} | {error}"
            )
    asyncio.create_task(save_blocks(delivery.task_id, content_blocks))
    return start_times


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
        _observe_tool_result(runtime_state, call, result)
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
                "content": unpack_tool_result(result),
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
) -> None:
    if runtime_state is None:
        return
    from services.agent.runtime.artifact_collector import collect_tool_result
    from services.agent.runtime.policies.data_accuracy import (
        validate_data_evidence,
    )
    from services.agent.runtime.artifact_ledger import ArtifactKind

    for evidence in collect_tool_result(result, tool_call_id=call.get("id")):
        if (
            evidence.kind == ArtifactKind.DATA_RESULT
            and not validate_data_evidence(evidence).accepted
        ):
            continue
        runtime_state.ledger.record(evidence)
        if (
            call.get("name") == "data_compute"
            and evidence.kind == ArtifactKind.DATA_RESULT
        ):
            runtime_state.request_grounded_final()


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


async def push_emit_payloads(
    *,
    payloads: list[dict[str, Any]],
    content_blocks: list[dict[str, Any]],
    delivery: StreamDelivery,
    websocket: Any,
    save_blocks: Callable[[str, list[dict[str, Any]]], Awaitable[None]],
) -> None:
    if not payloads:
        return
    for payload in payloads:
        block = build_block_from_payload(payload)
        if not block:
            continue
        content_blocks.append(block)
        await websocket.send_to_task_or_user(
            delivery.task_id,
            delivery.user_id,
            build_content_block_add(
                task_id=delivery.task_id,
                conversation_id=delivery.conversation_id,
                message_id=delivery.message_id,
                block=block,
            ),
        )
    asyncio.create_task(save_blocks(delivery.task_id, content_blocks))
    logger.info(
        f"Emit payloads pushed | count={len(payloads)} | "
        f"kinds={[payload.get('kind') for payload in payloads]} | "
        f"task={delivery.task_id}"
    )


async def push_form_block(
    *,
    form: dict[str, Any] | None,
    content_blocks: list[dict[str, Any]],
    delivery: StreamDelivery,
    websocket: Any,
    save_blocks: Callable[[str, list[dict[str, Any]]], Awaitable[None]],
) -> str:
    if not form:
        return ""
    content_blocks.append(form)
    await websocket.send_to_task_or_user(
        delivery.task_id,
        delivery.user_id,
        build_content_block_add(
            task_id=delivery.task_id,
            conversation_id=delivery.conversation_id,
            message_id=delivery.message_id,
            block=form,
        ),
    )
    hint = "请在上方表单中确认信息后点击提交。"
    await websocket.send_to_task_or_user(
        delivery.task_id,
        delivery.user_id,
        build_message_chunk(
            task_id=delivery.task_id,
            conversation_id=delivery.conversation_id,
            message_id=delivery.message_id,
            chunk=hint,
        ),
    )
    asyncio.create_task(save_blocks(delivery.task_id, content_blocks))
    logger.info(f"FormBlock pushed + persisted | task={delivery.task_id}")
    return hint


async def compact_tool_context(
    *,
    messages: list[dict[str, Any]],
    conversation_source: str,
    turn: int,
) -> None:
    """按 Web/企微既有预算压缩已完成工具轮次。"""
    from core.config import get_settings
    from services.handlers.context_compressor import (
        compact_loop_with_summary,
        compact_stale_by_user_turns,
        compact_stale_tool_results,
        enforce_history_budget_sync,
        enforce_tool_budget,
    )

    settings = get_settings()
    if conversation_source == "wecom":
        compact_stale_tool_results(
            messages,
            settings.context_tool_keep_turns,
        )
        enforce_tool_budget(messages, settings.context_tool_token_budget)
        enforce_history_budget_sync(
            messages,
            settings.context_history_token_budget,
        )
        if turn >= 3:
            await compact_loop_with_summary(
                messages,
                settings.context_max_tokens,
                settings.context_loop_summary_trigger,
            )
        return

    compact_stale_by_user_turns(
        messages,
        keep_user_turns=settings.context_web_keep_user_turns,
        capacity_trigger=settings.context_web_compact_trigger,
        max_tokens=settings.context_web_max_tokens,
    )
    enforce_tool_budget(messages, settings.context_web_tool_token_budget)
    enforce_history_budget_sync(
        messages,
        settings.context_web_history_token_budget,
    )
    if turn >= 3:
        await compact_loop_with_summary(
            messages,
            settings.context_web_max_tokens,
            settings.context_web_compact_trigger,
        )
