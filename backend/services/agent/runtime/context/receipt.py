"""不含正文的上下文影子回执。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from services.handlers.context_compressor.tokens import estimate_tokens


_CHARS_PER_TOKEN = 2.5


@dataclass(frozen=True)
class ContextBlockReceipt:
    """单条模型消息的安全观测字段。"""

    index: int
    role: str
    content_kind: str
    chars: int
    estimated_tokens: int
    content_hash: str


@dataclass(frozen=True)
class ContextReceipt:
    """一次 Provider 请求前的上下文影子回执，不保存敏感正文。"""

    schema_version: int
    conversation_id: str
    task_id: str
    model_id: str
    message_count: int
    tool_count: int
    estimated_prompt_tokens: int
    estimated_tool_tokens: int
    prefix_hash: str
    blocks: tuple[ContextBlockReceipt, ...]

    def to_log_fields(self) -> dict[str, Any]:
        """转换为结构化日志字段。"""
        return asdict(self)


def build_context_receipt(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    conversation_id: str,
    task_id: str,
    model_id: str,
) -> ContextReceipt:
    """只读生成影子回执；不得修改 Provider 将消费的 messages/tools。"""
    blocks = tuple(
        ContextBlockReceipt(
            index=index,
            role=str(message.get("role") or ""),
            content_kind=_content_kind(message.get("content")),
            chars=_message_chars(message),
            estimated_tokens=estimate_tokens([message]),
            content_hash=_hash_value(message),
        )
        for index, message in enumerate(messages)
    )
    tool_chars = len(_canonical_json(tools))
    return ContextReceipt(
        schema_version=1,
        conversation_id=conversation_id,
        task_id=task_id,
        model_id=model_id,
        message_count=len(messages),
        tool_count=len(tools),
        estimated_prompt_tokens=estimate_tokens(messages),
        estimated_tool_tokens=int(tool_chars / _CHARS_PER_TOKEN),
        prefix_hash=_hash_value({"messages": messages, "tools": tools}),
        blocks=blocks,
    )


def _content_kind(content: Any) -> str:
    if isinstance(content, str):
        return "text"
    if isinstance(content, list):
        return "parts"
    if content is None:
        return "empty"
    return type(content).__name__


def _message_chars(message: dict[str, Any]) -> int:
    content = message.get("content")
    chars = len(content) if isinstance(content, str) else 0
    if isinstance(content, list):
        chars += sum(
            len(str(part.get("text") or part.get("url") or ""))
            for part in content
            if isinstance(part, dict)
        )
    chars += sum(
        len(str(call.get("function", {}).get("arguments") or ""))
        for call in message.get("tool_calls") or []
        if isinstance(call, dict)
    )
    return chars


def _hash_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
