"""完整 ProviderContextPlan 与唯一 payload 投影测试。"""

from __future__ import annotations

import pytest

from services.agent.runtime.context import (
    ProviderContextPlan,
    prepare_provider_context_plan,
)
from services.agent.runtime.runtime_state import RuntimeState


def _payload() -> tuple[list[dict], list[dict]]:
    return (
        [
            {"role": "system", "content": "固定规则"},
            {"role": "user", "content": "当前问题"},
        ],
        [{
            "type": "function",
            "function": {
                "name": "search",
                "parameters": {"type": "object"},
            },
        }],
    )


def test_plan_is_deterministic_and_projects_isolated_payload() -> None:
    messages, tools = _payload()
    first = ProviderContextPlan.build(
        messages=messages,
        tools=tools,
        context_epoch_id="epoch-1",
        model_step=0,
        stable_prefix_blocks=1,
    )
    second = ProviderContextPlan.build(
        messages=messages,
        tools=tools,
        context_epoch_id="epoch-1",
        model_step=0,
        stable_prefix_blocks=1,
    )

    projected_messages, projected_tools = first.project()
    projected_messages[0]["content"] = "投影修改"
    projected_tools.clear()

    assert first == second
    assert first.matches(messages, tools) is True
    assert first.project() == (messages, tools)
    assert "固定规则" not in repr(first)


def test_plan_snapshot_does_not_drift_when_source_mutates() -> None:
    messages, tools = _payload()
    plan = ProviderContextPlan.build(
        messages=messages,
        tools=tools,
        context_epoch_id="epoch-1",
        model_step=0,
        stable_prefix_blocks=1,
    )

    messages.append({"role": "assistant", "content": "新增"})

    assert plan.matches(messages, tools) is False
    assert len(plan.project()[0]) == 2


def test_plan_rejects_non_json_provider_payload() -> None:
    messages, tools = _payload()
    messages[0]["content"] = object()

    with pytest.raises(TypeError):
        ProviderContextPlan.build(
            messages=messages,
            tools=tools,
            context_epoch_id="epoch-1",
            model_step=0,
            stable_prefix_blocks=1,
        )


def test_projection_detects_json_round_trip_shape_change() -> None:
    messages, tools = _payload()
    messages[0]["content"] = ("规则一", "规则二")
    plan = ProviderContextPlan.build(
        messages=messages,
        tools=tools,
        context_epoch_id="epoch-1",
        model_step=0,
        stable_prefix_blocks=1,
    )

    assert plan.matches(messages, tools) is False


def test_projection_mismatch_fails_before_provider(monkeypatch) -> None:
    messages, tools = _payload()
    state = RuntimeState(
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
        stable_prefix_blocks=1,
    )
    monkeypatch.setattr(ProviderContextPlan, "matches", lambda *_args: False)

    with pytest.raises(
        RuntimeError,
        match="CONTEXT_PLAN_PROJECTION_MISMATCH",
    ):
        prepare_provider_context_plan(state, messages=messages, tools=tools)

    assert state.context_receipts == []
    assert state.current_context_plan is None
    assert messages[0]["content"] == "固定规则"


def test_plan_build_failure_fails_before_provider() -> None:
    messages, tools = _payload()
    messages[0]["content"] = object()
    state = RuntimeState(
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
        stable_prefix_blocks=1,
    )

    with pytest.raises(TypeError):
        prepare_provider_context_plan(state, messages=messages, tools=tools)

    assert state.context_receipts == []
    assert state.current_context_plan is None


def test_matching_pruning_receipt_is_bound_to_provider_model_step() -> None:
    messages, tools = _payload()
    state = RuntimeState(
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
        stable_prefix_blocks=1,
        pruning_receipts=[
            {"model_step": 0, "outcome": "pruned", "source_hash": "hash-0"},
            {"model_step": 2, "outcome": "pruned", "source_hash": "hash-2"},
        ],
        compaction_receipts=[
            {"model_step": 0, "outcome": "compacted", "prefix_hash": "prefix-0"},
            {"model_step": 2, "outcome": "failed", "prefix_hash": "prefix-2"},
        ],
    )

    prepare_provider_context_plan(state, messages=messages, tools=tools)

    assert state.context_receipts[0]["pruning_receipt"] == {
        "model_step": 0,
        "outcome": "pruned",
        "source_hash": "hash-0",
    }
    assert state.context_receipts[0]["compaction_receipt"] == {
        "model_step": 0,
        "outcome": "compacted",
        "prefix_hash": "prefix-0",
    }
