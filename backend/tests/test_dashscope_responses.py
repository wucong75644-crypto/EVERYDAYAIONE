"""Bailian protocol regressions: server tools, business calls, caching and isolation."""
import copy
import json

import httpx
import pytest

from config.model_aliases import canonical_model_id
from services.adapters.dashscope.chat_adapter import DashScopeAPIError, DashScopeChatAdapter
from services.adapters.dashscope.responses import (
    ResponseStream, chat_messages, public_sources, response_input, response_tools, usage_fields,
)

FUNCTION = {"type": "function", "function": {"name": "lookup", "description": "Read data", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}}}
SEARCH = {"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}


def terminal(output=(), usage=None, kind="response.completed"):
    return {"type": kind, "response": {"status": "incomplete" if kind.endswith("incomplete") else "completed", "output": list(output), "usage": usage or {}}}


def test_server_tools_never_become_local_calls_and_functions_are_not_duplicated():
    parser = ResponseStream("qwen3.8-max")
    search = {"type": "web_search_call", "id": "search_1", "status": "completed", "action": {"queries": ["query"], "sources": [{"url": "https://example.com/doc"}]}}
    function = {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": '{"id":"1"}'}
    events = [
        {"type": "response.output_item.done", "output_index": 0, "item": search},
        {"type": "response.output_item.added", "output_index": 1, "item": {**function, "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"id":'},
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '"1"}'},
        {"type": "response.output_item.done", "output_index": 1, "item": function},
        terminal([search, function]),
    ]
    chunks = [c for e in events for c in parser.consume(e)]
    calls = [call for c in chunks for call in c.tool_calls or []]
    assert [c.name for c in calls if c.name] == ["lookup"]
    assert "".join(c.arguments_delta or "" for c in calls) == function["arguments"]
    assert chunks[-1].finish_reason == "tool_calls"
    assert chunks[-1].provider_output["items"] == [search]
    assert any(c.builtin_tool_event and c.builtin_tool_event["sources"] for c in chunks)


def test_replay_uses_actor_call_ids_and_cannot_cross_models():
    reasoning = {"type": "reasoning", "id": "r1", "summary": [{"type": "summary_text", "text": "plan"}]}
    messages = [{"role": "assistant", "content": "Checking", "reasoning_content": "plan", "_dashscope_output": {"model": "qwen3.8-max", "items": [reasoning]}, "tool_calls": [{"id": "actor_1", "function": {"name": "lookup", "arguments": "{}"}}]}, {"role": "tool", "tool_call_id": "actor_1", "content": "result"}]
    original = copy.deepcopy(messages)
    values = response_input(messages, "qwen3.8-max")
    assert values[0] == reasoning
    assert [x["call_id"] for x in values if "call_id" in x] == ["actor_1", "actor_1"]
    assert reasoning not in response_input(messages, "kimi-k3")
    assert "_dashscope_output" not in chat_messages(messages, cache=None)[0]
    assert messages == original


def test_partial_text_final_tail_and_final_frame_only_are_emitted_once():
    parser = ResponseStream("qwen3.8-flash")
    first = parser.consume({"type": "response.output_text.delta", "output_index": 0, "delta": "hel"})
    message = {"type": "message", "content": [{"type": "output_text", "text": "hello"}]}
    final = parser.consume(terminal([message]))
    assert "".join(c.content or "" for c in first + final) == "hello"
    only = ResponseStream("qwen3.8-flash").consume(terminal([message]))
    assert "".join(c.content or "" for c in only) == "hello"


@pytest.mark.parametrize("event", [
    {"type": "response.failed"},
    terminal([{"type": "function_call", "call_id": "x", "name": "lookup", "arguments": "{}"}], kind="response.incomplete"),
])
def test_failed_or_incomplete_calls_cannot_finish_successfully(event):
    with pytest.raises(ValueError):
        ResponseStream("qwen3.8-max").consume(event)


def test_cache_usage_real_bailian_nested_write_shape():
    usage = {"input_tokens": 1727, "output_tokens": 1, "input_tokens_details": {"cached_tokens": 1719}, "x_details": [{"prompt_tokens_details": {"cache_creation_input_tokens": 512}}], "x_tools": {"web_search": {"count": 2}, "web_extractor": {"count": 1}}}
    parsed = usage_fields(usage, responses=True)
    assert parsed["cached_tokens"] == 1719
    assert parsed["cache_creation_input_tokens"] == 512
    assert parsed["builtin_tool_usage"] == {"web_search": 2, "web_extractor": 1}
    assert usage_fields({"prompt_tokens_details": {"cache_creation_input_tokens": 1024}}, responses=False)["cache_creation_input_tokens"] == 1024


def test_cache_markers_history_endpoint_and_no_input_mutation():
    messages = [{"role": "system", "content": "stable rules"}, {"role": "user", "content": "tenant A private prompt"}]
    original = copy.deepcopy(messages)
    cached = chat_messages(messages, cache=True)
    assert all(m["content"][-1]["cache_control"] == {"type": "ephemeral"} for m in cached)
    assert messages == original
    assert not any("cache_control" in p for m in chat_messages(cached, cache=False) for p in m["content"])
    assert chat_messages(messages, cache=None) == messages
    assert "tenant A" not in json.dumps(chat_messages([{"role": "user", "content": "tenant B"}], cache=True))


def test_multimodal_conversion_and_stable_tool_order():
    result = response_input([{"role": "user", "content": [{"type": "text", "text": "what?", "cache_control": {"type": "ephemeral"}}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}], "qwen3.8-max")
    assert result[0]["content"][1] == {"type": "input_image", "image_url": "data:image/png;base64,AA=="}
    assert "cache_control" not in json.dumps(result)
    tools = response_tools([SEARCH, FUNCTION], ("web_search", "web_extractor"))
    assert tools == response_tools([FUNCTION, SEARCH], ("web_search", "web_extractor"))
    assert sum(t.get("type") == "web_search" for t in tools) == 1
    assert tools[0]["name"] == "lookup"


def test_sources_reject_unsafe_urls():
    values = [{"url": u} for u in ["javascript:alert(1)", "https://user:password@example.com", "https://example.com\n", "https://example.com/doc"]]
    assert public_sources({"action": {"sources": values}}) == [{"url": "https://example.com/doc", "title": ""}]


@pytest.mark.asyncio
@pytest.mark.parametrize("model,enabled,thinking,path,expected", [
    ("qwen3.8-max", True, "enabled", "/v1/responses", {"function", "web_search", "web_extractor"}),
    ("qwen3.8-flash", True, "disabled", "/v1/responses", {"function", "web_search"}),
    ("kimi-k3", True, "enabled", "/v1/responses", {"function", "web_search"}),
    ("deepseek-v4-pro", True, "enabled", "/v1/chat/completions", {"function"}),
    ("qwen3.8-max", False, "enabled", "/v1/chat/completions", {"function"}),
])
async def test_protocol_selection_cache_header_and_offline_aux_calls(model, enabled, thinking, path, expected):
    captured = []
    def handle(request):
        captured.append(request)
        event = terminal() if request.url.path.endswith("responses") else {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")
    adapter = DashScopeChatAdapter(api_key="test", model=model, base_url="https://example.com/v1")
    adapter._client = httpx.AsyncClient(base_url="https://example.com/v1", transport=httpx.MockTransport(handle))
    try:
        _ = [c async for c in adapter.stream_chat([{"role": "user", "content": "test"}], tools=[FUNCTION], enable_builtin_tools=enabled, thinking_mode=thinking)]
        request = captured[-1];body = json.loads(request.content)
        assert request.url.path == path
        assert {t["type"] for t in body["tools"]} == expected
        if path.endswith("responses"):
            assert request.headers["x-dashscope-session-cache"] == "enable"
            assert body["store"] is False and "previous_response_id" not in body
        # No agent opt-in: summaries and extraction always use the offline Chat path.
        _ = [c async for c in adapter.stream_chat([{"role": "user", "content": "summarize"}])]
        assert captured[-1].url.path.endswith("chat/completions")
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_truncated_sse_is_an_error_not_a_completed_business_call():
    adapter = DashScopeChatAdapter(api_key="test", model="qwen3.8-max")
    adapter._client = httpx.AsyncClient(base_url="https://example.com", transport=httpx.MockTransport(lambda r: httpx.Response(200, text='data: {"type":"response.created"}\n\n')))
    try:
        with pytest.raises(DashScopeAPIError, match="terminal"):
            _ = [c async for c in adapter.stream_chat([], enable_builtin_tools=True)]
    finally:
        await adapter.close()


def test_upgrade_aliases_leave_deepseek_unchanged():
    from core.config import Settings
    from services.adapters.factory import get_model_config
    settings = Settings(_env_file=None, database_url="test", jwt_secret_key="test", memory_extraction_model="qwen3.5-plus", memory_filter_model="qwen3.5-flash")
    assert settings.memory_extraction_model == "qwen3.8-max"
    assert settings.memory_filter_model == "qwen3.8-flash"
    assert settings.agent_loop_model == "deepseek-v4-pro"
    assert settings.dashscope_session_cache_enabled and settings.dashscope_builtin_tools_enabled
    assert get_model_config("qwen3.5-plus").model_id == "qwen3.8-max"
    assert canonical_model_id("deepseek-v4-pro") == "deepseek-v4-pro"


def test_archiving_drops_provider_replay_and_counts_hidden_reasoning():
    from services.handlers.context_compressor import enforce_budget, estimate_tokens
    messages = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "old answer", "reasoning_content": "x" * 4000, "_dashscope_output": {"model": "qwen3.8-max", "items": [{"type": "reasoning", "summary": [{"text": "x" * 4000}]}]}}, {"role": "user", "content": "current"}]
    assert estimate_tokens(messages) > 3000
    enforce_budget(messages, 50)
    assert "_dashscope_output" not in messages[1] and "reasoning_content" not in messages[1]
    assert messages[-1]["content"] == "current"


@pytest.mark.asyncio
async def test_chat_engine_projects_builtin_progress_sources_and_cache_without_local_calls():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from services.adapters.types import StreamChunk
    from services.handlers.chat.execution_engine import _read_turn
    from services.handlers.chat.stream_session import StreamTotals
    async def stream(**kwargs):
        yield StreamChunk(builtin_tool_event={"id": "s1", "name": "web_search", "status": "searching", "sources": [], "queries": ["public query"]})
        yield StreamChunk(builtin_tool_event={"id": "s1", "name": "web_search", "status": "completed", "sources": [{"url": "https://example.com/doc", "title": "doc"}], "queries": ["public query"]})
        yield StreamChunk(content="Answer", prompt_tokens=2000, completion_tokens=12, cached_tokens=1800, cache_creation_input_tokens=200, builtin_tool_usage={"web_search": 1})
    prepared = SimpleNamespace(model_gateway=SimpleNamespace(stream_chat=stream), messages=[], stream_kwargs={})
    sink = SimpleNamespace(on_text=AsyncMock(), on_thinking=AsyncMock(), on_block=AsyncMock(), on_block_update=AsyncMock())
    totals = StreamTotals(); blocks = []
    text, thinking, calls, previews = await _read_turn(prepared, [], asyncio.Event(), sink, totals, blocks, None)
    assert calls == [] and previews == set() and thinking == ""
    assert "https://example.com/doc" in text and "未与正文逐句对应" in text
    assert len(blocks) == 1 and blocks[0]["status"] == "completed"
    sink.on_block.assert_awaited_once(); sink.on_block_update.assert_awaited_once()
    assert totals.usage == {"prompt_tokens": 2000, "completion_tokens": 12, "cached_tokens": 1800, "cache_creation_input_tokens": 200, "web_search_calls": 1}


@pytest.mark.asyncio
async def test_explicit_no_reasoning_wins_over_thinking_mode_for_extractor():
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, text="data: " + json.dumps(terminal()) + "\n\n")
    adapter = DashScopeChatAdapter(api_key="test", model="qwen3.8-flash")
    adapter._client = httpx.AsyncClient(base_url="https://example.com", transport=httpx.MockTransport(handle))
    try:
        _ = [c async for c in adapter.stream_chat([], enable_builtin_tools=True, thinking_mode="enabled", reasoning_effort="none")]
        assert captured[0]["reasoning"] == {"effort": "none"}
        assert {t["type"] for t in captured[0]["tools"]} == {"web_search"}
    finally:
        await adapter.close()
