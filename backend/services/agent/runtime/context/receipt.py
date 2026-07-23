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
class ContextEpoch:
    """稳定前缀发生变化前的一段请求序列。"""

    epoch_id: str
    base_revision: int
    stable_prefix_blocks: int
    stable_prefix_hash: str


@dataclass(frozen=True)
class CacheIdentity:
    """不含正文的 Provider 前缀缓存归因。"""

    route_hash: str
    stable_prefix_hash: str
    dynamic_suffix_hash: str
    tool_schema_hash: str


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
    epoch: ContextEpoch
    cache_identity: CacheIdentity
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
    base_revision: int = 0,
    stable_prefix_blocks: int | None = None,
) -> ContextReceipt:
    """只读生成影子回执；不得修改 Provider 将消费的 messages/tools。"""
    stable_count = (
        _infer_stable_prefix_blocks(messages)
        if stable_prefix_blocks is None
        else max(0, min(stable_prefix_blocks, len(messages)))
    )
    stable_messages = messages[:stable_count]
    dynamic_messages = messages[stable_count:]
    stable_hash = _hash_value(stable_messages)
    dynamic_hash = _hash_value(dynamic_messages)
    tool_hash = _hash_value(tools)
    route_hash = _hash_value({
        "conversation_id": conversation_id,
        "model_id": model_id,
    })
    epoch_id = _hash_value({
        "conversation_id": conversation_id,
        "base_revision": base_revision,
        "stable_prefix_hash": stable_hash,
    })
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
        epoch=ContextEpoch(
            epoch_id=epoch_id,
            base_revision=base_revision,
            stable_prefix_blocks=stable_count,
            stable_prefix_hash=stable_hash,
        ),
        cache_identity=CacheIdentity(
            route_hash=route_hash,
            stable_prefix_hash=stable_hash,
            dynamic_suffix_hash=dynamic_hash,
            tool_schema_hash=tool_hash,
        ),
        blocks=blocks,
    )


def _infer_stable_prefix_blocks(messages: list[dict[str, Any]]) -> int:
    """识别 PromptBuilder 当前两种稳定前缀编码。"""
    if not messages or messages[0].get("role") != "system":
        return 0
    content = messages[0].get("content")
    if isinstance(content, list) and any(
        isinstance(part, dict) and part.get("cache_control")
        for part in content
    ):
        return 1
    if len(messages) > 1 and messages[1].get("role") == "system":
        return 2
    return 1


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
