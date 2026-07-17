"""ChatHandler 非流式兼容入口与工具结果转换。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from schemas.message import ContentPart, TextPart


@dataclass
class GenerateResult:
    """企微等非 WebSocket 通道使用的完整生成结果。"""

    parts: list[ContentPart]
    content_blocks: list[dict[str, Any]] = field(default_factory=list)
    tool_digest: dict[str, Any] | None = None


def unpack_tool_result(result: Any) -> Any:
    """把工具返回值转换为模型上下文内容。"""
    from schemas.multimodal import FileReadResult
    from services.agent.agent_result import AgentResult

    if isinstance(result, AgentResult):
        return result.to_message_content()
    if isinstance(result, FileReadResult):
        return result.text
    if isinstance(result, str):
        return result
    return str(result)


def extract_display_text(result: Any) -> str:
    """提取工具结果的原始展示文本。"""
    from schemas.multimodal import FileReadResult
    from services.agent.agent_result import AgentResult

    if isinstance(result, AgentResult):
        return result.summary or ""
    if isinstance(result, FileReadResult):
        return result.text or ""
    if isinstance(result, str):
        return result
    return str(result)


class ChatGenerateMixin:
    """非流式生成兼容能力，由 ChatHandler 继承。"""

    def _get_conv_source(self, conversation_id: str) -> str:
        """读取并按 conversation 缓存来源字段。"""
        cache = getattr(self, "_conv_source_cache", None)
        if cache is None:
            cache = {}
            self._conv_source_cache = cache
        if conversation_id in cache:
            return cache[conversation_id]

        source = ""
        try:
            response = (
                self.db.table("conversations")
                .select("source")
                .eq("id", conversation_id)
                .maybe_single()
                .execute()
            )
            if response and response.data:
                source = response.data.get("source") or ""
        except Exception as error:
            logger.warning(
                f"_get_conv_source failed | "
                f"conversation_id={conversation_id} | error={error}"
            )
        cache[conversation_id] = source
        return source

    async def generate_complete(
        self,
        content: list[ContentPart],
        user_id: str,
        conversation_id: str,
        model_id: str | None = None,
        context_anchor: Any = None,
    ) -> GenerateResult:
        """通过统一执行内核生成完整结果，并保留企微友好降级。"""
        from services.adapters.factory import DEFAULT_MODEL_ID
        from services.handlers.chat.execution_engine import (
            ChatExecutionRequest,
            execute_chat,
        )

        try:
            result = await execute_chat(
                handler=self,
                request=ChatExecutionRequest(
                    content=content,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    task_id="wecom_task",
                    message_id="wecom_msg",
                    model_id=(
                        DEFAULT_MODEL_ID
                        if not model_id or model_id == "auto"
                        else model_id
                    ),
                    context_anchor=context_anchor,
                    permission_mode="auto",
                    calculate_credits=False,
                ),
                cancellation_event=asyncio.Event(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                f"generate_complete error | "
                f"conversation_id={conversation_id} | user_id={user_id} | "
                f"error={error}"
            )
            return GenerateResult(
                parts=[TextPart(text="生成回复时遇到了问题，请稍后再试。")]
            )
        return GenerateResult(
            parts=result.parts,
            content_blocks=result.content_blocks,
            tool_digest=result.tool_digest,
        )
