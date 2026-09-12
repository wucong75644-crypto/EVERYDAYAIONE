"""Structured management contract: no second model, explicit patches and receipts."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.scheduler.chat_task_manager import ChatTaskManager, FormBlockResult, handle_form_submit
from services.scheduler.task_definition_input import TaskDefinitionInputError, task_definition_input
from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeAdapter, ScheduledTaskChangeSetService
from services.changeset.contracts import NormalizeRequest
from services.agent.tool_executor import ToolExecutor
from tests.tool_runtime_support import IdentityDB
from tests.test_scheduled_task_changeset_adapter import _Db, _Repo
from tests.test_scheduled_task_request_content import TARGETS


DEFINITION = {"name": "示例日报", "prompt": "统计昨天示例店的已付款订单，按平台汇总，排除退款订单", "schedule_type": "daily", "time_str": "08:00"}
TASK = {"id": "task-a", "revision": 2, "status": "active", "schedule_enabled": True,
        **{k: v for k, v in DEFINITION.items() if k != "time_str"},
        "cron_expr": "0 8 * * *", "timezone": "Asia/Shanghai",
        "push_target": {"type": "web", "user_id": "u1"}}


@pytest.fixture
def no_parser():
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(side_effect=AssertionError("structured calls must not parse again"))) as call:
        yield call
        call.assert_not_awaited()


def manager(task=None):
    return ChatTaskManager(_Db(rows=[task or TASK]), "u1", "o1", submission_mode="apply_if_allowed", structured_input=True)


@pytest.mark.parametrize("definition,missing", [
    (DEFINITION, []),
    ({"prompt": DEFINITION["prompt"]}, ["schedule_type", "time_str"]),
    ({"prompt": "检查示例数据", "schedule_type": "once"}, ["run_at"]),
    ({"prompt": "检查示例数据", "schedule_type": "weekly", "time_str": "08:00"}, ["weekdays"]),
    ({"prompt": "检查示例数据", "schedule_type": "monthly", "time_str": "08:00"}, ["day_of_month"]),
])
def test_definition_missing_fields_are_explicit(definition, missing):
    args = deepcopy(definition)
    result = task_definition_input(args, operation="create")
    assert result["missing_fields"] == missing
    assert result["changes"]["prompt"] == args["prompt"]
    assert args == definition


@pytest.mark.parametrize("extra", [
    {"time_str": "25:00"}, {"time_str": 800}, {"prompt": " "}, {"schedule_type": "hourly"},
    {"weekdays": [True]}, {"weekdays": [-1]}, {"weekdays": [7]}, {"weekdays": [1, 1]},
    {"weekdays": []}, {"day_of_month": 0}, {"day_of_month": 32},
    {"run_at": "2030-10-01T08:00"}, {"run_at": "tomorrow"},
    {"user_id": "other"}, {"execution_policy": {"allowed_tools": ["erp_execute"]}},
    {"push_target": {"type": "web", "user_id": "other"}},
])
async def test_bad_fields_fail_without_reinterpretation_or_submission(extra, no_parser):
    host = manager()
    with patch.object(host, "_begin_request", AsyncMock()) as submit:
        result = await host.handle("create", {"definition": {**DEFINITION, **extra}})
    assert result["success"] is False
    submit.assert_not_awaited()


async def test_model_create_has_no_description_fallback(no_parser):
    host = manager()
    result = await host.handle("create", {"description": "每天八点做示例日报"})
    assert result["success"] is False and "definition" in result["text"]


async def test_complete_chat_fields_reach_original_submission_with_actor_target(no_parser, monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(), "scheduled_task_direct_enabled", True)
    executor = ToolExecutor(IdentityDB(), "u1", "c1", "o1", tool_entrypoint="model", task_id="turn-a")
    executor._parent_messages = [{"role": "user", "content": "创建一个定时任务。" + DEFINITION["prompt"] + "，每天八点钟发给我看"}]
    args = {"action": "create", "definition": deepcopy(DEFINITION), "recipient": "我"}
    row = {"id": "cs-a", "operation": "create", "status": "applied"}
    with patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch.object(ChatTaskManager, "_begin_request", AsyncMock(return_value={"type": "change_set", "data": row, "text": "任务已创建。"})) as submit:
        result = await executor.execute("manage_scheduled_task", args, call_id="call-a")
    assert result.metadata["change_set"] == row
    assert result.summary == "任务已创建。"
    assert submit.call_args.args == ("create", {**DEFINITION, "push_target": TASK["push_target"], "timezone": "Asia/Shanghai"})
    assert args == {"action": "create", "definition": DEFINITION, "recipient": "我"}


async def test_incomplete_form_keeps_business_and_completes_without_model(no_parser):
    host = manager()
    definition = {"prompt": DEFINITION["prompt"]}
    with patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch.object(host, "_begin_request", AsyncMock()) as initial:
        form = await host.handle("create", {"definition": definition})
    initial.assert_not_awaited()
    data = {field["name"]: field.get("default_value") for field in form["fields"]}
    assert data["prompt"] == definition["prompt"]
    assert data["time_str"] == data["schedule_type"] == ""
    data.update(schedule_type="daily", time_str="08:00")
    with patch("services.permissions.checker.check_permission", AsyncMock(return_value=True)), \
         patch("services.scheduler.chat_task_manager._propose_form_change", AsyncMock(return_value={"success": True})) as submit:
        await handle_form_submit(_Db(), "u1", "o1", "scheduled_task_create", data)
    assert submit.call_args.kwargs["definition"]["prompt"] == DEFINITION["prompt"]
    assert submit.call_args.kwargs["definition"]["time_str"] == "08:00"


@pytest.mark.parametrize("patch_fields", [{"time_str": "10:00"}, {"name": "新名称"}, {"weekdays": [1, 5], "schedule_type": "weekly"}])
async def test_update_submits_only_changed_fields(patch_fields, no_parser):
    host = manager()
    with patch.object(host, "_begin_request", AsyncMock(return_value={"type": "change_set"})) as submit:
        result = await host.handle("update", {"task_id": "task-a", "definition": patch_fields})
    assert result["type"] == "change_set"
    assert submit.call_args.args == ("update", patch_fields, TASK)


async def test_output_format_keeps_all_original_business_requirements(no_parser):
    host = manager()
    with patch.object(host, "_begin_request", AsyncMock(return_value={"type": "change_set"})) as submit:
        await host.handle("update", {"task_id": "task-a", "definition": {"output_format": "表格"}})
    assert submit.call_args.args[1] == {"prompt": TASK["prompt"] + "\n输出格式：表格"}


async def test_missing_update_field_form_contains_only_patch_and_resolved_id(no_parser):
    import json
    from pathlib import Path
    host = manager()
    form = await host.handle("update", {"task_id": "task-a", "definition": {"schedule_type": "weekly"}})
    fixture = json.loads((Path(__file__).parent / 'fixtures/scheduled_task_structured_form.json').read_text())
    assert {**form, 'form_id': fixture['form_id']} == fixture  # Same payload rendered by the frontend contract test.
    data = {field["name"]: field.get("default_value") for field in form["fields"]}
    assert set(data) == {"task_id", "schedule_type", "weekdays", "_submission_mode", "_structured_input"}
    data["weekdays"] = [1, 5]
    with patch("services.permissions.checker.check_permission", AsyncMock(return_value=True)), \
         patch("services.scheduler.chat_task_manager._propose_form_change", AsyncMock(return_value={"success": True})) as submit:
        await handle_form_submit(_Db(rows=[TASK]), "u1", "o1", "scheduled_task_update", data)
    assert submit.call_args.kwargs["definition"] == {"schedule_type": "weekly", "weekdays": [1, 5], "timezone": "Asia/Shanghai"}
    assert submit.call_args.kwargs["task_id"] == "task-a"


@pytest.mark.parametrize("recipient,target", [("我", TASK["push_target"]), ("我（企微）", {"type": "wecom_user", "wecom_userid": "mapped-self"})])
async def test_recipient_is_resolved_from_current_user_options(recipient, target, no_parser):
    import json
    host = manager()
    targets = [*TARGETS, {"label": "推送给我（企微）", "value": json.dumps({"type": "wecom_user", "wecom_userid": "mapped-self"})}]
    with patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=targets)), \
         patch.object(host, "_begin_request", AsyncMock()) as submit:
        await host.handle("update", {"task_id": "task-a", "definition": {}, "recipient": recipient})
    assert submit.call_args.args[1] == {"push_target": target}


async def test_unknown_recipient_requires_selection_and_does_not_redirect_to_self(no_parser):
    host = manager()
    with patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch.object(host, "_begin_request", AsyncMock()) as submit:
        form = await host.handle("create", {"definition": DEFINITION, "recipient": "不存在的示例群"})
    submit.assert_not_awaited()
    fields = {field["name"]: field for field in form["fields"]}
    assert fields["push_target"]["default_value"] == ""
    assert fields["prompt"]["default_value"] == DEFINITION["prompt"]


@pytest.mark.parametrize("operation", ["update", "pause", "resume", "delete"])
async def test_supplied_unknown_id_cannot_retarget_by_name(operation, no_parser):
    host = manager()
    with patch.object(host, "_begin_request", AsyncMock()) as submit, \
         patch.object(host, "_propose_chat_change", AsyncMock()) as mutate:
        result = await host.handle(operation, {"task_id": "wrong", "task_name": TASK["name"], "definition": {"time_str": "10:00"}})
    assert "未找到" in result["text"]
    submit.assert_not_awaited()
    mutate.assert_not_awaited()


@pytest.mark.parametrize("operation", ["pause", "resume", "delete"])
async def test_lifecycle_actions_use_existing_submission_and_receipt(operation, no_parser):
    task = {**TASK, "status": "paused" if operation == "resume" else "active", "schedule_enabled": operation != "resume"}
    host = manager(task)
    row = {"id": "cs-a", "operation": operation, "status": "awaiting_approval" if operation == "delete" else "applied"}
    with patch.object(ScheduledTaskChangeSetService, "begin", AsyncMock(return_value=row)) as submit:
        result = await host.handle(operation, {"task_id": "task-a"})
    assert result["data"] == row
    assert submit.call_args.kwargs["resource_id"] == "task-a"
    assert submit.call_args.kwargs["submission_mode"] == "apply_if_allowed"
    assert ("确认" if operation == "delete" else "已") in result["text"]


@pytest.mark.parametrize("base,patch_fields,cron", [
    ({**TASK, "schedule_type": "weekly", "cron_expr": "0 8 * * 1", "weekdays": [1]}, {"weekdays": [1, 5]}, "0 8 * * 1,5"),
    ({**TASK, "schedule_type": "monthly", "cron_expr": "0 8 1 * *", "day_of_month": 1}, {"day_of_month": 15}, "0 8 15 * *"),
    (TASK, {"time_str": "10:00"}, "0 10 * * *"),
    (TASK, {"schedule_type": "weekly", "weekdays": [1]}, "0 8 * * 1"),
])
async def test_calendar_patch_updates_actual_cron_and_preserves_business(base, patch_fields, cron):
    context = SimpleNamespace(operation="update", base_snapshot=base, policy_snapshot={})
    result = await ScheduledTaskChangeAdapter(_Db(), user_id="u1", org_id="o1").normalize(NormalizeRequest(context=context, proposed_snapshot=patch_fields))
    assert result.proposed_snapshot["cron_expr"] == cron
    for key in ("prompt", "name", "push_target"):
        assert result.proposed_snapshot[key] == base[key]


async def test_panel_ai_calls_model_once_and_uses_same_definition_contract():
    from services.scheduler.task_nl_parser import parse_structured_task_request
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value={"definition": DEFINITION, "recipient": "我"})) as call:
        result = await parse_structured_task_request("每天八点生成示例日报发给我")
    call.assert_awaited_once()
    assert result == task_definition_input(DEFINITION, operation="create", recipient="我")
    assert "request_parts" not in call.call_args.kwargs["system_prompt"]


async def test_panel_output_only_patch_is_not_a_new_instruction():
    from services.scheduler.task_nl_parser import parse_structured_task_request
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value={"definition": {"output_format": "表格"}})):
        result = await parse_structured_task_request("改用表格显示", operation="update")
    assert result["changes"] == {"output_format": "表格"}


def test_panel_route_is_additive_and_rejects_invalid_model_fields():
    from fastapi.testclient import TestClient
    from tests.test_scheduled_tasks_routes import FakeDB, _build_app
    with patch("api.routes.scheduled_tasks.check_permission", AsyncMock(return_value=True)), \
         patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value={"definition": DEFINITION, "recipient": "我"})):
        response = TestClient(_build_app(FakeDB())).post("/api/scheduled-tasks/parse", json={"text": "示例任务", "structured_fields": True})
    assert response.status_code == 200
    assert response.json()["data"]["prompt"] == DEFINITION["prompt"]
    with patch("api.routes.scheduled_tasks.check_permission", AsyncMock(return_value=True)), \
         patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value={"definition": {"prompt": ""}})):
        response = TestClient(_build_app(FakeDB())).post("/api/scheduled-tasks/parse", json={"text": "示例任务", "structured_fields": True})
    assert response.status_code == 422


@pytest.mark.parametrize('mode,enabled', [('proposal', True), ('apply_if_allowed', False)])
async def test_structured_form_keeps_confirmation_gate_and_explicit_date(mode, enabled, no_parser, monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(), 'scheduled_task_direct_enabled', enabled)
    host = ChatTaskManager(_Db(), 'u1', 'o1', submission_mode=mode, structured_input=True)
    with patch('services.scheduler.chat_task_manager._load_push_targets', AsyncMock(return_value=TARGETS)):
        form = await host.handle('create', {'definition': {'prompt': '给出学习建议', 'schedule_type': 'once'}})
    data = {f['name']: f.get('default_value') for f in form['fields']}
    data['run_at'] = '2030-10-01T10:00'
    with patch('services.permissions.checker.check_permission', AsyncMock(return_value=True)), \
         patch('services.scheduler.chat_task_manager._propose_form_change', AsyncMock(return_value={'success': True})) as submit:
        result = await handle_form_submit(_Db(), 'u1', 'o1', 'scheduled_task_create', data)
    assert result['success']
    assert submit.call_args.kwargs['submission_mode'] == 'proposal'
    assert submit.call_args.kwargs['definition']['run_at'] == '2030-10-01T10:00:00+08:00'


@pytest.mark.parametrize('definition', [{'run_at': '2030-10-01T10:00:00+08:00'}, {'weekdays': [1]}, {'day_of_month': 15}])
async def test_incompatible_calendar_patch_cannot_silently_do_nothing(definition, no_parser):
    host = manager()
    with patch.object(host, '_begin_request', AsyncMock()) as submit:
        result = await host.handle('update', {'task_id': TASK['id'], 'definition': definition})
    assert result['success'] is False
    submit.assert_not_awaited()


async def test_source_placeholder_still_requires_user_choice(no_parser):
    host = manager()
    with patch('services.scheduler.chat_task_manager._load_push_targets', AsyncMock(return_value=TARGETS)), \
         patch.object(host, '_begin_request', AsyncMock()) as submit:
        form = await host.handle('create', {'definition': DEFINITION, 'description': '每天八点统计【实际店铺名】订单'})
    assert form['type'] == 'form'
    assert '实际店铺名称' in form['description']
    submit.assert_not_awaited()


class ChainDb(IdentityDB):
    def __init__(self, task, operation):
        super().__init__()
        self.tasks = _Db(rows=[task], rpc_data={'outcome': {'create': 'created', 'update': 'updated', 'pause': 'paused', 'resume': 'resumed', 'delete': 'deleted'}[operation], 'new_revision': 3})
        self.commits = []

    def table(self, name):
        return self.tasks.table(name) if name == 'scheduled_tasks' else super().table(name)

    def rpc(self, name, params):
        self.commits.append((name, deepcopy(params)))
        return self.tasks.rpc(name, params)


@pytest.mark.parametrize('operation', ['create', 'update', 'pause', 'resume', 'delete'])
async def test_real_tool_changeset_authorization_commit_and_replay(operation, no_parser, monkeypatch):
    from core.config import get_settings
    from config.chat_tools import get_core_tools
    from services.planner import CapabilityRegistry, PlanCandidate, PlanStep, PlannerFramework
    from services.scheduler.task_submission import submission_receipt
    from services.scheduler.scheduled_task_change_adapter import DEFAULT_TASK_LIMITS
    monkeypatch.setattr(get_settings(), 'scheduled_task_direct_enabled', True)
    release = PlannerFramework(CapabilityRegistry.from_tool_schemas(get_core_tools(org_id='o1'))).release(
        PlanCandidate(target={}, input_contract={}, output_contract={'result': 'report'},
                      steps=(PlanStep('read', '读取示例订单', ('erp_agent',)),), candidate_tools=('erp_agent',)))
    task = {**TASK, **DEFAULT_TASK_LIMITS, 'user_id': 'u1', 'org_id': 'o1', 'execution_policy': release.tool_policy,
            'plan_snapshot': release.as_dict(), 'data_scope': {'kind': 'task_prompt'}}
    if operation == 'resume':
        task.update(status='paused', schedule_enabled=False)
    db, repo = ChainDb(task, operation), _Repo()
    executor = ToolExecutor(db, 'u1', 'c1', 'o1', tool_entrypoint='model', task_id='turn')
    args = {'action': operation}
    if operation == 'create': args['definition'] = DEFINITION
    else: args['task_id'] = task['id']
    if operation == 'update': args['definition'] = {'time_str': '10:00'}
    pending = []
    with patch('services.scheduler.chat_task_manager._load_push_targets', AsyncMock(return_value=TARGETS)), \
         patch('services.scheduler.scheduled_task_change_adapter.ChangeSetRepository', return_value=repo), \
         patch('services.permissions.checker.check_permission', AsyncMock(return_value=True)), \
         patch.object(ScheduledTaskChangeSetService, '_build_release', AsyncMock(return_value=release)), \
         patch('services.agent.scheduled_task_agent.ScheduledTaskAgent.execute', AsyncMock(side_effect=AssertionError('management must not run business query'))), \
         patch('services.websocket_manager.ws_manager.send_to_user', AsyncMock()) as notify, \
         patch('asyncio.create_task', side_effect=lambda coro: pending.append(coro)):
        result = await executor.execute('manage_scheduled_task', args, call_id='chain-call')
        assert result.metadata['change_set']['status'] == ('applied' if operation in {'pause', 'resume'} else 'draft')
        for coro in pending:
            await coro
        final = 'awaiting_approval' if operation == 'delete' else 'applied'
        assert repo.row['status'] == final, repo.checks
        assert notify.call_args.args[1]['payload']['status'] == final
        assert len(db.commits) == (0 if operation == 'delete' else 1)
        if db.commits:
            rpc, params = db.commits[0]
            assert rpc == 'commit_scheduled_task_changeset'
            assert (params['p_org_id'], params['p_user_id'], params['p_operation']) == ('o1', 'u1', operation)
            assert params['p_definition']['prompt'] == TASK['prompt']
            if operation == 'update': assert params['p_definition']['cron_expr'] == '0 10 * * *'
        retry_executor = ToolExecutor(db, 'u1', 'c1', 'o1', tool_entrypoint='model', task_id='turn')
        replay = await retry_executor.execute('manage_scheduled_task', args, call_id='chain-call')
        assert replay.metadata['change_set']['id'] == result.metadata['change_set']['id']
        assert replay.summary == submission_receipt(repo.row)
        assert len(db.commits) == (0 if operation == 'delete' else 1)


@pytest.mark.parametrize('permission_mode,enabled', [('plan', True), ('auto', False)])
async def test_interactive_model_never_reparses_when_confirmation_mode_changes(permission_mode, enabled, no_parser, monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(), 'scheduled_task_direct_enabled', enabled)
    executor = ToolExecutor(IdentityDB(), 'u1', 'c1', 'o1', tool_entrypoint='model', permission_mode=permission_mode)
    result = await executor.execute('manage_scheduled_task', {'action': 'create', 'description': '每天八点给我学习建议'}, call_id='new-chat')
    assert result.status == 'error'
    assert 'definition' in result.summary
