"""Chat 单工具执行结果的分类投递、审计与错误收口。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loguru import logger

from schemas.websocket import build_tool_result


@dataclass(frozen=True)
class ToolResultContext:
    task_id: str
    conversation_id: str
    message_id: str
    user_id: str
    tool_name: str
    tool_call_id: str
    turn: int
    args: dict[str, Any]
    elapsed_ms: int


class ChatToolResultMixin:
    async def _process_tool_result(
        self,
        tool_call: dict[str, Any],
        result: Any,
        context: ToolResultContext,
    ) -> tuple:
        from schemas.multimodal import FileReadResult
        from services.agent.agent_result import AgentResult
        from services.scheduler.chat_task_manager import FormBlockResult

        if isinstance(result, AgentResult):
            return await ChatToolResultMixin._process_agent_result(
                self, tool_call, result, context,
            )
        if isinstance(result, FormBlockResult):
            return await ChatToolResultMixin._process_form_result(
                self, tool_call, result, context,
            )
        if isinstance(result, FileReadResult):
            return await ChatToolResultMixin._process_file_read_result(
                self, tool_call, result, context,
            )
        return await ChatToolResultMixin._process_string_result(
            self, tool_call, result, context,
        )

    async def _process_agent_result(
        self,
        tool_call: dict[str, Any],
        result: Any,
        context: ToolResultContext,
    ) -> tuple:
        from services.handlers.chat_generate_mixin import extract_display_text

        display = extract_display_text(result)
        await ChatToolResultMixin._send_tool_result(
            self, context, not result.is_failure,
            result.summary[:100] if result.summary else "",
        )
        await ChatToolResultMixin._finish_tool_step(
            self, context, not result.is_failure, display,
        )
        ChatToolResultMixin._audit_tool_result(
            self, context, len(result.summary), result.status,
        )
        return tool_call, result, result.is_failure, display

    async def _process_form_result(
        self,
        tool_call: dict[str, Any],
        result: Any,
        context: ToolResultContext,
    ) -> tuple:
        self._pending_form_block = result.form
        display = "表单已展示"
        await ChatToolResultMixin._send_tool_result(
            self, context, True, display,
        )
        await ChatToolResultMixin._finish_tool_step(
            self, context, True, display,
        )
        ChatToolResultMixin._audit_tool_result(
            self, context, len(json.dumps(result.form)), "success",
        )
        return tool_call, result.llm_hint, False, display

    async def _process_file_read_result(
        self,
        tool_call: dict[str, Any],
        result: Any,
        context: ToolResultContext,
    ) -> tuple:
        from services.handlers.chat_generate_mixin import extract_display_text

        display = extract_display_text(result)
        await ChatToolResultMixin._send_tool_result(
            self, context, True, result.text[:100] if result.text else "",
        )
        await ChatToolResultMixin._finish_tool_step(
            self, context, True, display,
        )
        ChatToolResultMixin._audit_tool_result(
            self, context, len(result.text), "success",
        )
        return tool_call, result, False, display

    async def _process_string_result(
        self,
        tool_call: dict[str, Any],
        result: Any,
        context: ToolResultContext,
    ) -> tuple:
        from services.agent.tool_result_envelope import (
            PERSISTED_OUTPUT_TAG,
            wrap_for_erp_agent,
        )
        from services.handlers.chat_generate_mixin import extract_display_text

        display = extract_display_text(result)
        raw_summary = result[:100] if result else ""
        wrapped = wrap_for_erp_agent(context.tool_name, result)
        truncated = bool(
            wrapped and (
                PERSISTED_OUTPUT_TAG in wrapped
                or "⚠ 输出过长" in wrapped
            )
        )
        await ChatToolResultMixin._send_tool_result(
            self, context, True, raw_summary,
        )
        await ChatToolResultMixin._finish_tool_step(
            self, context, True, display,
        )
        ChatToolResultMixin._audit_tool_result(
            self, context, len(wrapped), "success", truncated,
        )
        return tool_call, wrapped, False, display

    async def _process_tool_exception(
        self,
        tool_call: dict[str, Any],
        error: Exception,
        context: ToolResultContext,
    ) -> tuple:
        logger.error(
            f"Tool execution error | tool={context.tool_name} "
            f"| task={context.task_id} | error={error}"
        )
        error_message = f"工具执行失败: {error}"
        display = str(error)
        await ChatToolResultMixin._send_tool_result(
            self, context, False, str(error)[:100],
        )
        await ChatToolResultMixin._finish_tool_step(
            self, context, False, display,
        )
        ChatToolResultMixin._audit_tool_result(
            self, context, len(error_message), "error",
        )
        return tool_call, error_message, True, display

    async def _send_tool_result(
        self,
        context: ToolResultContext,
        success: bool,
        summary: str,
    ) -> None:
        from services.handlers import chat_tool_mixin

        await chat_tool_mixin.ws_manager.send_to_task_or_user(
            context.task_id,
            context.user_id,
            build_tool_result(
                task_id=context.task_id,
                conversation_id=context.conversation_id,
                message_id=context.message_id,
                tool_name=context.tool_name,
                tool_call_id=context.tool_call_id,
                success=success,
                summary=summary,
                turn=context.turn,
            ),
        )

    async def _finish_tool_step(
        self,
        context: ToolResultContext,
        success: bool,
        output: str,
    ) -> None:
        await self._push_tool_step_update(
            context.task_id,
            context.conversation_id,
            context.message_id,
            context.user_id,
            context.tool_name,
            context.tool_call_id,
            success=success,
            output=output,
            elapsed_ms=context.elapsed_ms,
        )

    def _audit_tool_result(
        self,
        context: ToolResultContext,
        result_length: int,
        status: str,
        truncated: bool = False,
    ) -> None:
        self._emit_tool_audit(
            context.task_id,
            context.conversation_id,
            context.user_id,
            context.tool_name,
            context.tool_call_id,
            context.turn,
            context.args,
            result_length,
            context.elapsed_ms,
            status,
            truncated,
        )
