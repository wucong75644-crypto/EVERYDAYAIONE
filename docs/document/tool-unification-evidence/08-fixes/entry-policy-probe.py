"""Read-only acceptance probes: real tool boundaries; all business handlers mocked.

Run with the testing environment recorded in entry-policy-probe-command.txt.
Only disposable files under TemporaryDirectory are created. No external IO.
"""
import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from core.config import get_settings
from services.agent.agent_result import AgentResult
from services.tools import build_legacy_catalog
from services.tools.spec import Exposure
from services.websocket_manager import WebSocketManager
from tests.tool_runtime_support import MockHandlerExecutor
from tests.test_tool_production_integration import ChatHarness, invoke, tc, restore_records


def settings_for(mp, root):
    settings = get_settings()
    mp.setattr(settings, "file_workspace_root", str(root))
    for key in ("file_workspace_enabled", "sandbox_enabled", "crawler_enabled"):
        mp.setattr(settings, key, True)
    mp.setattr("services.oss_service.get_oss_service", lambda: SimpleNamespace(
        bucket=SimpleNamespace(get_object_meta=Mock(return_value=SimpleNamespace(etag="fixture")))) )


def sample(prop):
    if prop.get("enum"):
        return prop["enum"][0]
    kind = prop.get("type")
    if kind == "object":
        return {key: sample(prop["properties"][key]) for key in prop.get("required", ())}
    if kind == "array":
        return [sample(prop.get("items", {}))]
    return {"integer": 1, "number": 1.0, "boolean": True}.get(kind, "fixture")


def arguments(spec):
    schema = spec.to_schema()
    props = schema["function"]["parameters"] if schema else {}
    args = {key: sample(props["properties"][key]) for key in props.get("required", ())}
    if spec.policy_rules.action_rule in {"erp_query", "erp_raw_read"}:
        from services.kuaimai.registry import TOOL_REGISTRIES
        tool = "erp_trade_query" if spec.name == "fetch_all_pages" else spec.handler_key
        args["action"] = next(name for name, entry in TOOL_REGISTRIES[tool].items() if not entry.is_write)
        if spec.name == "fetch_all_pages":
            args["tool"] = tool
    elif spec.policy_rules.action_rule == "erp_write":
        from services.kuaimai.registry import TRADE_REGISTRY
        args.update(category="trade", action=next(name for name, entry in TRADE_REGISTRY.items() if entry.is_write), params={})
    elif spec.name == "manage_scheduled_task":
        args = {"action": "list"}
    elif spec.name == "file_search":
        args = {}
    elif spec.name == "file_delete":
        args = {"files": ["fixture.txt"]}
    elif spec.name == "file_analyze":
        args = {"path": "fixture.csv"}
    elif spec.name == "restore_file":
        args = {"filename": "restore-fixture"}
    return args


async def all_handlers(root):
    rows = []
    specs = build_legacy_catalog().specs()
    with pytest.MonkeyPatch.context() as mp:
        settings_for(mp, root)
        manager = WebSocketManager()
        manager.send_to_task_or_user = AsyncMock()
        mp.setattr("services.handlers.chat_tool_mixin.ws_manager", manager)
        for spec in specs:
            for entry in ("legacy", "chat", "loop"):
                for authorized in (True, False):
                    domain = "erp" if spec.domain == "erp" else "general"
                    executor = MockHandlerExecutor(agent_domain=domain, task_id="task1",
                        tool_entrypoint="legacy_internal" if entry == "legacy" else "model",
                        allowed_tool_names=None if authorized else frozenset())
                    executor.handler.return_value = AgentResult(summary="mock completed", status="success")
                    executor.tool_confirmer = AsyncMock(return_value=True)
                    restore_records(executor)
                    dispatcher = executor.tool_runtime.service.dispatcher
                    dispatched = AsyncMock(wraps=dispatcher.dispatch)
                    mp.setattr(dispatcher, "dispatch", dispatched)
                    call_args = arguments(spec)
                    output = await invoke(entry, executor, [tc(spec.name, call_args)], mp)
                    expected = int(authorized and (entry == "legacy" or spec.exposure == Exposure.PUBLIC))
                    actual = executor.handler.await_count
                    row = {"tool": spec.name, "handler_key": spec.handler_key, "domain": domain,
                           "entry": entry, "authorized": authorized, "expected_calls": expected,
                           "handler_calls": actual, "dispatcher_calls": dispatched.await_count,
                           "mock_confirm_calls": executor.tool_confirmer.await_count,
                           "arguments": call_args,
                           "passed": actual == dispatched.await_count == expected}
                    if not row["passed"]:
                        row["output"] = str(output)
                    rows.append(row)
        return {"spec_count": len(specs), "case_count": len(rows),
                "passed": sum(row["passed"] for row in rows), "rows": rows}


