"""任务调度层继续生效，生产入口共享同一 Gateway 准入边界。"""

import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.adapters.types import StreamChunk
from services.agent.scheduled_task_agent import ScheduledTaskAgent
from services.conversation_execution import ConversationExecutionService, GenerationOutcome
from services.conversation_worker import ConversationWorker
from services.handlers.chat.stream_runner import run_legacy_chat_stream
from services import model_gateway as gateway_module
from tests.test_chat_gateway_retry_integration import environment, make_adapter, web_request, websocket
from tests.test_conversation_worker import _DB, _row
from tests.test_model_gateway_concurrency import Streams, consume, until, clean_breakers
from tests.test_scheduled_task_agent_integration import make_task, FakeToolExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize("lose_queued_lease", [False, True])
async def test_multiple_actor_claims_keep_worker_limit_and_renew_while_model_queued(lose_queued_lease):
    streams = Streams(1)

    class Executor:
        async def execute(self, task, claim, cancellation_event):
            session = streams.session(claim.task_id, cancel_token=cancellation_event)
            await consume(session)
            return GenerationOutcome(result_content=[], usage={}, credits_cost=0)

    rows = [_row(f"t{i}", f"c{i}") for i in range(3)]
    db = _DB(rows)
    execution = ConversationExecutionService(db, Executor(), renew_interval_seconds=0.01)
    calls = []

    async def rpc(name, params):
        calls.append((name, params))
        if name == "claim_next_serial_generation_turn":
            index = params["p_conversation_id"][1:]
            return {
                "outcome": "claimed", "task_id": f"t{index}", "execution_token": f"token{index}",
                "turn_id": f"turn{index}", "input_message_id": f"input{index}",
                "base_context_revision": 0, "execution_attempt": 1,
            }
        if name == "renew_generation_lease":
            if lose_queued_lease and params["p_task_id"] == "t1":
                return {"outcome": "ownership_lost"}
            return {"outcome": "renewed"}
        assert name == "commit_generation_turn"
        return {"outcome": "committed"}

    execution._rpc = rpc
    execution._load_task = AsyncMock(side_effect=lambda task: {
        "id": task, "conversation_id": f"c{task[1:]}", "assistant_message_id": f"output-{task}",
    })
    worker = ConversationWorker(db, execution, concurrency=2)
    assert await worker.scan_once() == 2
    await until(lambda: streams.started == ["t0"])
    await until(lambda: any(name == "renew_generation_lease" and p["p_task_id"] == "t1" for name, p in calls))
    assert len([1 for name, _ in calls if name == "claim_next_serial_generation_turn"]) == 2
    assert "t2" not in streams.controls  # 第三个任务没有绕过 Worker 被领取
    if lose_queued_lease:
        await until(lambda: any(e.task_id == "t1" and e.event.value == "cancelled" for e in streams.events))
    streams.controls["t0"].set()
    if not lose_queued_lease:
        await until(lambda: "t1" in streams.started)
        streams.controls["t1"].set()
    await worker.wait_idle()
    commits = [p["p_task_id"] for name, p in calls if name == "commit_generation_turn"]
    assert commits == (["t0"] if lose_queued_lease else ["t0", "t1"])
    assert streams.peak == 1 and streams.active == 0
    await worker.stop()


