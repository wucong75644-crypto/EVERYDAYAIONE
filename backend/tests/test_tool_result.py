"""Block 03 field-by-field envelope/projection comparisons; no persistence changes."""

import asyncio
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from schemas.multimodal import FileReadResult
from services.agent.agent_result import AgentResult
from services.agent.tool_output import ColumnMeta, FileRef, OutputFormat, ToolOutput
from services.handlers.chat_generate_mixin import extract_display_text, unpack_tool_result
from services.handlers.emit_payloads import build_content_blocks_from_payloads, collect_agent_result_payloads
from services.scheduler.chat_task_manager import FormBlockResult
from services.tools import ToolCall, ToolContext, ToolPolicy, ToolResult, build_legacy_catalog


def wrap(raw, name="search_knowledge"):
    ctx = ToolContext(
        actor_user_id="actor", workspace_owner_id="actor", org_id="org",
        context_scope="user", personal_context_allowed=True, agent_domain="general",
        permission_mode="ask", execution_mode="interactive", conversation_id="conversation",
        task_id="task", call_id="call", feature_flags={"file_workspace_enabled": True},
    )
    call = ToolCall("call", name, {"query": "fixture"})
    decision = ToolPolicy(build_legacy_catalog()).decide(name, ctx, call.arguments)
    return ToolResult.wrap(raw, call=call, context=ctx, decision=decision, elapsed_ms=23)


def representative_agent(fmt=OutputFormat.TABLE, status="success"):
    columns = [ColumnMeta("amount", "numeric", "金额"), ColumnMeta("time", "timestamp", "时间")]
    file_ref = FileRef(
        path="/unavailable-test-workspace/staging/fixture.parquet", filename="fixture.parquet",
        format="parquet", row_count=300, size_bytes=2048, columns=columns, preview="fixture preview",
        created_at=123, id="artifact-id", mime_type="application/x-parquet", created_by="erp_agent",
        ttl_seconds=172800, derived_from=("input-id",),
    ) if fmt == OutputFormat.FILE_REF else None
    return AgentResult(
        summary="fixture summary", status=status, format=fmt, file_ref=file_ref,
        data=[{"amount": Decimal("1.25"), "time": datetime(2026, 9, 9, tzinfo=timezone.utc),
               "id": UUID("00000000-0000-0000-0000-000000000001")}], columns=columns,
        source="erp_agent", error_message="fixture business error" if status in {"error", "timeout"} else "",
        metadata={"retryable": True, "retry_context": {"prompt": "fixture"}, "custom": [1, {"x": 2}],
                  "doc_type": "order", "time_range": ["2026-09-01", "2026-09-09"]},
        emit_payloads=[
            {"kind": "file", "url": "https://example.test/a.csv", "workspace_path": "staging/a.csv", "name": "a.csv"},
            {"kind": "image", "url": "https://example.test/a.png", "workspace_path": "images/a.png",
             "original_url": "https://example.test/original.png", "width": 20, "height": 30},
            {"kind": "image", "failed": True, "error": "fixture failure", "retry_context": {"prompt": "fixture"}},
            {"kind": "chart", "option": {"series": [{"type": "line", "data": [1, 2]}]}},
            {"kind": "diagram", "source": "graph LR; A-->B;"},
        ], tokens_used=321, confidence=0.6, insights=["insight"], follow_up=["follow-up"],
        thinking_text="fixture thinking", _valid_cache={"fixture": True},
    )