async def concurrency(root):
    reports = []
    for entry in ("chat", "loop"):
        with pytest.MonkeyPatch.context() as mp:
            settings_for(mp, root)
            manager = WebSocketManager()
            manager.send_to_task_or_user = AsyncMock()
            mp.setattr("services.handlers.chat_tool_mixin.ws_manager", manager)
            executor = MockHandlerExecutor(agent_domain="general")
            restore_records(executor)
            both, release = asyncio.Event(), asyncio.Event()
            trace, active = [], set()
            async def handler(name, args):
                label = args.get("query") or args.get("keyword") or args.get("filename")
                active.add(label)
                trace.append({"event": "start", "label": label, "active": sorted(active)})
                if label in {"A", "B"}:
                    if {"A", "B"} <= active:
                        both.set()
                    await release.wait()
                elif label in {"C", "E"}:
                    assert active == {label}
                await asyncio.sleep(0)
                active.remove(label)
                trace.append({"event": "end", "label": label, "active": sorted(active)})
                return "ok"
            executor.handler.side_effect = handler
            calls = [tc("file_search", {"keyword": "A"}, "a"), tc("web_search", {"query": "B"}, "b"),
                     tc("restore_file", {"filename": "C"}, "c"), tc("web_search", {"query": "D"}, "d"),
                     tc("restore_file", {"filename": "E"}, "e")]
            task = asyncio.create_task(invoke(entry, executor, calls, mp))
            await asyncio.wait_for(both.wait(), timeout=5)
            release.set()
            await task
            def index(event, label):
                return next(i for i, item in enumerate(trace) if item["event"] == event and item["label"] == label)
            passed = (index("start", "C") > max(index("end", "A"), index("end", "B"))
                      and index("start", "D") > index("end", "C")
                      and index("start", "E") > index("end", "D"))
            reports.append({"entry": entry, "passed": passed, "trace": trace})
    return reports


async def websocket_bypass(root):
    """Use current real WS route, scoped waiter, Runtime/Policy/Dispatcher.

    Only message transport, identity DB, and the file_delete business handler are
    mocked. An isolated fixture must remain unchanged in every scenario.
    """
    from api.routes import ws
    reports = []
    for scenario in ("scoped_nonbool_false", "foreign_actor_without_scope", "normal_false", "normal_true"):
        with pytest.MonkeyPatch.context() as mp:
            settings_for(mp, root)
            manager = WebSocketManager()
            manager.send_to_connection = AsyncMock()
            original_wait = manager.wait_for_confirm
            async def bounded_wait(call_id, **kw):
                return await original_wait(call_id, **{**kw, "timeout": .05})
            manager.wait_for_confirm = bounded_wait
            mp.setattr(ws, "ws_manager", manager)
            mp.setattr("services.handlers.chat_tool_mixin.ws_manager", manager)
            persist = AsyncMock(return_value=(False, "CONFIRM_LEGACY"))
            mp.setattr(ws, "_persist_actor_tool_confirmation", persist)
            harness = ChatHarness()
            executor = MockHandlerExecutor(agent_domain="general", task_id="task1")
            executor.tool_confirmer = lambda call, ctx, decision: harness._confirm_tool_call(call, ctx, decision, "m1")
            responses = []
            async def send(*items):
                message = items[-1]
                if message["type"] != "tool_confirm_request":
                    return
                approval_id = message["payload"]["tool_call_id"]
                payload = {"tool_call_id": approval_id, "task_id": "task1", "conversation_id": "c1"}
                actor = "u1"
                if scenario == "scoped_nonbool_false":
                    payload["approved"] = "false"
                elif scenario == "foreign_actor_without_scope":
                    payload = {"tool_call_id": approval_id, "approved": True}
                    actor = "other-user"
                else:
                    payload["approved"] = scenario == "normal_true"
                responses.append({"actor": actor, "payload": payload})
                await ws._handle_message("fixture-connection", actor,
                    {"type": "tool_confirm_response", "payload": payload}, org_id="o1")
            manager.send_to_task_or_user = AsyncMock(side_effect=send)
            result = await executor.tool_runtime.execute("file_delete", {"files": ["fixture.txt"]}, call_id="danger-call")
            fixture_unchanged = (root / "org/o1/u1/fixture.txt").read_text() == "acceptance fixture"
            expected = int(scenario == "normal_true")
            actual = executor.handler.await_count
            reports.append({"scenario": scenario, "expected_handler_calls": expected, "handler_calls": actual,
                            "policy_outcome": result.decision.outcome, "policy_reason": result.decision.reason,
                            "identity_persistence_adapter_calls": persist.await_count,
                            "fixture_unchanged": fixture_unchanged, "response": responses[0],
                            "passed": actual == expected and fixture_unchanged})
    return reports


async def main():
    with tempfile.TemporaryDirectory(prefix="tool08-entry-probe-") as temp:
        root = Path(temp)
        workspace = root / "org/o1/u1"
        workspace.mkdir(parents=True)
        (workspace / "fixture.txt").write_text("acceptance fixture")
        (workspace / "fixture.csv").write_text("x\n1\n")
        report = {"base_commit": "41732e4f352809f000e34c28789eb71a07000618", "candidate": "08 fix working tree; source fingerprints in fix-evidence",
                  "boundary": "Real Registry/Policy/Runtime/Dispatcher and entrypoints, mocked business handlers and external IO",
                  "all_handlers": await all_handlers(root),
                  "concurrency": await concurrency(root),
                  "websocket_confirmation": await websocket_bypass(root)}
        target = Path(__file__).with_name("entry-policy-probe-results.json")
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"result_file": str(target),
                          "all_handler_cases": report["all_handlers"]["case_count"],
                          "all_handler_passed": report["all_handlers"]["passed"],
                          "concurrency": report["concurrency"],
                          "websocket_confirmation": report["websocket_confirmation"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
