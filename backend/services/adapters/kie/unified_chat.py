"""KIE 现有 Chat API 到统一 BaseChatAdapter 输出的薄适配。"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from ..base import (
    ChatResponse,
    CostEstimate as BaseCostEstimate,
    ModelProvider,
    StreamChunk,
    ToolCallDelta,
)
from .models import ReasoningEffort, ThinkingMode


class KieUnifiedChatMixin:
    """保持 KieChatAdapter 的既有统一接口和 Provider 调用方式。"""

    @property
    def provider(self) -> ModelProvider:
        return ModelProvider.KIE

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        formatted_messages = self.format_messages_from_history(messages)
        effort = (
            ReasoningEffort(reasoning_effort)
            if reasoning_effort else ReasoningEffort.HIGH
        )
        mode = ThinkingMode(thinking_mode) if thinking_mode else None
        stream = await self.chat(
            messages=formatted_messages,
            stream=True,
            include_thoughts=False,
            reasoning_effort=effort,
            thinking_mode=mode,
            **kwargs,
        )
        async for chunk in stream:
            yield _to_stream_chunk(chunk)

    async def chat_sync(
        self,
        messages: List[Dict[str, Any]],
        reasoning_effort: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        formatted_messages = self.format_messages_from_history(messages)
        effort = (
            ReasoningEffort(reasoning_effort)
            if reasoning_effort else ReasoningEffort.HIGH
        )
        mode = ThinkingMode(thinking_mode) if thinking_mode else None
        response = await self.chat(
            messages=formatted_messages,
            stream=False,
            include_thoughts=False,
            reasoning_effort=effort,
            thinking_mode=mode,
            **kwargs,
        )
        content = (
            response.choices[0].delta.content or ""
            if response.choices else ""
        )
        return ChatResponse(
            content=content,
            finish_reason=(
                response.choices[0].finish_reason
                if response.choices else None
            ),
            prompt_tokens=(
                response.usage.prompt_tokens if response.usage else 0
            ),
            completion_tokens=(
                response.usage.completion_tokens if response.usage else 0
            ),
        )

    def estimate_cost_unified(
        self,
        input_tokens: int,
        output_tokens: int,
    ) -> BaseCostEstimate:
        result = self.estimate_cost(input_tokens, output_tokens)
        return BaseCostEstimate(
            model=result.model,
            estimated_cost_usd=result.estimated_cost_usd,
            estimated_credits=result.estimated_credits,
            breakdown=result.breakdown,
        )

    async def close(self) -> None:
        await self.client.close()


def _to_stream_chunk(chunk: Any) -> StreamChunk:
    delta = chunk.choices[0].delta if chunk.choices else None
    usage = chunk.usage
    details = usage.completion_tokens_details if usage else None
    return StreamChunk(
        content=delta.content if delta else None,
        thinking_content=delta.reasoning_content if delta else None,
        finish_reason=(
            chunk.choices[0].finish_reason if chunk.choices else None
        ),
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        reasoning_tokens=details.reasoning_tokens if details else 0,
        credits_consumed=chunk.credits_consumed,
        tool_calls=_tool_call_deltas(delta),
    )


def _tool_call_deltas(delta: Any) -> list[ToolCallDelta] | None:
    if not delta or not delta.tool_calls:
        return None
    return [
        ToolCallDelta(
            index=call.index,
            id=call.id,
            name=call.function.name if call.function else None,
            arguments_delta=(
                call.function.arguments if call.function else None
            ),
        )
        for call in delta.tool_calls
    ]