@pytest.mark.parametrize("fmt", list(OutputFormat))
@pytest.mark.parametrize("status", ["success", "empty", "partial", "plan", "rejected", "error", "timeout"])
def test_agent_all_fields_and_distinct_model_projections_preserved(fmt, status):
    raw = representative_agent(fmt, status)
    before = {f.name: getattr(raw, f.name) for f in fields(raw)}
    result = wrap(raw)
    assert ToolOutput is AgentResult
    assert result.to_legacy() is raw
    assert result.status == raw.status
    assert result.is_failure is raw.is_failure
    for name, value in before.items():
        assert getattr(result.to_legacy(), name) is value
    assert result.model_content("chat") == raw.to_message_content() == unpack_tool_result(raw)
    assert result.model_content("tool_loop") == raw.to_tool_content()
    assert result.model_content("chat") != result.model_content("tool_loop")
    assert result.display["text"] == extract_display_text(raw)
    assert result.display["thinking_text"] == raw.thinking_text
    assert result.display["format"] is raw.format
    assert result.artifacts.file_ref is raw.file_ref
    assert result.artifacts.data is raw.data
    assert result.artifacts.columns is raw.columns
    assert result.artifacts.emit_payloads is raw.emit_payloads
    assert result.metadata is raw.metadata
    for name in ("tokens_used", "confidence", "insights", "follow_up", "source", "thinking_text"):
        assert result.agent_context[name] == getattr(raw, name)
    audit = result.audit_fields()
    assert audit == {
        "tool_name": "search_knowledge", "tool_call_id": "call", "actor_user_id": "actor",
        "workspace_owner_id": "actor", "org_id": "org", "conversation_id": "conversation", "task_id": "task",
        "args": {"query": "fixture"}, "status": status, "elapsed_ms": 23,
        "result_length": len(raw.summary), "truncated": False, "cached": False,
        "tokens_used": 321, "source": "erp_agent", "metadata": raw.metadata,
    }
    assert audit["metadata"] is raw.metadata
    assert result.execution.status == "succeeded"  # completion != business success
    assert result.execution.handler_started and result.execution.attempts == 1
    assert not result.execution.cached and not result.execution.replayed
    if raw.is_failure:
        assert result.error.message == raw.error_message
        assert result.error.retryable is True
        assert result.error.retry_context is raw.metadata["retry_context"]
        assert result.error.safe_to_retry is True  # only explicit effects=(none,)
    else:
        assert result.error is None
    assert str(result.to_legacy()) == raw.summary
    assert "fixture" in result.to_legacy()
    assert result.to_legacy().to_text() == raw.to_text()


@pytest.mark.parametrize("hint", [True, False, None, "true"])
@pytest.mark.parametrize("name", ["search_knowledge", "file_search", "web_search"])
def test_retry_hint_retained_but_effects_not_inferred_safe(hint, name):
    raw = AgentResult("failed", status="error", error_message="fixture", metadata={"retryable": hint})
    result = wrap(raw, name)
    assert result.error.retryable is (hint if type(hint) is bool else None)
    assert result.error.safe_to_retry is (hint is True and name == "search_knowledge")
    assert result.to_legacy().metadata["retryable"] is hint


@pytest.mark.parametrize("type,url", [("image", "https://example.test/image.png"), ("image", ""), ("text", "ignored")])
def test_file_read_text_image_and_actual_chat_injection_unchanged(type, url):
    from services.handlers.chat.tool_loop import apply_tool_results, append_tool_images

    raw = FileReadResult(type=type, text="image/file metadata", image_url=url)
    result = wrap(raw, "file_search")
    assert result.to_legacy() is raw
    assert result.model_content("chat") == raw.text == unpack_tool_result(raw)
    assert result.model_content("tool_loop") is raw  # existing loop passes non-Agent values through
    assert result.display["text"] == raw.text
    call = {"name": "file_search", "id": "call"}
    messages = []
    images = apply_tool_results(
        tool_results=[(call, result.to_legacy(), False, result.display["text"])],
        messages=messages, content_blocks=[], start_times={}, tool_context=MagicMock(),
    )
    append_tool_images(messages, images)
    assert images == ([url] if type == "image" and url else [])
    assert messages[0]["content"] == raw.text
    if images:
        assert messages[-1]["content"][1:] == result.model_image_blocks
    else:
        assert result.model_image_blocks == []
    assert result.artifacts.image_url == (url if type == "image" else "")
    assert result.audit_fields()["result_length"] == len(raw.text)


async def test_form_exact_payload_hint_and_terminal_behavior():
    from services.handlers.chat_tool_result_mixin import ChatToolResultMixin, ToolResultContext

    raw = FormBlockResult({"type": "form", "form_id": "fixture", "fields": [{"name": "title", "default_value": "fixture"}]}, "LLM only")
    result = wrap(raw)
    assert result.to_legacy() is raw
    assert result.model_content("chat") == raw.llm_hint
    assert result.model_content("tool_loop") is raw
    assert result.artifacts.form is raw.form
    assert result.display == {"text": "表单已展示", "form": raw.form, "terminal_form": True}
    host = MagicMock()
    host._execution_sink = MagicMock(on_tool_result=AsyncMock())
    host._push_tool_step_update = AsyncMock()
    ctx = ToolResultContext("task", "conversation", "message", "actor", "manage_scheduled_task", "call", 1, {}, 23)
    projected = await ChatToolResultMixin._process_tool_result(host, {"id": "call"}, result.to_legacy(), ctx)
    assert host._pending_form_block is raw.form
    assert host._terminal_form_pending is True
    assert projected[1:] == (raw.llm_hint, False, "表单已展示")
    host._execution_sink.on_tool_result.assert_awaited_once()
    host._emit_tool_audit.assert_called_once()
    assert result.audit_fields()["result_length"] == host._emit_tool_audit.call_args.args[7]


