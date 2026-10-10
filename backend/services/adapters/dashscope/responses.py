"""Wire conversion inside the existing DashScope adapter (not a provider)."""

from __future__ import annotations

import copy
import json
from urllib.parse import urlsplit

from ..types import StreamChunk, ToolCallDelta

# Explicit model capabilities; never infer built-in tools from the provider name.
BUILTIN_TOOLS = {
    "qwen3.8-max": ("web_search", "web_extractor"),
    "qwen3.8-max-0902": ("web_search", "web_extractor"),
    "qwen3.8-flash": ("web_search", "web_extractor"),
    "kimi-k3": ("web_search",),
}
CACHE_MODELS = frozenset({"qwen3.8-max", "qwen3.8-max-0902", "qwen3.8-flash"})


def stable_tools(tools: list[dict]) -> list[dict]:
    normalized = json.loads(json.dumps(tools, sort_keys=True, ensure_ascii=False))
    return sorted(normalized, key=lambda t: (t.get("type", ""), t.get("name", t.get("function", {}).get("name", ""))))


def chat_messages(messages: list[dict], *, cache: bool | None) -> list[dict]:
    """Never leak provider replay metadata to Chat or another model."""
    result = []
    for message in messages:
        item = {k: copy.deepcopy(v) for k, v in message.items()
                if k in {"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content"}}
        if cache is False and isinstance(item.get("content"), list):
            item["content"] = [{k: v for k, v in p.items() if k != "cache_control"}
                               for p in item["content"]]
        result.append(item)
    if cache:
        # Qwen merges system messages; keep the existing system boundary, then
        # cache complete history at the latest user message (at most four markers).
        markers = sum(1 for m in result if isinstance(m.get("content"), list)
                      for p in m["content"] if p.get("cache_control"))
        candidates = []
        if markers == 0:
            candidates.extend(m for m in result[:1] if m.get("role") == "system")
        user = next((m for m in reversed(result) if m.get("role") == "user"), None)
        if user is not None:
            candidates.append(user)
        for message in candidates:
            if markers >= 4:
                break
            content = message.get("content")
            if isinstance(content, str) and content:
                content = message["content"] = [{"type": "text", "text": content}]
            if isinstance(content, list):
                text = next((p for p in reversed(content) if p.get("type") == "text"), None)
                if text is not None and not text.get("cache_control"):
                    text["cache_control"] = {"type": "ephemeral"}
                    markers += 1
    return result


def response_input(messages: list[dict], model: str) -> list[dict]:
    result = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            result.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                           "output": message.get("content") or ""})
            continue
        replay = message.get("_dashscope_output") or {}
        if role == "assistant" and replay.get("model") == model:
            result.extend(copy.deepcopy(replay.get("items", [])))
        content = message.get("content")
        if content:
            if isinstance(content, list):
                converted = []
                for part in content:
                    kind = part.get("type")
                    if kind in {"text", "input_text", "output_text"}:
                        converted.append({"type": "output_text" if role == "assistant" else "input_text",
                                          "text": part["text"]})
                    elif kind == "image_url" and role == "user":
                        image = part["image_url"]
                        converted.append({"type": "input_image", "image_url": image["url"] if isinstance(image, dict) else image})
                    else:
                        # Reject unsupported multimodal parts rather than silently losing attachments.
                        raise ValueError(f"Unsupported Responses content type: {kind}")
                content = converted
            result.append({"role": role, "content": content})
        for call in message.get("tool_calls") or []:
            function = call["function"]
            result.append({"type": "function_call", "call_id": call["id"],
                           "name": function["name"], "arguments": function["arguments"]})
    return result


def response_tools(tools: list[dict], builtins: tuple[str, ...]) -> list[dict]:
    result = [{"type": name} for name in builtins]
    for tool in tools:
        if tool.get("type") != "function":
            raise ValueError("Unsupported DashScope Responses tool")
        function = tool["function"]
        if "web_search" in builtins and function["name"] == "web_search":
            continue
        result.append({"type": "function", **function})
    return stable_tools(result)


def usage_fields(usage: dict, *, responses: bool) -> dict:
    details = usage.get("input_tokens_details" if responses else "prompt_tokens_details") or {}
    creation = details.get("cache_creation_input_tokens", usage.get("cache_creation_input_tokens"))
    if creation is None and responses:
        # Bailian reports explicit Session-cache writes inside each billing step.
        creation = sum((step.get("prompt_tokens_details") or {}).get("cache_creation_input_tokens", 0) or 0
                       for step in usage.get("x_details") or [])
    return {
        "prompt_tokens": usage.get("input_tokens" if responses else "prompt_tokens", 0) or 0,
        "completion_tokens": usage.get("output_tokens" if responses else "completion_tokens", 0) or 0,
        "cached_tokens": details.get("cached_tokens", 0) or 0,
        "cache_creation_input_tokens": creation or 0,
        "builtin_tool_usage": {name: value["count"] for name, value in (usage.get("x_tools") or {}).items()
                               if name in {"web_search", "web_extractor"} and isinstance(value, dict)
                               and type(value.get("count")) is int and value["count"] >= 0},
    }


