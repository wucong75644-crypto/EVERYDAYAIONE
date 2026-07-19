"""ToolLoopExecutor 新旧停止决策的旁路比较测试。"""

from unittest.mock import MagicMock

from services.agent.agent_result import AgentResult
from services.agent.loop_types import LoopConfig, LoopStrategy
from services.agent.runtime.validation import ToolEffect
from services.agent.stop_policy import FailureTracker, StopPolicyConfig
from services.agent.tool_loop_executor import ToolLoopExecutor


def _executor() -> ToolLoopExecutor:
    return ToolLoopExecutor(
        adapter=MagicMock(),
        executor=MagicMock(),
        all_tools=[],
        config=LoopConfig(
            max_turns=5,
            context_window=50000,
            tool_timeout=30,
        ),
        strategy=LoopStrategy(),
    )


def _observe(
    executor: ToolLoopExecutor,
    result: object,
    *,
    audit_status: str,
    tool_call_id: str = "tc1",
) -> None:
    executor._validation_runtime.observe_result(
        result,
        tool_call_id=tool_call_id,
        tool_name="local_data",
        model_step=1,
        turns_remaining=4,
        audit_status=audit_status,
    )


def _evaluate(
    executor: ToolLoopExecutor,
    result: object,
    *,
    audit_status: str,
) -> tuple[bool, str]:
    executor._turn_tool_outcomes = [
        ("local_data", result, audit_status),
    ]
    return executor._evaluate_turn_outcomes(
        FailureTracker(),
        StopPolicyConfig(),
        model_step=1,
        turns_remaining=4,
    )


def test_success_decisions_are_aligned() -> None:
    executor = _executor()
    executor._start_validation_observation("task-success")
    _observe(executor, "订单数据", audit_status="success")

    should_wrap, _reason = _evaluate(
        executor, "订单数据", audit_status="success",
    )

    assert should_wrap is False
    assert executor._validation_comparisons == [{
        "task_id": "task-success",
        "model_step": 1,
        "old_decision": "continue",
        "new_decisions": ["continue"],
        "old_action": "continue",
        "new_action": "continue",
        "aligned": True,
    }]


def test_structured_fatal_exposes_real_legacy_difference() -> None:
    executor = _executor()
    executor._start_validation_observation("task-difference")
    failure = AgentResult(
        summary="执行失败",
        status="error",
        error_message="backend rejected request",
        metadata={"retryable": False},
    )
    _observe(executor, failure, audit_status="error")

    should_wrap, _reason = _evaluate(
        executor, failure, audit_status="error",
    )

    assert should_wrap is False
    comparison = executor._validation_comparisons[0]
    assert comparison["old_decision"] == "continue"
    assert comparison["new_decisions"] == ["fail"]
    assert comparison["aligned"] is False


def test_parallel_turn_uses_most_conservative_new_action() -> None:
    executor = _executor()
    executor._start_validation_observation("task-parallel")
    failure = AgentResult(
        summary="执行失败",
        status="error",
        metadata={"retryable": False},
    )
    _observe(executor, failure, audit_status="error", tool_call_id="tc1")
    _observe(executor, "另一工具成功", audit_status="success", tool_call_id="tc2")
    executor._compare_validation_decision(
        old_decision="continue",
        model_step=1,
    )

    comparison = executor._validation_comparisons[0]
    assert comparison["new_decisions"] == ["fail", "continue"]
    assert comparison["new_action"] == "stop"
    assert comparison["aligned"] is False


def test_shadow_comparison_failure_does_not_change_old_decision() -> None:
    executor = _executor()
    executor._start_validation_observation("task-fail-open")
    executor._validation_runtime = None
    executor._turn_tool_outcomes = [
        ("local_data", "订单数据", "success"),
    ]

    should_wrap, _reason = executor._evaluate_turn_outcomes(
        FailureTracker(),
        StopPolicyConfig(),
        model_step=1,
        turns_remaining=4,
    )

    assert should_wrap is False
    assert executor._validation_comparisons == []


def test_tool_loop_observer_passes_existing_write_effect_metadata() -> None:
    executor = _executor()
    executor._start_validation_observation("task-write")
    executor._validation_runtime.observe_result = MagicMock(return_value=(
        MagicMock(
            result_class=MagicMock(value="success"),
        ),
        MagicMock(value="continue"),
    ))

    executor._observe_validation_result(
        result="完成",
        tool_call_id="tc-write",
        tool_name="erp_execute",
        hook_ctx=MagicMock(turn=1, task_id="task-write"),
        audit_status="success",
        elapsed_ms=3,
    )

    kwargs = executor._validation_runtime.observe_result.call_args.kwargs
    assert kwargs["effect"] == ToolEffect.NON_IDEMPOTENT_WRITE
