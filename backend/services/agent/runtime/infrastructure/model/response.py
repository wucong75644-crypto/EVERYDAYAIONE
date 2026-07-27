"""Provider 流响应的闭合聚合、校验与脱敏 receipt。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from services.agent.runtime.domain import StopReason
from services.agent.runtime.ports.model import (
    ModelOutput,
    ModelOutputKind,
    ModelResponseReceipt,
    ModelToolCall,
    ModelUsage,
)


class ResponseAccumulator:
    """累积一个 Provider attempt，拒绝半截 Tool Call。"""

    def __init__(self, model_step_id: str) -> None:
        self.model_step_id = model_step_id
        self.text = ""
        self.finish_reason: str | None = None
        self.usage = ModelUsage()
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.response_started = False
        self.explicit_refusal = False

    def add(self, chunk: Any) -> None:
        self.response_started = True
        if chunk.content:
            self.text += chunk.content
        if chunk.finish_reason:
            self.finish_reason = str(chunk.finish_reason)
        if getattr(chunk, "refusal", False):
            self.explicit_refusal = True
        if chunk.tool_calls:
            self._add_tool_deltas(chunk.tool_calls)
        self.usage = ModelUsage(
            input_tokens=max(
                self.usage.input_tokens,
                _token_value(chunk, "prompt_tokens"),
            ),
            output_tokens=max(
                self.usage.output_tokens,
                _token_value(chunk, "completion_tokens"),
            ),
            reasoning_tokens=max(
                self.usage.reasoning_tokens,
                _token_value(chunk, "reasoning_tokens"),
            ),
            cache_read_tokens=max(
                self.usage.cache_read_tokens,
                _token_value(chunk, "cached_tokens"),
            ),
            cache_write_tokens=max(
                self.usage.cache_write_tokens,
                _token_value(chunk, "cache_creation_tokens"),
            ),
        )

    def complete(
        self,
        *,
        provider: str,
        structured_output: bool,
        schema_revision: str | None,
    ) -> tuple[
        StopReason,
        ModelOutput | None,
        tuple[ModelToolCall, ...],
        ModelResponseReceipt,
        str,
    ]:
        try:
            calls = self._complete_tool_calls()
            output = self._build_output(
                structured_output,
                schema_revision,
            )
        except (ValueError, json.JSONDecodeError, TypeError):
            calls = ()
            output = None
            stop_reason = StopReason.PROTOCOL_ERROR
            response_hash = _invalid_response_hash(
                self.text,
                self.tool_calls,
                self.usage,
                self.finish_reason,
            )
            receipt = ModelResponseReceipt(
                output_kind=None,
                output_characters=len(self.text),
                tool_call_count=0,
                invalid_tool_call_count=len(self.tool_calls),
                usage=self.usage,
                provider=provider,
            )
            return stop_reason, output, calls, receipt, response_hash
        stop_reason = map_stop_reason(
            self.finish_reason,
            has_output=output is not None,
            has_tool_calls=bool(calls),
            structured_output=structured_output,
            explicit_refusal=self.explicit_refusal,
        )
        response_hash = _response_hash(
            output=output,
            tool_calls=calls,
            usage=self.usage,
            provider_stop_reason=self.finish_reason,
        )
        receipt = ModelResponseReceipt(
            output_kind=output.kind if output else None,
            output_characters=len(output.content) if output else 0,
            tool_call_count=len(calls),
            invalid_tool_call_count=0,
            usage=self.usage,
            provider=provider,
        )
        return stop_reason, output, calls, receipt, response_hash

    def _add_tool_deltas(self, deltas: list[Any]) -> None:
        for delta in deltas:
            entry = self.tool_calls.setdefault(
                int(delta.index),
                {"id": "", "name": "", "arguments": ""},
            )
            if delta.id:
                entry["id"] = str(delta.id)
            if delta.name:
                entry["name"] = str(delta.name)
            if delta.arguments_delta:
                entry["arguments"] += str(delta.arguments_delta)

    def _complete_tool_calls(self) -> tuple[ModelToolCall, ...]:
        calls: list[ModelToolCall] = []
        for index, value in sorted(self.tool_calls.items()):
            provider_call_id = value["id"] or None
            calls.append(ModelToolCall(
                index=index,
                call_id=(
                    provider_call_id
                    or _derived_tool_call_id(
                        self.model_step_id,
                        index,
                        value["name"],
                        value["arguments"],
                    )
                ),
                name=value["name"],
                arguments_json=value["arguments"],
                provider_call_id=provider_call_id,
            ))
        return tuple(calls)

    def _build_output(
        self,
        structured_output: bool,
        schema_revision: str | None,
    ) -> ModelOutput | None:
        if not self.text.strip():
            return None
        if structured_output:
            parsed = json.loads(self.text)
            canonical = json.dumps(
                parsed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            return ModelOutput(
                kind=ModelOutputKind.STRUCTURED,
                content=canonical,
                schema_revision=schema_revision,
            )
        return ModelOutput(kind=ModelOutputKind.TEXT, content=self.text)


def map_stop_reason(
    provider_reason: str | None,
    *,
    has_output: bool,
    has_tool_calls: bool,
    structured_output: bool,
    explicit_refusal: bool = False,
) -> StopReason:
    """未知 reason 失败关闭为 protocol_error，绝不静默 final。"""
    normalized = (provider_reason or "").strip().lower()
    if explicit_refusal:
        return StopReason.MODEL_REFUSAL
    if has_tool_calls:
        return (
            StopReason.TOOL_CALLS
            if normalized in ("", "tool_calls", "function_call", "stop")
            else StopReason.PROTOCOL_ERROR
        )
    if normalized in ("length", "max_tokens", "finishreason.max_tokens"):
        return StopReason.LENGTH
    if normalized in {
        "content_filter",
        "safety",
        "finishreason.safety",
        "blocklist",
        "prohibited_content",
        "spii",
        "recitation",
    }:
        return StopReason.CONTENT_FILTER
    if normalized in ("refusal", "model_refusal"):
        return StopReason.MODEL_REFUSAL
    if normalized in ("stop", "finishreason.stop"):
        if not has_output:
            return StopReason.PROTOCOL_ERROR
        return (
            StopReason.STRUCTURED_FINAL
            if structured_output else StopReason.FINAL
        )
    return StopReason.PROTOCOL_ERROR


def _response_hash(
    *,
    output: ModelOutput | None,
    tool_calls: tuple[ModelToolCall, ...],
    usage: ModelUsage,
    provider_stop_reason: str | None,
) -> str:
    value = {
        "output": asdict(output) if output else None,
        "tool_calls": [asdict(call) for call in tool_calls],
        "usage": asdict(usage),
        "provider_stop_reason": provider_stop_reason,
    }
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _invalid_response_hash(
    text: str,
    tool_calls: dict[int, dict[str, Any]],
    usage: ModelUsage,
    provider_stop_reason: str | None,
) -> str:
    value = {
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "tool_call_hash": hashlib.sha256(
            json.dumps(
                tool_calls,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "usage": asdict(usage),
        "provider_stop_reason": provider_stop_reason,
    }
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _token_value(chunk: Any, field_name: str) -> int:
    return max(0, int(getattr(chunk, field_name, 0) or 0))


def _derived_tool_call_id(
    model_step_id: str,
    index: int,
    name: str,
    arguments_json: str,
) -> str:
    raw = f"{model_step_id}\0{index}\0{name}\0{arguments_json}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"runtime-{digest}"
