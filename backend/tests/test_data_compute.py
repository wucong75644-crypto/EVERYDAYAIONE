"""可信数据证据的确定性重算测试。"""

from types import SimpleNamespace

import pytest

from services.agent.agent_result import AgentResult
from services.agent.runtime.artifact_collector import collect_tool_result
from services.agent.runtime.data_compute import (
    build_data_context_prompt,
    execute_data_compute,
)
from services.agent.runtime.runtime_state import RuntimeState
from services.agent.tool_executor import ToolExecutor
from services.agent.tool_output import ColumnMeta, OutputFormat
from services.handlers.chat.tool_loop import prepare_tool_turn


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

    result = execute_data_compute(
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

    result = execute_data_compute(
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

    first = execute_data_compute(state, arguments)
    second = execute_data_compute(state, arguments)

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

    result = execute_data_compute(state, arguments)

    assert result.status == "error"
    assert message in result.summary
    assert result.metadata["retryable"] is False


def test_prompt_exposes_id_and_columns_without_copying_rows() -> None:
    state, artifact_id = _state()

    prompt = build_data_context_prompt(state)

    assert artifact_id in prompt
    assert "platform,total_orders,valid_orders" in prompt
    assert "拼多多" not in prompt
    assert "必须调用 data_compute" in prompt


def test_data_compute_tool_is_only_injected_with_ready_data() -> None:
    permission = SimpleNamespace(
        need_exit_attachment=False,
        get_reminder=lambda _turn: "",
    )
    context = SimpleNamespace(
        discovered_tools=set(),
        build_context_prompt=lambda: "",
    )
    empty_tools = prepare_tool_turn(
        core_tools=[],
        discovered_names=set(),
        org_id="org-1",
        turn=0,
        messages=[],
        tool_context=context,
        permission=permission,
        runtime_state=RuntimeState.observing(),
    )
    state, _ = _state()
    ready_tools = prepare_tool_turn(
        core_tools=[],
        discovered_names=set(),
        org_id="org-1",
        turn=0,
        messages=[],
        tool_context=context,
        permission=permission,
        runtime_state=state,
    )

    assert empty_tools == []
    assert ready_tools[0]["function"]["name"] == "data_compute"


@pytest.mark.asyncio
async def test_tool_executor_consumes_same_runtime_state() -> None:
    state, artifact_id = _state()
    executor = ToolExecutor(
        db=SimpleNamespace(),
        user_id="user-1",
        conversation_id="conv-1",
        org_id=None,
        runtime_state=state,
    )

    result = await executor.execute(
        "data_compute",
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

    result = execute_data_compute(
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
