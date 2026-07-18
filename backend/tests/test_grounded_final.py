"""确定性 Grounded Final 输出边界测试。"""

from types import SimpleNamespace

import pytest

from services.agent.agent_result import AgentResult
from services.agent.runtime.artifact_collector import collect_tool_result
from services.agent.runtime.data_compute import execute_data_compute
from services.agent.runtime.grounded_final import (
    build_grounded_final,
    is_data_compute_follow_up,
)
from services.agent.runtime.runtime_state import RuntimeState
from services.agent.tool_output import ColumnMeta, OutputFormat
from services.handlers.chat.tool_loop import apply_tool_results


def _state() -> tuple[RuntimeState, str]:
    source = AgentResult(
        summary="付款订单按平台统计",
        format=OutputFormat.TABLE,
        data=[
            {"platform": "淘宝", "valid_orders": 414},
            {"platform": "抖音", "valid_orders": 286},
            {"platform": "1688", "valid_orders": 143},
            {"platform": "京东", "valid_orders": 163},
            {"platform": "系统", "valid_orders": 3},
            {"platform": "小红书", "valid_orders": 27},
            {"platform": "快手", "valid_orders": 20},
            {"platform": "拼多多", "valid_orders": 3541},
        ],
        columns=[
            ColumnMeta(name="platform", dtype="str", label="平台"),
            ColumnMeta(name="valid_orders", dtype="int", label="有效订单"),
        ],
        source="erp_agent",
    )
    evidence = collect_tool_result(source, tool_call_id="erp-1")[0]
    state = RuntimeState.observing()
    state.ledger.record(evidence)
    return state, evidence.fingerprint


@pytest.mark.parametrize(
    "text",
    [
        "除了拼多多以外其他平台共多少单",
        "按照有效订单计算",
        "重新计算",
        "求和",
        "recalculate the total excluding Pinduoduo",
    ],
)
def test_high_confidence_data_follow_up_is_gated(text: str) -> None:
    assert is_data_compute_follow_up(text, has_data_context=True) is True
    assert is_data_compute_follow_up(text, has_data_context=False) is False


def test_unrelated_follow_up_keeps_normal_streaming() -> None:
    assert (
        is_data_compute_follow_up("帮我解释一下这个趋势", has_data_context=True)
        is False
    )


def test_grounded_final_uses_verified_compute_not_model_text() -> None:
    state, artifact_id = _state()
    result = execute_data_compute(
        state,
        {
            "artifact_id": artifact_id,
            "filters": [
                {"field": "platform", "operator": "ne", "value": "拼多多"}
            ],
            "metrics": [
                {
                    "field": "valid_orders",
                    "operation": "sum",
                    "alias": "有效订单合计",
                }
            ],
        },
    )
    apply_tool_results(
        tool_results=[
            (
                {"id": "compute-1", "name": "data_compute"},
                result,
                False,
                result.summary,
            )
        ],
        messages=[],
        content_blocks=[],
        start_times={},
        tool_context=SimpleNamespace(update_from_result=lambda *_: None),
        runtime_state=state,
    )

    assert state.grounded_final_pending is True
    assert state.final_tools([{"function": {"name": "erp_agent"}}]) == []
    assert build_grounded_final(state) == "重新计算结果：有效订单合计：1,056。"


def test_grouped_grounded_final_renders_deterministic_table() -> None:
    state, artifact_id = _state()
    result = execute_data_compute(
        state,
        {
            "artifact_id": artifact_id,
            "filters": [
                {"field": "platform", "operator": "ne", "value": "拼多多"}
            ],
            "group_by": ["platform"],
            "metrics": [
                {
                    "field": "valid_orders",
                    "operation": "sum",
                    "alias": "有效订单",
                }
            ],
        },
    )
    evidence = collect_tool_result(result, tool_call_id="compute-1")[0]
    state.ledger.record(evidence)
    state.request_grounded_final()

    final = build_grounded_final(state)

    assert "共 7 组" in final
    assert "| platform | 有效订单 |" in final
    assert "| 淘宝 | 414 |" in final
    assert "| 1688 | 143 |" in final
    assert "1,688" not in final
    assert "拼多多" not in final