@pytest.mark.parametrize("raw", ["plain text", "", "错误: legacy string has no structured failure status", "x" * 40000])
def test_strings_preserve_exact_content_without_staging_or_error_guessing(raw):
    result = wrap(raw)
    assert result.to_legacy() is raw
    assert result.model_content("chat") == result.model_content("tool_loop") == raw
    assert result.display["text"] == raw
    assert result.status == "success" and not result.is_failure
    assert result.audit_fields()["result_length"] == len(raw)
    assert result.audit_fields()["truncated"] is False


def test_multimodal_emit_and_erp_table_compatibility_uses_existing_consumers():
    from services.handlers.chat_tool_mixin import _collect_interactive_agent_payloads

    raw = representative_agent()
    result = wrap(raw)
    old_payloads = collect_agent_result_payloads(raw)
    new_payloads = collect_agent_result_payloads(result.to_legacy())
    assert new_payloads == old_payloads
    assert sum(p["kind"] == "table" for p in new_payloads) == 1
    assert build_content_blocks_from_payloads(new_payloads) == build_content_blocks_from_payloads(old_payloads)
    interactive = _collect_interactive_agent_payloads("erp_agent", result.to_legacy())
    assert all(p["kind"] != "table" for p in interactive)
    assert interactive == _collect_interactive_agent_payloads("erp_agent", raw)
    failed = next(p for p in new_payloads if p.get("failed"))
    assert failed["error"] == "fixture failure" and failed["retry_context"] == {"prompt": "fixture"}
    assert result.artifacts.emit_payloads is raw.emit_payloads
    assert len(raw.emit_payloads) == 5  # wrapping/collection did not synthesize another artifact on raw


@pytest.mark.parametrize("error", [ValueError("fixture"), TimeoutError("fixture"), asyncio.CancelledError("fixture")])
@pytest.mark.parametrize("started", [False, True])
def test_exception_envelopes_preserve_cancel_and_never_offer_retry(error, started):
    wrapped = wrap("unused", "web_search")
    ctx = ToolContext(actor_user_id="actor", workspace_owner_id="actor", org_id=None,
                      context_scope="user", personal_context_allowed=True, agent_domain="general",
                      permission_mode="ask", execution_mode="interactive")
    result = ToolResult.from_exception(error, call=ToolCall("call", "web_search", {}), context=ctx,
                                       decision=wrapped.decision, handler_started=started)
    assert result.exception is error and result.is_failure
    assert result.execution.status == ("uncertain" if started else "not_started")
    assert result.execution.cancelled is isinstance(error, asyncio.CancelledError)
    assert result.error.message == str(error)
    assert result.error.retryable is None and not result.error.safe_to_retry
    assert result.status == ("cancelled" if isinstance(error, asyncio.CancelledError) else
                             "timeout" if isinstance(error, TimeoutError) else "error")
    with pytest.raises(type(error)) as raised:
        result.to_legacy()
    assert raised.value is error


def test_wrapping_runtime_metadata_does_not_serialize_or_validate_files():
    raw = representative_agent(OutputFormat.FILE_REF)
    sentinel = object()
    raw.metadata["runtime_only"] = sentinel
    result = wrap(raw)
    assert result.to_legacy() is raw and result.metadata["runtime_only"] is sentinel
    assert result.to_legacy()._valid_cache == {"fixture": True}
    assert result.artifacts.file_ref.path.startswith("/unavailable-test-workspace/")
    raw.metadata["nested_runtime"] = {"handle": sentinel}
    # Legacy projection may fail on runtime-only nested values; envelope creation must not.
    with pytest.raises(TypeError):
        raw.to_tool_content()
    with pytest.raises(TypeError):
        result.model_content("tool_loop")
    assert result.to_legacy() is raw
    with pytest.raises(ValueError, match="Unknown model consumer"):
        result.model_content("unknown")
