"""F08-02: real Runtime/Loop/ScheduledTaskAgent, mocked business Handler."""
import json
import asyncio
from unittest.mock import AsyncMock

import pytest

from services.agent.agent_result import AgentResult
from services.handlers.emit_payloads import build_content_blocks_from_payloads
from tests.test_tool_production_integration import setup, loop_for, tc
from tests.tool_runtime_support import MockHandlerExecutor


async def test_cached_tool_artifacts_are_collected_once(setup, monkeypatch):
    executor = MockHandlerExecutor(agent_domain="general", execution_mode="scheduled",
        task_id="scheduled-fixture", allowed_tool_names={"code_execute"},
        tool_policy_snapshot={"version": 1, "allowed_tools": ["code_execute"]})
    raw = AgentResult("report", emit_payloads=[
        {"kind": "file", "name": "report.csv", "url": "https://example.test/report.csv", "workspace_path": "report.csv"},
        {"kind": "image", "url": "https://example.test/chart.png", "workspace_path": "chart.png"},
    ], tokens_used=19)
    executor.handler.return_value = raw
    loop, context = loop_for(executor)
    from services.agent.loop_hooks import ToolAuditHook
    audit = AsyncMock()
    monkeypatch.setattr("services.agent.tool_audit.record_tool_audit", audit)
    loop.hooks = [ToolAuditHook()]
    trace = []
    for call_id in ("original", "cached"):
        await loop._execute_tools([tc("code_execute", {"code": "print('report')", "description": "生成报告"}, call_id)], [], "", context)
        result = loop._turn_tool_outcomes[0][1]
        trace.append({"call_id": call_id, "cached": result.execution.cached,
                      "handler_started": result.execution.handler_started,
                      "chargeable_tokens": result.chargeable_tokens,
                      "collected_payloads": len(loop._emit_payloads)})
    blocks = build_content_blocks_from_payloads(loop._emit_payloads)
    assert executor.handler.await_count == 1
    assert trace[1]["cached"] is True
    assert sum(step["chargeable_tokens"] for step in trace) == 19
    assert len(blocks) == 2, "Cache reuse must not deliver each original file/image twice"
    assert [step["collected_payloads"] for step in trace] == [2, 2]
    assert [m["tool_call_id"] for m in context.messages if m["role"] == "tool"] == ["original", "cached"]
    await asyncio.sleep(0)
    assert audit.await_count == 2
    assert [c.args[1].is_cached for c in audit.await_args_list] == [False, True]


