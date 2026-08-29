"""定时任务规划、权限策略与 Completion Gate 的纯逻辑测试。"""
from types import SimpleNamespace

import pytest

from services.scheduler.scheduled_task_workflow import (
    ScheduledExecutionPolicy,
    completion_gate,
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
