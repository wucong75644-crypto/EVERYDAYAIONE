from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from services.scheduler.task_nl_parser import parse_task_request, parse_task_nl
from services.scheduler.chat_task_manager import ChatTaskManager, handle_form_submit
from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeSetService, ScheduledTaskChangeAdapter
from services.scheduler.task_submission import instruction_scope_preserved
from services.changeset.contracts import ChangeSetContext, NormalizeRequest, PreflightRequest
from services.planner import CapabilityRegistry, PlanCandidate, PlanStep, PlannerFramework
from config.chat_tools import get_core_tools
from tests.test_scheduled_task_changeset_adapter import _Db, _Repo


@pytest.mark.asyncio
async def test_strict_parser_never_uses_legacy_default_time_and_keeps_shop_instruction():
    text = "每天九点把A店的订单日报发给我"
    raw = {"changes": {"name": "订单日报", "prompt": "所有店铺订单", "schedule_type": "daily", "time_str": "09:00"},
           "evidence": {"prompt": "A店的订单日报", "schedule_type": "每天", "time_str": "九点"}, "recipient": "我",
           "request_parts": [{"kind": "schedule", "text": "每天九点"},
                             {"kind": "execution", "text": "把A店的订单日报"},
                             {"kind": "delivery", "text": "发给我"}]}
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request(text)
    assert result["changes"]["prompt"] == "把A店的订单日报" and not result["missing_fields"]
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=None)):
        strict = await parse_task_request("做订单日报")
        old = await parse_task_nl("做订单日报")
    assert "time_str" in strict["missing_fields"] and "time_str" not in strict["changes"]
    assert old["time_str"] == "09:00"  # old prefill API remains compatible


@pytest.mark.asyncio
async def test_one_shot_explicit_date_does_not_ask_for_a_second_time_field():
    request = "2030年10月1日9点发A店日报给我"
    parsed = {"changes": {"prompt": "A店日报", "schedule_type": "once", "run_at": "2030-10-01T09:00:00+08:00"},
              "evidence": {"prompt": "A店日报", "schedule_type": "2030年10月1日", "run_at": "2030年10月1日9点"},
              "recipient": "我", "request_parts": [{"kind": "schedule", "text": "2030年10月1日9点"},
                                                   {"kind": "execution", "text": "发A店日报"},
                                                   {"kind": "delivery", "text": "给我"}]}
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=parsed)):
        result = await parse_task_request(request)
    assert result["missing_fields"] == [] and "time_str" not in result["changes"]


@pytest.mark.asyncio
async def test_parser_drops_ungrounded_update_fields_and_detects_recipient_omission():
    raw = {"changes": {"time_str":"10:00", "name":"新建任务", "prompt":"全部订单", "schedule_type":"daily"},
           "evidence": {"time_str":"十点", "prompt":"不存在的原文"}}
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)):
        result = await parse_task_request("改到十点并发送到销售群", operation="update")
    assert result["changes"] == {"time_str":"10:00"}
    assert "销售群" in result["recipient"]


@pytest.mark.asyncio
async def test_complete_and_incomplete_chat_requests_wait_for_form_confirmation():
    manager = ChatTaskManager(_Db(),"u","org",submission_mode="apply_if_allowed",idempotency_key="call")
    parsed = {"changes":{"name":"A店订单","prompt":"每天九点A店订单日报","schedule_type":"daily","time_str":"09:00"},
              "missing_fields":[],"recipient":""}
    submit = AsyncMock(return_value={"type":"change_set","data":{"id":"cs"}})
    targets = [{"label":"推送给我（网页）","value":'{"type":"web","user_id":"u"}'}]
    with patch("services.scheduler.task_nl_parser.parse_task_request", AsyncMock(return_value=parsed)), \
         patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=targets)), \
         patch.object(manager,"_begin_request",submit):
        complete_form = await manager.handle("create",{"description":"每天九点A店订单日报"})
        assert complete_form["type"] == "form"
        assert complete_form["submit_text"] == "确认创建"
        parsed["changes"].pop("time_str")
        parsed["missing_fields"] = ["time_str"]
        form = await manager.handle("create",{"description":"每天做A店订单日报"})
    fields = {f["name"]:f for f in form["fields"]}
    submit.assert_not_awaited()
    assert fields["time_str"]["default_value"] == "" and fields["prompt"]["type"] == "textarea"
    assert fields["_submission_mode"]["default_value"] == "apply_if_allowed"