async def test_scheduled_agent_final_artifacts_are_collected_once(setup, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from services.agent.scheduled_task_agent import ScheduledTaskAgent
    from services.agent.tool_executor import ToolExecutor
    from tests.test_scheduled_task_agent_integration import FakeAdapter
    from tests.tool_runtime_support import IdentityDB
    adapter = FakeAdapter([
        {"tool_calls": [{"id": "original", "name": "code_execute", "args": "{\"code\":\"print(1)\",\"description\":\"生成报告\"}"}]},
        {"tool_calls": [{"id": "cached", "name": "code_execute", "args": "{\"code\":\"print(1)\",\"description\":\"生成报告\"}"}]},
        {"text": "已生成报告"},
    ])
    monkeypatch.setattr("services.model_gateway.get_model_gateway", lambda: SimpleNamespace(open_chat=lambda _: adapter))
    handler = AsyncMock(return_value=AgentResult("report", emit_payloads=[
        {"kind": "file", "name": "report.csv", "url": "https://example.test/report.csv", "workspace_path": "report.csv"},
        {"kind": "image", "url": "https://example.test/chart.png", "workspace_path": "chart.png"},
    ]))
    monkeypatch.setattr(ToolExecutor, "_code_execute", handler)
    monkeypatch.setattr("services.agent.tool_audit.record_tool_audit", AsyncMock())
    task = {"id": "scheduled-fixture", "user_id": "u1", "org_id": "o1", "prompt": "生成报告", "timeout_sec": 60,
            "execution_policy": {"version": 1, "allowed_tools": ["code_execute"], "required_tools": ["code_execute"]}}
    result = await ScheduledTaskAgent(IdentityDB(), task, execution_mode="scheduled").execute()
    assert handler.await_count == 1
    assert result.status == "success"
    assert len(result.content_blocks) == 2, "Final scheduled result must contain each original artifact once"


def artifact_result(url="https://example.test/report.csv"):
    return AgentResult("report", emit_payloads=[{"kind": "file", "name": "report.csv", "url": url}], tokens_used=19)


@pytest.mark.parametrize("same_url", [True, False])
async def test_separate_executions_keep_same_named_artifacts(setup, same_url):
    executor = MockHandlerExecutor(agent_domain="general")
    executor.handler.side_effect = [artifact_result(), artifact_result() if same_url else artifact_result("https://example.test/new/report.csv")]
    loop, context = loop_for(executor)
    for call_id in ("one", "two"):
        await loop._execute_tools([tc("web_search", {"query": call_id}, call_id)], [], "", context)
    assert executor.handler.await_count == 2
    assert len(loop._emit_payloads) == 2
    assert [p["name"] for p in loop._emit_payloads] == ["report.csv", "report.csv"]


async def test_first_consumption_from_existing_cache_keeps_artifacts(setup):
    executor = MockHandlerExecutor(agent_domain="general")
    executor.handler.return_value = artifact_result()
    previous, old_context = loop_for(executor)
    await previous._execute_tools([tc("web_search", {"query": "x"}, "one")], [], "", old_context)
    current, context = loop_for(executor)
    current._cache = previous._cache
    for call_id in ("two", "three"):
        await current._execute_tools([tc("web_search", {"query": "x"}, call_id)], [], "", context)
        assert current._turn_tool_outcomes[0][1].execution.cached
    assert executor.handler.await_count == 1
    assert len(previous._emit_payloads) == len(current._emit_payloads) == 1


async def test_legacy_cache_source_survives_hits_but_not_replacement(setup):
    from services.agent.tool_result_cache import ToolResultCache
    executor = MockHandlerExecutor(agent_domain="general")
    loop, context = loop_for(executor)
    # Seed the old AgentResult cache API under the real runtime scope key.
    ctx = executor.tool_runtime.context()
    args = {"query": "x", "_tool_cache_scope": [ctx.actor_user_id, ctx.workspace_owner_id,
            ctx.org_id, ctx.conversation_id, ctx.task_id, ctx.context_scope, ctx.execution_mode, ctx.agent_domain]}
    raw = artifact_result()
    loop._cache.put("web_search", args, raw)
    assert loop._cache.get("web_search", args) is raw
    sources = []
    for call_id in ("one", "two"):
        await loop._execute_tools([tc("web_search", {"query": "x"}, call_id)], [], "", context)
        result = loop._turn_tool_outcomes[0][1]
        sources.append(result.artifact_source)
        assert result.execution.cached and result.chargeable_tokens == 0
        assert "origin" not in result.audit  # no fabricated historical facts
    assert sources[0] == sources[1] and sources[0] is not None
    assert len(loop._emit_payloads) == 1
    executor.handler.assert_not_awaited()
    # TTL expiry allows a new real execution, even with the same name and URL.
    key = ToolResultCache._key("web_search", args)
    entry = loop._cache._store[key]
    loop._cache._store[key] = (entry[0], entry[1] - 400)
    executor.handler.return_value = artifact_result()
    await loop._execute_tools([tc("web_search", {"query": "x"}, "three")], [], "", context)
    assert executor.handler.await_count == 1
    assert len(loop._emit_payloads) == 2
    assert loop._turn_tool_outcomes[0][1].artifact_source != sources[0]


@pytest.mark.parametrize("case", ["file", "sandbox", "erp", "media_failure", "media_image"])
def test_reencoded_replay_keeps_origin_and_every_payload_channel(setup, case):
    from services.tools import ToolCall
    from services.tools.result_payload import encode_result, restore_result
    from tests.test_tool_result_consumption import sample
    from tests.test_tool_result_persistence_06 import envelope
    raw = sample(case, setup[1])[1]
    original, (call, context, decision) = envelope(raw)
    loop, _ = loop_for(MockHandlerExecutor(agent_domain="general"))
    loop._register_result_files(original, call.name)
    expected = original.collect_payloads("tool_loop")
    assert expected
    previous = original
    for call_id in ("cache", "reencoded-replay"):
        next_call = ToolCall(call_id, call.name, call.arguments)
        recovered = restore_result(encode_result(previous), call=next_call, context=context, decision=decision)
        recovered = recovered.reused(call=next_call, context=context, decision=decision, replayed=True)
        assert recovered.artifact_source == original.artifact_source
        assert recovered.audit["tool_call_id"] == call_id
        assert recovered.chargeable_tokens == 0
        loop._register_result_files(recovered, call.name)
        assert loop._emit_payloads == expected
        assert recovered.model_content("tool_loop") == original.model_content("tool_loop")
        assert recovered.error == original.error
        previous = recovered
    fresh, _ = loop_for(MockHandlerExecutor(agent_domain="general"))
    fresh._register_result_files(recovered, call.name)
    assert fresh._emit_payloads == expected  # recovery's first delivery is retained


async def test_each_run_resets_collection_together_with_output(setup):
    from tests.test_scheduled_task_agent_integration import FakeAdapter
    executor = MockHandlerExecutor(agent_domain="general")
    executor.handler.return_value = artifact_result()
    loop, context = loop_for(executor)
    for call_id in ("first-run", "second-run"):
        adapter = FakeAdapter([
            {"tool_calls": [{"id": call_id, "name": "web_search", "args": '{"query":"x"}'}]},
            {"text": "完成"},
        ])
        loop.model_gateway = loop.adapter = adapter
        output = await loop.run([], [], [], context)
        assert len(output.emit_payloads) == 1
    assert executor.handler.await_count == 1


def test_failed_collection_does_not_mark_source_as_delivered(setup, monkeypatch):
    from services.tools import ToolResult
    from tests.test_tool_result_persistence_06 import envelope
    original, (call, _, _) = envelope(artifact_result())
    loop, _ = loop_for(MockHandlerExecutor(agent_domain="general"))
    collect = ToolResult.collect_payloads
    def broken(*_): raise RuntimeError("presentation failed")
    monkeypatch.setattr(ToolResult, "collect_payloads", broken)
    with pytest.raises(RuntimeError, match="presentation failed"):
        loop._register_result_files(original, call.name)
    monkeypatch.setattr(ToolResult, "collect_payloads", collect)
    loop._register_result_files(original, call.name)
    assert len(loop._emit_payloads) == 1


def test_old_payload_keeps_legacy_reader_contract(setup):
    from services.tools.result_payload import restore_result
    from tests.test_tool_result_persistence_06 import envelope, old_function
    original, (call, context, decision) = envelope(artifact_result())
    payload = old_function("serialize_tool_result")(original.raw)
    result = restore_result(payload, call=call, context=context, decision=decision)
    result = result.reused(call=call, context=context, decision=decision, replayed=True)
    loop, _ = loop_for(MockHandlerExecutor(agent_domain="general"))
    loop._register_result_files(result, call.name)
    loop._register_result_files(result, call.name)
    assert len(loop._emit_payloads) == 1
    assert result.chargeable_tokens == 0 and "origin" not in result.audit
