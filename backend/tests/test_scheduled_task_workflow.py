"""定时任务规划、权限策略与 Completion Gate 的纯逻辑测试。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from services.scheduler.scheduled_task_workflow import (
    ScheduledExecutionPolicy,
    completion_gate,
    create_plan,
    stable_json_hash,
    validate_plan,
)


def test_config_hash_is_stable_across_key_order():
    assert stable_json_hash({"name": "日报", "prompt": "查数据"}) == stable_json_hash(
        {"prompt": "查数据", "name": "日报"},
    )


def test_plan_cannot_expand_to_unavailable_tool():
    with pytest.raises(ValueError, match="未授权工具"):
        validate_plan({
            "objective": "日报",
            "allowed_tools": ["erp_execute"],
            "steps": [{"tools": ["erp_execute"], "required": True}],
        }, available_tools={"erp_agent"}, timeout_sec=180)


def test_optional_step_does_not_become_required_tool():
    _, policy = validate_plan({
        "objective": "日报",
        "allowed_tools": ["erp_agent", "code_execute", "file_search"],
        "steps": [
            {"id": "query", "tools": ["erp_agent"], "required": True},
            {"id": "lookup", "tools": ["file_search"], "required": False},
            {"id": "report", "tools": ["code_execute"], "required": True},
        ],
    }, available_tools={"erp_agent", "code_execute", "file_search"}, timeout_sec=180)

    assert policy.required_tools == {"erp_agent", "code_execute"}


def test_completion_gate_rejects_wrap_up_even_with_text():
    policy = ScheduledExecutionPolicy.from_dict({
        "allowed_tools": ["erp_agent"], "required_tools": ["erp_agent"],
    }, timeout_sec=180)
    result = SimpleNamespace(
        stop_reason="wrap_up_failure", is_llm_synthesis=True,
        text="查询超时，我缩小范围后继续查询。", tools_called=["erp_agent"],
    )
    gate = completion_gate(result=result, policy=policy)
    assert gate["passed"] is False
    assert "loop_stopped:wrap_up_failure" in gate["reasons"]


def test_completion_gate_requires_user_confirmed_tool_coverage():
    policy = ScheduledExecutionPolicy.from_dict({
        "allowed_tools": ["erp_agent", "code_execute"],
        "required_tools": ["erp_agent", "code_execute"],
    }, timeout_sec=180)
    result = SimpleNamespace(
        stop_reason="", is_llm_synthesis=True, text="已完成", tools_called=["erp_agent"],
        tool_outcomes=[{"tool_name": "erp_agent", "status": "success"}],
    )
    gate = completion_gate(result=result, policy=policy)
    assert gate["passed"] is False
    assert "required_tools_missing:code_execute" in gate["reasons"]


def test_completion_gate_requires_required_tool_to_succeed_not_just_be_requested():
    policy = ScheduledExecutionPolicy.from_dict({
        "allowed_tools": ["erp_agent"], "required_tools": ["erp_agent"],
    }, timeout_sec=180)
    result = SimpleNamespace(
        stop_reason="", is_llm_synthesis=True, text="已完成", tools_called=["erp_agent"],
        tool_outcomes=[{"tool_name": "erp_agent", "status": "timeout"}],
    )
    gate = completion_gate(result=result, policy=policy)
    assert gate["passed"] is False
    assert "required_tools_missing:erp_agent" in gate["reasons"]


@pytest.mark.asyncio
async def test_create_plan_uses_gateway_stream_and_closes_session() -> None:
    async def stream_chat(**_kwargs):
        yield SimpleNamespace(
            content='{"objective":"日报","allowed_tools":["erp_agent"],"steps":[{"id":"query","intent":"查询数据","tools":["erp_agent"],"required":true}],"output_contract":{"allow_empty_result":false,"required_evidence":[]}}',
            prompt_tokens=4,
            completion_tokens=6,
            finish_reason="stop",
        )

    session = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    gateway = Mock(open_chat=Mock(return_value=session))
    settings = SimpleNamespace(agent_loop_model="qwen3.5-plus")
    definition = {
        "name": "日报",
        "prompt": "查询日报",
        "schedule_type": "cron",
        "timeout_sec": 180,
        "push_target": {},
    }
    with (
        patch("config.chat_tools.get_core_tools", return_value=[{"function": {"name": "erp_agent", "description": "查询"}}]),
        patch("services.scheduler.scheduled_task_workflow.preflight_allowed_tool_names", return_value={"erp_agent"}),
        patch("core.config.get_settings", return_value=settings),
        patch("services.model_gateway.get_model_gateway", return_value=gateway),
    ):
        plan, policy = await create_plan(db=Mock(), org_id="org-1", definition=definition)

    assert plan["objective"] == "日报"
    assert policy.allowed_tools == frozenset({"erp_agent"})
    gateway.open_chat.assert_called_once()
    session.close.assert_awaited_once()