@pytest.mark.asyncio
async def test_web_and_scheduled_agent_share_capacity_without_changing_completion(environment, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    active, peak = 0, 0

    def controlled_adapter(block=False):
        adapter = make_adapter()
        async def stream(**_kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            started.set()
            try:
                if block:
                    await release.wait()
                yield StreamChunk(content="完成", prompt_tokens=2, completion_tokens=1)
            finally:
                active -= 1
        adapter.stream_chat = stream
        return adapter

    environment.configure(controlled_adapter(True), controlled_adapter(), max_concurrency=1)
    monkeypatch.setattr("config.phase_tools.build_domain_tools", lambda *_a, **_k: [])
    monkeypatch.setattr("services.agent.tool_executor.ToolExecutor", lambda *_a, **_k: FakeToolExecutor())
    web = asyncio.create_task(run_legacy_chat_stream(
        handler=environment.handler, request=web_request(), websocket=websocket(),
    ))
    await started.wait()
    scheduled = asyncio.create_task(ScheduledTaskAgent(MagicMock(), make_task()).execute())
    await until(lambda: len(environment.sessions) == 2 and environment.sessions[1]._acquire_task is not None)
    assert active == 1 and not scheduled.done()
    release.set()
    await web
    result = await asyncio.wait_for(scheduled, 2)
    assert result.status == "success"
    assert peak == 1 and active == 0
    environment.handler.on_complete.assert_awaited_once()
    environment.handler.on_error.assert_not_awaited()
    task_ids = {e.task_id for e in environment.events if e.is_terminal}
    assert task_ids == {"task", "task_int_001"}


@pytest.mark.asyncio
async def test_retry_routes_without_holding_slot_and_shutdown_interrupts_routing(environment):
    first = make_adapter(ConnectionError("provider failed"))
    environment.configure(first, make_adapter(StreamChunk(content="routing result")), max_concurrency=1)
    routed = asyncio.Event()
    async def route(_context):
        # 路由器/辅助请求需要同一 Gateway；前一失败 attempt 必须已释放槽位。
        from services.model_gateway import ModelCallRequest
        session = gateway_module.get_model_gateway().open_chat(ModelCallRequest(model_id="qwen3.5-plus"))
        await consume(session)
        routed.set()
        await asyncio.Event().wait()
    environment.handler._route_retry = AsyncMock(side_effect=route)
    web = asyncio.create_task(run_legacy_chat_stream(
        handler=environment.handler, request=web_request(), websocket=websocket(),
    ))
    await asyncio.wait_for(routed.wait(), 1)
    await gateway_module.get_model_gateway().close()
    await asyncio.wait_for(asyncio.gather(web, return_exceptions=True), 1)
    assert environment.sessions[0].last_result.status == "cancelled"
    environment.handler.on_complete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("script", ["direct", "erp", "loop"])
async def test_migrated_benchmarks_use_real_gateway_and_close_session(script, monkeypatch):
    streams = Streams()
    monkeypatch.setattr("services.model_gateway.get_model_gateway", lambda: streams.gateway)
    # 不调用付费 Provider；实际执行原脚本的模型读取循环。
    streams.controls["qwen3.5-plus"] = asyncio.Event()
    streams.controls["qwen3.5-plus"].set()
    if script == "direct":
        from scripts.benchmark_direct_vs_agent import run_llm_loop
        result = await run_llm_loop("query", [], "system", "qwen3.5-plus")
    elif script == "erp":
        from scripts.test_erp_agent_benchmark import run_erp_agent_test
        monkeypatch.setattr("config.phase_tools.build_domain_tools", lambda *_a: [])
        monkeypatch.setattr("services.tool_selector.select_and_filter_tools", AsyncMock(return_value=[]))
        result = await run_erp_agent_test("query", "qwen3.5-plus")
    else:
        from scripts.test_tool_loop_benchmark import call_llm_with_tools
        result = await call_llm_with_tools("query", [], "qwen3.5-plus")
    assert result["turns"] == 1
    assert streams.started == ["qwen3.5-plus"]
    streams.adapters["qwen3.5-plus"].close.assert_awaited_once()
    assert [e.event.value for e in streams.events] == ["started", "first_chunk", "completed"]


def test_production_chat_calls_have_no_direct_adapter_bypass():
    backend = Path(__file__).resolve().parents[1]
    violations = []
    for folder in ("services", "api", "scripts"):
        for path in (backend / folder).rglob("*.py"):
            relative = path.relative_to(backend).as_posix()
            if relative.startswith("services/adapters/") or relative == "services/model_gateway.py":
                continue  # Provider 实现和 Gateway 唯一 dispatch 保留
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                call = node.func
                if isinstance(call, ast.Attribute) and call.attr == "stream_chat":
                    receiver = ast.unparse(call.value)
                    if receiver not in {"session", "model_gateway", "self.model_gateway"}:
                        violations.append(f"{relative}:{node.lineno}: {receiver}.stream_chat")
                name = call.id if isinstance(call, ast.Name) else call.attr if isinstance(call, ast.Attribute) else ""
                # 离线 poc_*.py 仍有 chat_sync 协议实验，未被服务入口导入。
                # 工厂兼容入口保留；服务和 API 不允许绕过 Gateway 创建 Chat adapter。
                if name == "create_chat_adapter" and folder != "scripts":
                    violations.append(f"{relative}:{node.lineno}: adapter factory bypass")
    assert violations == []
