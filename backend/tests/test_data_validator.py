"""运行时内部数据校验器测试。"""

import pytest

from services.agent.agent_result import AgentResult
from services.agent.runtime.artifact_collector import collect_tool_result
from services.agent.runtime.data_validator import (
    execute_validation_plan,
    requires_validation,
    run_internal_validation,
)
from services.agent.runtime.runtime_state import RuntimeState
from services.agent.tool_output import ColumnMeta, OutputFormat


PLATFORM_ROWS = [
    {"platform": "抖音", "total_orders": 350, "valid_orders": 286},
    {"platform": "淘宝", "total_orders": 504, "valid_orders": 414},
    {"platform": "1688", "total_orders": 150, "valid_orders": 143},
    {"platform": "京东", "total_orders": 354, "valid_orders": 163},
    {"platform": "系统", "total_orders": 17, "valid_orders": 3},
    {"platform": "小红书", "total_orders": 29, "valid_orders": 27},
    {"platform": "快手", "total_orders": 35, "valid_orders": 20},
    {"platform": "拼多多", "total_orders": 4069, "valid_orders": 3541},
]


def _state() -> tuple[RuntimeState, str]:
    result = AgentResult(
        summary="付款订单按平台统计",
        format=OutputFormat.TABLE,
        data=PLATFORM_ROWS,
        columns=[
            ColumnMeta(name="platform", dtype="str", label="平台"),
            ColumnMeta(name="total_orders", dtype="int", label="总订单数"),
            ColumnMeta(name="valid_orders", dtype="int", label="有效订单数"),
        ],
        source="erp_agent",
        metadata={"date": "2026-07-17"},
    )
    evidence = collect_tool_result(result, tool_call_id="erp-1")[0]
    state = RuntimeState.observing()
    state.ledger.record(evidence)
    return state, evidence.fingerprint


@pytest.mark.parametrize(
    ("field", "expected"),
    [("total_orders", 1439), ("valid_orders", 1056)],
)
def test_excludes_pinduoduo_and_recalculates_metric(
    field: str,
    expected: int,
) -> None:
    state, artifact_id = _state()

    result = execute_validation_plan(
        state,
        {
            "artifact_id": artifact_id,
            "filters": [
                {"field": "platform", "operator": "ne", "value": "拼多多"}
            ],
            "metrics": [
                {"field": field, "operation": "sum", "alias": "订单合计"}
            ],
        },
    )

    assert result.status == "success"
    assert result.data == [{"订单合计": expected}]
    assert result.metadata["filtered_rows"] == 7
    assert result.metadata["derived_from"] == [artifact_id]
    assert result.metadata["deterministic"] is True


def test_grouped_sum_matches_detail_total() -> None:
    state, artifact_id = _state()

    result = execute_validation_plan(
        state,
        {
            "artifact_id": artifact_id,
            "filters": [
                {"field": "platform", "operator": "not_in", "value": ["拼多多"]}
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

    assert len(result.data) == 7
    assert sum(row["有效订单"] for row in result.data) == 1056


def test_repeated_compute_is_stable() -> None:
    state, artifact_id = _state()
    arguments = {
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
    }

    first = execute_validation_plan(state, arguments)
    second = execute_validation_plan(state, arguments)

    assert first.data == second.data == [{"有效订单合计": 1056}]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            {"artifact_id": "missing", "metrics": []},
            "数据证据不存在或不可用",
        ),
        (
            {
                "filters": [
                    {"field": "missing", "operator": "eq", "value": 1}
                ],
                "metrics": [{"operation": "count", "alias": "数量"}],
            },
            "字段不存在",
        ),
        (
            {
                "metrics": [
                    {"field": "platform", "operation": "sum", "alias": "合计"}
                ]
            },
            "sum 字段包含非数值",
        ),
    ],
)
def test_invalid_compute_fails_without_guessing(
    arguments: dict,
    message: str,
) -> None:
    state, artifact_id = _state()
    arguments.setdefault("artifact_id", artifact_id)

    result = execute_validation_plan(state, arguments)

    assert result.status == "error"
    assert message in result.summary
    assert result.metadata["retryable"] is False


def test_internal_validator_is_not_a_model_tool_or_context_message() -> None:
    state, _ = _state()
    state.user_text = "除了拼多多以外，其他平台共多少单"
    state.requires_validation = requires_validation(
        state.user_text,
        has_data_context=True,
    )

    assert run_internal_validation(state) is True
    assert state.verified_final_pending is True
    assert state.validation_error is None
    assert state.ledger.snapshot().evidence[-1].payload["source"] == "runtime_validator"


def test_initial_platform_request_preserves_grouped_shape() -> None:
    state, _ = _state()
    state.user_text = "查询昨天付款订单，按照平台划分"

    assert run_internal_validation(state) is True
    rows = state.ledger.snapshot().evidence[-1].payload["data"]
    assert len(rows) == 8
    assert rows[0]["平台"] == "抖音"
    assert rows[0]["总订单数"] == 350
    assert rows[-1]["平台"] == "拼多多"


def test_validation_failure_does_not_open_final_gate() -> None:
    state, _ = _state()
    state.user_text = "排除一个无法识别的平台后重新汇总"
    state.requires_validation = True

    assert run_internal_validation(state) is False
    assert state.verified_final_pending is False
    assert state.validation_error


def test_legacy_data_compute_evidence_reuses_original_source() -> None:
    state, source_id = _state()
    legacy = execute_validation_plan(
        state,
        {
            "artifact_id": source_id,
            "filters": [
                {"field": "platform", "operator": "ne", "value": "拼多多"}
            ],
            "metrics": [
                {
                    "field": "total_orders",
                    "operation": "sum",
                    "alias": "总订单合计",
                }
            ],
        },
    )
    legacy.source = "data_compute"
    state.ledger.record(collect_tool_result(legacy, tool_call_id="old")[0])
    state.user_text = "按照有效订单计算"

    assert run_internal_validation(state) is True
    verified = state.ledger.snapshot().evidence[-1]
    assert verified.payload["data"] == [{"有效订单合计": 1056}]
    assert verified.payload["metadata"]["derived_from"] == [source_id]


def test_persisted_evidence_is_reusable_but_not_recommitted() -> None:
    state, artifact_id = _state()
    original = state.ledger.snapshot().evidence[0]
    payload = dict(original.payload)
    payload["metadata"] = {
        **dict(payload.get("metadata") or {}),
        "persisted": True,
    }
    from dataclasses import replace

    restored = replace(original, payload=payload)
    next_state = RuntimeState.observing()
    next_state.restore((restored,))

    result = execute_validation_plan(
        next_state,
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

    assert result.data == [{"有效订单合计": 1056}]
    assert next_state.persistence_projection() == []


def test_oversized_evidence_is_not_projected_to_actor_commit() -> None:
    state, _ = _state()
    evidence = state.ledger.snapshot().evidence[0]
    payload = dict(evidence.payload)
    payload["data"] = [{"value": "x" * 1_048_576}]
    from dataclasses import replace

    oversized = RuntimeState.observing()
    oversized.ledger.record(replace(evidence, payload=payload))

    assert oversized.persistence_projection() == []