def public_sources(item: dict) -> list[dict]:
    action = item.get("action") or {}
    values = action.get("sources") or [{"url": url} for url in item.get("urls", [])]
    result = []
    for value in values:
        url = value.get("url") if isinstance(value, dict) else value
        if not isinstance(url, str) or len(url) > 1024 or any(c.isspace() or c in "<>\\" or ord(c) < 32 for c in url):
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                continue
        except ValueError:
            continue
        result.append({"url": url, "title": str(value.get("title") or "")[:200] if isinstance(value, dict) else ""})
    return result[:20]


class ResponseStream:
    """Translate SSE events once, including final-frame-only providers."""

    def __init__(self, model: str):
        self.model = model
        self.calls: dict[int, dict] = {}
        self.items: dict[int, dict] = {}
        self.text_values: dict[int, str] = {}
        self.reasoning_values: dict[int, str] = {}
        self.builtin_states: dict[str, dict] = {}
        self.finished = False

    def item_chunks(self, item: dict, index: int, *, done: bool) -> list[StreamChunk]:
        kind = item.get("type")
        chunks = []
        if kind == "function_call":
            call = self.calls.setdefault(index, {"arguments": "", "announced": False})
            if not call["announced"]:
                call["announced"] = True
                chunks.append(StreamChunk(tool_calls=[ToolCallDelta(index=index, id=item.get("call_id") or item.get("id"), name=item.get("name"))]))
            arguments = item.get("arguments") or ""
            if done and arguments:
                if not arguments.startswith(call["arguments"]):
                    raise ValueError("Inconsistent Responses function arguments")
                tail = arguments[len(call["arguments"]):]
                if tail:
                    chunks.append(StreamChunk(tool_calls=[ToolCallDelta(index=index, arguments_delta=tail)]))
                call["arguments"] = arguments
        elif kind in {"web_search_call", "web_extractor_call"}:
            tool_id = item.get("id") or f"{kind}_{index}"
            status = item.get("status") or ("completed" if done else "in_progress")
            sources = public_sources(item)
            previous = self.builtin_states.get(tool_id) or {}
            event = {"id": tool_id, "name": kind.removesuffix("_call"), "status": status,
                     "sources": sources or previous.get("sources", []),
                     "queries": (item.get("action") or {}).get("queries") or previous.get("queries", [])}
            if event != previous:
                self.builtin_states[tool_id] = event
                chunks.append(StreamChunk(builtin_tool_event=event))
        elif done and kind in {"message", "reasoning"}:
            reasoning = kind == "reasoning"
            parts = item.get("summary", []) if reasoning else item.get("content", [])
            text = "".join(p.get("text", "") for p in parts if reasoning or p.get("type") == "output_text")
            values = self.reasoning_values if reasoning else self.text_values
            previous = values.get(index, "")
            if text and not text.startswith(previous):
                raise ValueError("Inconsistent Responses text")
            tail = text[len(previous):]
            if tail:
                chunks.append(StreamChunk(**{"thinking_content" if reasoning else "content": tail}))
            values[index] = text or previous
        if done:
            self.items[index] = item
        return chunks

    def consume(self, event: dict) -> list[StreamChunk]:
        kind = event.get("type", "")
        index = event.get("output_index", 0)
        if kind in {"error", "response.failed"}:
            raise ValueError("DashScope Responses request failed")
        if kind in {"response.output_item.added", "response.output_item.done"}:
            return self.item_chunks(event["item"], index, done=kind.endswith("done")) or [StreamChunk()]
        if kind == "response.output_text.delta":
            delta = event.get("delta") or ""
            self.text_values[index] = self.text_values.get(index, "") + delta
            return [StreamChunk(content=delta)]
        if kind in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
            delta = event.get("delta") or ""
            self.reasoning_values[index] = self.reasoning_values.get(index, "") + delta
            return [StreamChunk(thinking_content=delta)]
        if kind == "response.function_call_arguments.delta":
            call = self.calls.setdefault(index, {"arguments": "", "announced": False})
            delta = event.get("delta") or ""
            call["arguments"] += delta
            return [StreamChunk(tool_calls=[ToolCallDelta(index=index, arguments_delta=delta)])]
        if kind.startswith("response.web_search_call."):
            return self.item_chunks({"type": "web_search_call", "id": event.get("item_id"),
                                     "status": kind.rsplit(".", 1)[-1]}, index, done=False)
        if kind in {"response.completed", "response.incomplete"}:
            response = event["response"]
            if response.get("error") or response.get("status") in {"failed", "cancelled"}:
                raise ValueError("DashScope Responses request failed")
            chunks = []
            for idx, item in enumerate(response.get("output") or []):
                chunks.extend(self.item_chunks(item, idx, done=True))
            incomplete = kind == "response.incomplete" or response.get("status") == "incomplete"
            if incomplete and self.calls:
                raise ValueError("Incomplete Responses function call; execution refused")
            replay = [item for _, item in sorted(self.items.items())
                      if item.get("type") in {"reasoning", "web_search_call", "web_extractor_call"}]
            chunks.append(StreamChunk(finish_reason="length" if incomplete else "tool_calls" if self.calls else "stop",
                provider_output={"model": self.model, "items": replay},
                **usage_fields(response.get("usage") or {}, responses=True)))
            self.finished = True
            return chunks
        # Creation/progress events are activity, not assistant text or local tool calls.
        return [StreamChunk()]
