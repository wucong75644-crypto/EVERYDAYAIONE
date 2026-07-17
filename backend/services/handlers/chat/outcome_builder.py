"""将 Chat 执行过程中的内容块转换为稳定的消息协议。"""

from __future__ import annotations

from typing import Any

from schemas.message import (
    ChartPart,
    ContentPart,
    FilePart,
    FormPart,
    ImagePart,
    TextPart,
    ThinkingPart,
    ToolResultPart,
    ToolStepPart,
)


def append_final_turn_blocks(
    blocks: list[dict[str, Any]],
    *,
    thinking: str,
    thinking_committed: bool,
    thinking_duration_ms: int,
    text: str,
) -> None:
    """按原流式时序将最后一轮尚未收割的 thinking/text 追加到块列表。"""
    if thinking and not thinking_committed:
        blocks.append(
            {
                "type": "thinking",
                "text": thinking,
                "duration_ms": thinking_duration_ms,
            }
        )
    if text:
        blocks.append({"type": "text", "text": text})


def build_content_parts(
    blocks: list[dict[str, Any]],
    *,
    fallback_text: str,
    fallback_thinking: str = "",
    fallback_thinking_duration_ms: int | None = None,
) -> list[ContentPart]:
    """从可信内部内容块构建 ContentPart；普通文本不会被扫描成媒体。"""
    if not blocks:
        parts: list[ContentPart] = [TextPart(text=fallback_text)]
        if fallback_thinking:
            parts.insert(
                0,
                ThinkingPart(
                    text=fallback_thinking,
                    duration_ms=fallback_thinking_duration_ms,
                ),
            )
        return parts

    parts = []
    for block in blocks:
        part = _build_part(block)
        if part is not None:
            parts.append(part)
    return parts


def _build_part(block: dict[str, Any]) -> ContentPart | None:
    block_type = block.get("type")
    if block_type == "thinking":
        return ThinkingPart(
            text=block["text"],
            duration_ms=block.get("duration_ms"),
        )
    if block_type == "text":
        return TextPart(text=block["text"])
    if block_type == "tool_step":
        return ToolStepPart(
            tool_name=block["tool_name"],
            tool_call_id=block["tool_call_id"],
            status=block.get("status", "completed"),
            input=block.get("input"),
            code=block.get("code"),
            output=block.get("output"),
            elapsed_ms=block.get("elapsed_ms"),
        )
    if block_type == "tool_result":
        return ToolResultPart(
            tool_name=block["tool_name"],
            text=block["text"],
            files=block.get("files", []),
        )
    if block_type == "image":
        return ImagePart(
            url=block.get("url"),
            alt=block.get("alt"),
            width=block.get("width"),
            height=block.get("height"),
            failed=block.get("failed"),
            error=block.get("error"),
            retry_context=block.get("retry_context"),
        )
    if block_type == "file":
        return FilePart(
            url=block["url"],
            name=block["name"],
            mime_type=block["mime_type"],
            size=block.get("size"),
            workspace_path=block.get("workspace_path"),
        )
    if block_type == "chart":
        return ChartPart(
            option=block["option"],
            title=block.get("title", ""),
            chart_type=block.get("chart_type", ""),
            spec_format=block.get("spec_format", "echarts"),
        )
    if block_type == "form":
        return FormPart(**block)
    return None
