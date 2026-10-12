"""Existing scheduling boundaries must survive the new read-only tool bridge."""
import json
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from services.tools import ToolCall, ToolPolicy, build_legacy_catalog
from services.scheduler.task_definition_input import task_definition_input, TaskDefinitionInputError
from services.scheduler.chat_task_manager import handle_form_submit
from tests.test_tool_policy import context
from tests.test_scheduled_task_structured_input import manager, DEFINITION
from tests.test_scheduled_task_request_content import TARGETS
from tests.tool_runtime_support import IdentityDB


SKILLS = [{"skill_id": "report", "revision": "v1"}]


@pytest.mark.parametrize("mode", ["scheduled", "preflight"])
@pytest.mark.parametrize("action", ["auto", "search", "read", "transcript", "read_comments", "list_connections",
                                    "create_post", "create_comment", "set_like", "upload_video"])
def test_background_reach_actions_remain_read_only(mode, action):
    args = {"task": "test", "platform": "twitter", "action": action, "query": "test",
            "url": "https://x.com/i/status/123", "content": "test",
            "connection_id": "00000000-0000-0000-0000-000000000001"}
    if action == "upload_video":
        args.update(platform="youtube", url="", title="test", category_id=1, visibility="private",
                    media=[{"name": "video.mp4", "resource_ref": "fref1_fixture", "role": "video"}])
    ctx = context(execution_mode=mode, task_id="task", permission_mode="auto",
                  authorized_tool_names={"agent_reach"}, feature_flags={"agent_reach_enabled": True},
                  authorization_snapshot={"version": 1, "allowed_tools": ["agent_reach"]})
    policy = ToolPolicy(build_legacy_catalog())
    call = ToolCall("call", "agent_reach", args)
    decision = policy.plan_batches([call], ctx)[0][0].decision
    writes = action in {"create_post", "create_comment", "set_like", "upload_video"}
    assert decision.outcome == ("deny" if writes else "allow")
    if writes:
        assert decision.reason == "agent_reach_background_write_forbidden"
    assert policy.decide("agent_reach", replace(ctx, authorization_snapshot={"allowed_tools": []}), args).outcome == "deny"


def test_preflight_catalog_respects_reach_feature_flag(monkeypatch):
    from core.config import get_settings
    from services.scheduler.scheduled_task_workflow import preflight_allowed_tool_names
    settings = get_settings()
    for enabled in (False, True):
        monkeypatch.setattr(settings, "agent_reach_enabled", enabled)
        assert ("agent_reach" in preflight_allowed_tool_names("org")) is enabled


@pytest.mark.parametrize("skills", [None, {}, "report", [{"skill_id": "report"}],
    [{"skill_id": "report", "revision": "v1", "allowed_tools": ["erp_execute"]}],
    SKILLS * 2, [{"skill_id": f"skill{i}", "revision": "v1"} for i in range(5)]])
def test_invalid_skill_intent_is_rejected_before_submission(skills):
    with pytest.raises(TaskDefinitionInputError):
        task_definition_input({"skills": skills}, operation="update")


async def test_chat_form_reuses_skill_selection_without_creating_before_confirmation(monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(), "skill_catalog_enabled", True)
    monkeypatch.setattr(get_settings(), "skill_runtime_enabled", True)
    host = manager()
    with patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch.object(host, "_begin_request", AsyncMock()) as begin:
        form = await host.handle("create", {"definition": {**DEFINITION, "skills": SKILLS}, "recipient": "我"})
    begin.assert_not_awaited()
    values = {field["name"]: field.get("default_value") for field in form["fields"]}
    assert json.loads(values["skills"]) == SKILLS
    with patch("services.permissions.checker.check_permission", AsyncMock(return_value=True)), \
         patch("services.scheduler.chat_task_manager._propose_form_change", AsyncMock(return_value={"success": True})) as submit:
        await handle_form_submit(IdentityDB(), "u1", "o1", "scheduled_task_create", values)
    assert submit.call_args.kwargs["definition"]["skills"] == SKILLS
    assert submit.call_args.kwargs["definition"]["prompt"] == DEFINITION["prompt"]


async def test_patch_without_skills_does_not_clear_binding():
    host = manager()
    with patch.object(host, "_begin_request", AsyncMock(return_value={"success": True})) as submit:
        await host.handle("update", {"task_id": "task-a", "definition": {"name": "new"}})
    assert submit.call_args.args[1] == {"name": "new"}
    assert task_definition_input({"skills": []}, operation="update")["changes"] == {"skills": []}


async def test_partial_update_form_preserves_skill_patch():
    host = manager()
    form = await host.handle("update", {"task_id": "task-a", "definition": {
        "skills": SKILLS, "schedule_type": "weekly"}})
    fields = {field["name"]: field.get("default_value") for field in form["fields"]}
    assert json.loads(fields["skills"]) == SKILLS
    assert "prompt" not in fields


