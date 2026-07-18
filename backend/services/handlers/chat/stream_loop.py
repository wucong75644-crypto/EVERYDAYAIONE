"""Chat 多轮流式工具循环协调器。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from schemas.websocket import build_content_block_add
from services.handlers.chat.stream_session import (
    StreamDelivery,
    StreamTotals,
    StreamTurnResult,
    read_stream_turn,
)
from services.handlers.chat.tool_loop import (
    append_tool_images,
    apply_tool_results,
    begin_tool_calls,
    compact_tool_context,
    prepare_tool_turn,
    push_emit_payloads,
    push_form_block,
)


class ChatStreamLoop:
    """执行模型流与工具的多轮循环，不构造或提交最终任务终态。"""

    def __init__(
        self,
        *,
        handler: Any,
        prepared: Any,
        delivery: StreamDelivery,
        websocket: Any,
        thinking_effort: str | None,
        thinking_mode: str | None,
    ) -> None:
        self.handler = handler
        self.prepared = prepared
        self.delivery = delivery
        self.websocket = websocket
        self.thinking_effort = thinking_effort
        self.thinking_mode = thinking_mode
        self.totals = StreamTotals()
        self.content_blocks: list[dict[str, Any]] = []
        self.turn_result = _empty_turn_result()
        self.empty_output_retried = False
        from services.agent.runtime.runtime_state import RuntimeState

        self.runtime_state = getattr(
            prepared,
            "runtime_state",
            RuntimeState.observing(),
        )

    async def run(self) -> None:
        while not self.prepared.budget.stop_reason:
            if await self._cancel_at("loop_top"):
                break
            self.prepared.budget.use_turn()
            turn = self.prepared.budget.turns_used - 1
            await self._read_turn(turn)
            if await self._cancel_at(
                "stream",
                partial_text=self.turn_result.text,
                partial_thinking=self.turn_result.thinking,
            ):
                break
            if not self.turn_result.tool_calls:
                if self._retry_empty_output(turn):
                    continue
                if self.runtime_state.should_continue_after_plain_text():
                    self._append_completion_reminder()
                    continue
                break
            await self._append_intermediate_blocks()
            form_submitted = await self._execute_tool_turn(turn)
            if form_submitted or await self._cancel_at("post_tool"):
                break
            await self._after_tool_turn(turn)
        if (
            self.prepared.budget.stop_reason
            and self.runtime_state.contract.enabled
        ):
            self.runtime_state.evaluate(budget_exhausted=True)

    async def _read_turn(self, turn: int) -> None:
        tools = prepare_tool_turn(
            core_tools=self.prepared.core_tools,
            discovered_names=self.prepared.tool_context.discovered_tools,
            org_id=self.handler.org_id,
            turn=turn,
            messages=self.prepared.messages,
            tool_context=self.prepared.tool_context,
            permission=self.prepared.permission,
            runtime_state=self.runtime_state,
        )
        self.prepared.stream_kwargs["tools"] = self.runtime_state.final_tools(
            tools
        )
        self.turn_result = await read_stream_turn(
            adapter=self.prepared.adapter,
            messages=self.prepared.messages,
            stream_kwargs=self.prepared.stream_kwargs,
            thinking_effort=self.thinking_effort,
            thinking_mode=self.thinking_mode,
            delivery=self.delivery,
            totals=self.totals,
            content_blocks=self.content_blocks,
            websocket=self.websocket,
            save_accumulated=self.handler._save_accumulated_content,
            buffer_output=self.runtime_state.should_buffer_output,
        )
        if (
            self.runtime_state.grounded_final_pending
            and not self.turn_result.tool_calls
        ):
            await self._replace_with_grounded_final()

    async def _replace_with_grounded_final(self) -> None:
        from services.agent.runtime.grounded_final import build_grounded_final
        from schemas.websocket import build_message_chunk

        text = build_grounded_final(self.runtime_state)
        self.turn_result.text = text
        self.turn_result.thinking = ""
        self.turn_result.thinking_committed = True
        self.totals.text += text
        if text:
            await self.websocket.send_to_task_or_user(
                self.delivery.task_id,
                self.delivery.user_id,
                build_message_chunk(
                    task_id=self.delivery.task_id,
                    conversation_id=self.delivery.conversation_id,
                    message_id=self.delivery.message_id,
                    chunk=text,
                ),
            )

    def _retry_empty_output(self, turn: int) -> bool:
        if self.turn_result.text or self.prepared.budget.turns_used <= 1:
            return False
        logger.warning(
            f"Empty output detected | task={self.delivery.task_id} | "
            f"turn={turn + 1} | finish_reason={self.totals.last_finish_reason} | "
            f"thinking_len={len(self.turn_result.thinking)} | "
            f"retried={self.empty_output_retried}"
        )
        if not self.empty_output_retried:
            self.empty_output_retried = True
            self.thinking_mode = None
            self.prepared.messages.append(
                {
                    "role": "user",
                    "content": "请根据刚才的工具执行结果，直接告诉我结论。",
                }
            )
            return True
        fallback = _last_tool_output(self.content_blocks)
        self.turn_result.text = (
            "抱歉，我在整理回复时遇到了问题。以下是工具返回的原始结果：\n\n"
            + (fallback or "（无工具输出）")
        )
        self.totals.text += self.turn_result.text
        return False

    async def _append_intermediate_blocks(self) -> None:
        if not self.turn_result.thinking_committed:
            duration = _thinking_duration(self.turn_result)
            await self._append_and_push(
                {
                    "type": "thinking",
                    "text": self.turn_result.thinking,
                    "duration_ms": duration,
                },
                "thinking",
            )
        if self.turn_result.text:
            await self._append_and_push(
                {"type": "text", "text": self.turn_result.text},
                "text",
            )
            asyncio.create_task(
                self.handler._save_accumulated_blocks(
                    self.delivery.task_id,
                    self.content_blocks,
                )
            )

    async def _append_and_push(
        self,
        block: dict[str, Any],
        label: str,
    ) -> None:
        self.content_blocks.append(block)
        try:
            await self.websocket.send_to_task_or_user(
                self.delivery.task_id,
                self.delivery.user_id,
                build_content_block_add(
                    task_id=self.delivery.task_id,
                    conversation_id=self.delivery.conversation_id,
                    message_id=self.delivery.message_id,
                    block=block,
                ),
            )
        except Exception as error:
            logger.warning(
                f"{label} block push failed | "
                f"task={self.delivery.task_id} | {error}"
            )

    async def _execute_tool_turn(self, turn: int) -> bool:
        calls = sorted(
            self.turn_result.tool_calls.values(),
            key=lambda call: call.get("id", ""),
        )
        logger.info(
            f"Tool calls detected | task={self.delivery.task_id} | "
            f"turn={turn + 1} | tools={[call['name'] for call in calls]}"
        )
        self.message_position = len(self.prepared.messages)
        start_times = await begin_tool_calls(
            completed_calls=calls,
            turn_text=self.turn_result.text,
            turn=turn,
            messages=self.prepared.messages,
            content_blocks=self.content_blocks,
            delivery=self.delivery,
            websocket=self.websocket,
            save_blocks=self.handler._save_accumulated_blocks,
        )
        results = await self.handler._execute_tool_calls(
            calls,
            self.delivery.task_id,
            self.delivery.conversation_id,
            self.delivery.message_id,
            self.delivery.user_id,
            turn + 1,
            messages=self.prepared.messages,
            budget=self.prepared.budget,
            runtime_state=self.runtime_state,
        )
        image_urls = apply_tool_results(
            tool_results=results,
            messages=self.prepared.messages,
            content_blocks=self.content_blocks,
            start_times=start_times,
            tool_context=self.prepared.tool_context,
            runtime_state=self.runtime_state,
        )
        asyncio.create_task(
            self.handler._save_accumulated_blocks(
                self.delivery.task_id,
                self.content_blocks,
            )
        )
        append_tool_images(self.prepared.messages, image_urls)
        self._append_data_context()
        await self._push_pending_payloads()
        if (
            self.runtime_state.contract.enabled
            and self.runtime_state.evaluate().decision.value == "finalize"
        ):
            self.runtime_state.request_final_synthesis()
            self._append_final_synthesis_prompt()
        return await self._push_pending_form()

    def _append_completion_reminder(self) -> None:
        self.prepared.messages.append(
            {
                "role": "system",
                "content": (
                    "交付合同尚未满足，请继续使用可用工具完成缺失产物。"
                    f"当前状态：{self.runtime_state.last_completion.reason}"
                ),
            }
        )

    def _append_final_synthesis_prompt(self) -> None:
        self.prepared.messages.append(
            {
                "role": "system",
                "content": (
                    "必需产物已经通过运行时验证。请基于现有工具结果完成一次"
                    "简洁文字收尾；不得再次调用工具。"
                ),
            }
        )

    def _append_data_context(self) -> None:
        from services.agent.runtime.data_compute import build_data_context_prompt

        prompt = build_data_context_prompt(self.runtime_state)
        if prompt and not any(
            message.get("role") == "system" and message.get("content") == prompt
            for message in self.prepared.messages
        ):
            self.prepared.messages.append(
                {"role": "system", "content": prompt}
            )

    async def _push_pending_payloads(self) -> None:
        payloads = self.handler._pending_emit_payloads
        if not payloads:
            return
        await push_emit_payloads(
            payloads=payloads,
            content_blocks=self.content_blocks,
            delivery=self.delivery,
            websocket=self.websocket,
            save_blocks=self.handler._save_accumulated_blocks,
        )
        self.handler._pending_emit_payloads = []

    async def _push_pending_form(self) -> bool:
        hint = await push_form_block(
            form=getattr(self.handler, "_pending_form_block", None),
            content_blocks=self.content_blocks,
            delivery=self.delivery,
            websocket=self.websocket,
            save_blocks=self.handler._save_accumulated_blocks,
        )
        if not hint:
            return False
        self.handler._pending_form_block = None
        self.totals.text += hint
        return True

    async def _after_tool_turn(self, turn: int) -> None:
        steer_message = self.websocket.check_steer(self.delivery.task_id)
        if steer_message:
            logger.info(
                f"Steer detected | task={self.delivery.task_id} | "
                f"msg={steer_message[:50]}"
            )
            self.prepared.messages.append(
                {"role": "user", "content": steer_message}
            )
        from services.handlers.session_memory import extract_incremental

        new_messages = self.prepared.messages[self.message_position:]
        if new_messages:
            asyncio.create_task(extract_incremental(new_messages))
        await compact_tool_context(
            messages=self.prepared.messages,
            conversation_source=self.handler._get_conv_source(
                self.delivery.conversation_id
            ),
            turn=turn,
        )
        logger.info(
            f"Tool turn {turn + 1} complete | "
            f"task={self.delivery.task_id} | continuing loop"
        )

    async def _cancel_at(
        self,
        location: str,
        partial_text: str = "",
        partial_thinking: str = "",
    ) -> bool:
        if not self.websocket.is_cancelled(self.delivery.task_id):
            return False
        await self.handler._handle_user_cancel(
            self.delivery.task_id,
            self.delivery.message_id,
            self.delivery.conversation_id,
            self.prepared.messages,
            self.content_blocks,
            location,
            partial_text=partial_text,
            partial_thinking=partial_thinking,
        )
        return True


def _empty_turn_result() -> StreamTurnResult:
    return StreamTurnResult(
        text="",
        thinking="",
        thinking_committed=False,
        thinking_started_at=None,
        request_started_at=0,
        tool_calls={},
        cancelled=False,
    )


def _thinking_duration(result: StreamTurnResult) -> int:
    started_at = result.thinking_started_at or result.request_started_at
    return int((time.monotonic() - started_at) * 1000)


def _last_tool_output(blocks: list[dict[str, Any]]) -> str:
    for block in reversed(blocks):
        text = block.get("output") or block.get("text")
        if block.get("type") in {"tool_result", "tool_step"} and text:
            return str(text)[:2000]
    return ""