@pytest.mark.asyncio
async def test_edit_time_keeps_definition_and_revalidates_saved_plan_without_model():
    framework = PlannerFramework(CapabilityRegistry.from_tool_schemas(get_core_tools(org_id="org")))
    release = framework.release(PlanCandidate(target={},input_contract={},output_contract={"result":"report"},
        steps=(PlanStep("read","读取A店订单",("erp_agent",)),),candidate_tools=("erp_agent",)))
    base = {"name":"日报","prompt":"读取A店订单","schedule_type":"daily","cron_expr":"0 9 * * *",
            "timezone":"Asia/Shanghai","push_target":{"type":"web","user_id":"u"},
            "data_scope":{"kind":"task_prompt"},"execution_policy":release.tool_policy,"plan_snapshot":release.as_dict()}
    ctx = ChangeSetContext(id="cs",org_id="org",resource_type="scheduled_task",resource_id="task",operation="update",
                          base_revision="0",base_snapshot=base,proposed_snapshot={"time_str":"10:00"},patch=(),diff={},policy_snapshot={})
    service = ScheduledTaskChangeSetService(_Db(),user_id="u",org_id="org")
    normalized = (await service.adapter.normalize(NormalizeRequest(context=ctx,proposed_snapshot=ctx.proposed_snapshot))).proposed_snapshot
    assert normalized["cron_expr"] == "0 10 * * *" and normalized["prompt"] == base["prompt"]
    with patch("services.scheduler.scheduled_task_workflow.create_plan", AsyncMock(side_effect=AssertionError("must not replan"))):
        revised = await service._build_release("update",normalized,{},base=base)
    assert revised.tool_policy["allowed_tools"] == list(base["execution_policy"]["allowed_tools"])
    assert revised.candidate["steps"] == release.candidate["steps"]


@pytest.mark.asyncio
async def test_ordinary_create_checks_scope_without_running_agent_and_applies(monkeypatch):
    repo, db = _Repo(), _Db(rpc_data={"outcome":"created","new_revision":0})
    service = ScheduledTaskChangeSetService(db,user_id="u",org_id="org")
    framework = PlannerFramework(CapabilityRegistry.from_tool_schemas(get_core_tools(org_id="org")))
    release = framework.release(PlanCandidate(target={},input_contract={},output_contract={"result":"report"},
        steps=(PlanStep("read","read orders",("erp_agent",)),),candidate_tools=("erp_agent",)))
    definition = {"name":"日报","prompt":"读取A店订单","schedule_type":"daily","time_str":"09:00", "push_target":{"type":"web","user_id":"u"}}
    with patch("services.scheduler.scheduled_task_change_adapter.ChangeSetRepository",return_value=repo), \
         patch("services.permissions.checker.check_permission",AsyncMock(return_value=True)), \
         patch.object(service,"_build_release",AsyncMock(return_value=release)), \
         patch.object(service.adapter,"_check_execution_scope",AsyncMock(return_value=())) as scope, \
         patch("services.agent.scheduled_task_agent.ScheduledTaskAgent.execute",AsyncMock(side_effect=AssertionError("no trial"))):
        # Drive the same persisted background completion deterministically.
        with patch("asyncio.create_task", side_effect=lambda coroutine: coroutine.close()):
            await service.begin(operation="create",proposed_snapshot=definition,submission_mode="apply_if_allowed")
        await service.complete("cs1")
    assert repo.row["status"] == "applied"
    assert repo.row["proposed_snapshot"]["execution_policy"]["allowed_tools"] == ["erp_agent"]
    scope.assert_awaited_once()
    checks = {c["check_type"]:c for c in repo.checks}
    assert checks["preflight"]["result"]["mode"] == "validated_capability_scope"
    assert checks["preflight"]["result"]["full_run"] is False


@pytest.mark.asyncio
async def test_new_form_date_is_explicit_and_old_form_stays_proposal():
    data = {"name":"单次","prompt":"只读A店","schedule_type":"once","run_at":"2030-10-01T10:00", "push_target":'{"type":"web","user_id":"u"}',"_submission_mode":"apply_if_allowed"}
    with patch("services.permissions.checker.check_permission",AsyncMock(return_value=True)), \
         patch("services.scheduler.chat_task_manager._propose_form_change",AsyncMock(return_value={"success":True})) as submit:
        await handle_form_submit(_Db(),"u","org","scheduled_task_create",data)
    assert submit.call_args.kwargs["definition"]["run_at"] == "2030-10-01T10:00:00+08:00"
    assert submit.call_args.kwargs["submission_mode"] == "apply_if_allowed"


def test_presentation_additions_keep_data_scope_but_arbitrary_rewrites_do_not():
    base = {"prompt":"只读取A店昨天订单"}
    assert instruction_scope_preserved(base,{"prompt":base["prompt"]+"\n输出格式：表格"})
    assert not instruction_scope_preserved(base,{"prompt":"读取全部店铺订单"})
    assert not instruction_scope_preserved(base,{"prompt":base["prompt"]+"并读取B店"})


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["task-owner", None])
async def test_manager_edit_checks_runtime_as_task_owner_and_fails_closed_if_missing(owner):
    adapter = ScheduledTaskChangeAdapter(_Db(rows=[{"user_id": owner}]), user_id="manager", org_id="org")
    context = SimpleNamespace(operation="update", resource_id="task")
    with patch("services.agent.tool_executor.ToolExecutor") as executor, \
         patch("services.tools.runtime_context.refresh_context", AsyncMock(return_value="trusted")) as refresh:
        executor.return_value.tool_runtime.registry.check_access.return_value = SimpleNamespace(allowed=False, reason="owner_scope_denied")
        reasons = await adapter._check_execution_scope(context, {"version": 1, "allowed_tools": ["erp_agent"]})
    if owner:
        assert executor.call_args.kwargs["user_id"] == "task-owner"
        assert executor.call_args.kwargs["org_id"] == "org"
        assert reasons == ("erp_agent:owner_scope_denied",)
        refresh.assert_awaited_once()
    else:
        executor.assert_not_called()
        assert reasons == ("task_owner_unavailable",)