@pytest.mark.parametrize("skills", ['not-json', '[{"skill_id":"report"}]', '[]'])
async def test_form_skill_validation_precedes_changeset(skills):
    with patch("services.permissions.checker.check_permission", AsyncMock(return_value=True)), \
         patch("services.scheduler.chat_task_manager._propose_form_change", AsyncMock(return_value={"success": True})) as submit:
        result = await handle_form_submit(IdentityDB(), "u1", "o1", "scheduled_task_create", {
            **DEFINITION, "_structured_input": True, "skills": skills})
    assert submit.await_count == (1 if skills == '[]' else 0)
    assert result["success"] is (skills == '[]')


@pytest.mark.parametrize("platform", ["bilibili", "xiaohongshu"])
@pytest.mark.parametrize("mode", ["scheduled", "preflight"])
async def test_real_executor_reuses_org_connection_and_scoped_read_handler(monkeypatch, platform, mode):
    from core.config import get_settings
    from services.agent.tool_executor import ToolExecutor
    from services.agent.agent_reach import handler
    from services.agent.agent_result import AgentResult
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_reach_enabled", True)
    monkeypatch.setattr(settings, "agent_reach_platforms", "bilibili,xiaohongshu")
    org, user = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
    connection_id = "00000000-0000-0000-0000-000000000003"
    db = IdentityDB(user, org)
    db.pool = None
    reads = []
    class Store:
        def __init__(self, pool, scope):
            assert scope.org_id == org and scope.actor_user_id == user
        def list(self):
            return [{"id": connection_id, "platform": platform}]
        def load(self, cid, target, *, write):
            assert cid == connection_id and target == platform and write is False
            reads.append(cid)
            return {"id": cid, "account_id": "test-account", "secret": {}}
    monkeypatch.setattr(handler, "Connections", Store)
    remote = AsyncMock(return_value=AgentResult(summary="verified source", status="success"))
    monkeypatch.setattr(handler, "execute", remote)
    executor = ToolExecutor(db, user, "scheduled_test", org, task_id="task", execution_mode=mode,
        allowed_tool_names={"agent_reach"}, tool_policy_snapshot={"version": 1, "allowed_tools": ["agent_reach"]})
    result = await executor.execute("agent_reach", {"task": "搜索", "action": "search",
        "platform": platform, "query": "电商运营"}, call_id="reach-read")
    assert result.status == "success" and result.summary == "verified source"
    assert len(reads) == 2  # Existing lock boundary rechecks the same connection.
    assert remote.call_args.kwargs["connection"]["id"] == connection_id
    db.active = False
    with pytest.raises(PermissionError, match="identity_or_authorization_unavailable"):
        await executor.execute("agent_reach", {"task": "搜索", "platform": platform}, call_id="revoked")
    assert remote.await_count == 1


async def test_existing_sandbox_file_survives_kernel_recreation(tmp_path):
    """Capability probe, not a live social/ERP monitoring acceptance test."""
    from services.sandbox.kernel_manager import KernelManager
    from services.sandbox.executor import SandboxExecutor
    from core.workspace import resolve_workspace_dir, resolve_staging_dir, resolve_output_dir
    root = str(tmp_path)
    workspace = resolve_workspace_dir(root, "owner", "org")
    staging = resolve_staging_dir(root, "owner", "org", "scheduled-probe")
    output = resolve_output_dir(root, "owner", "org")
    km = KernelManager()
    await km.start()
    executor = SandboxExecutor(workspace_dir=workspace, staging_dir=staging, output_dir=output,
        kernel_manager=km, conversation_id="scheduled-probe", timeout=15)
    try:
        saved = await executor.execute(
            "import json\nwith open('continuity-probe.json', 'w') as f:\n"
            "    json.dump({'likes': 100, 'orders': 12, 'report_total': 120}, f)\nprint('baseline_saved')",
            "isolated task continuity probe")
        assert saved.status == "success" and "baseline_saved" in saved.summary
        await km.destroy("scheduled-probe")
        restored = await executor.execute(
            "import json\nwith open('continuity-probe.json') as f:\n    old = json.load(f)\n"
            "print('like_delta', 140-old['likes'])\nprint('order_delta', 15-old['orders'])\n"
            "print('report_total', old['report_total'])", "read previous sample after kernel reset")
        assert restored.status == "success"
        assert "like_delta 40" in restored.summary and "order_delta 3" in restored.summary
        assert "report_total 120" in restored.summary
        other = resolve_workspace_dir(root, "other", "org")
        from pathlib import Path
        assert not (Path(other) / "continuity-probe.json").exists()
    finally:
        await km.shutdown()
