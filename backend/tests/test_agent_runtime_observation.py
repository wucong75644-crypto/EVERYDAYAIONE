"""通用交付运行时观察模式测试。"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.agent.agent_result import AgentResult
from services.agent.runtime.artifact_collector import collect_tool_result
from services.agent.runtime.artifact_ledger import (
    ArtifactEvidence,
    ArtifactKind,
    ArtifactSource,
    ArtifactStatus,
)
from services.agent.runtime.completion_gate import CompletionDecision
from services.agent.runtime.policies.data_accuracy import DataAccuracyPolicy
from services.agent.runtime.runtime_contract import (
    ContractSource,
    RunContract,
    build_run_contract,
)
from services.agent.runtime.runtime_state import RuntimeState
from services.agent.tool_output import ColumnMeta
from services.handlers.chat.tool_loop import apply_tool_results


def _data_result() -> AgentResult:
    return AgentResult(
        summary="按平台统计",
        data=[{"platform": "淘宝", "valid_orders": Decimal("414")}],
        columns=[
            ColumnMeta(name="platform", dtype="str", label="平台"),
            ColumnMeta(name="valid_orders", dtype="decimal", label="有效订单"),
        ],
        source="erp_agent",
        metadata={"metric": "valid_orders"},
    )


def test_collector_builds_stable_data_evidence() -> None:
    first = collect_tool_result(_data_result(), tool_call_id="call-1")
    second = collect_tool_result(_data_result(), tool_call_id="call-1")

    assert len(first) == 1
    assert first[0].kind == ArtifactKind.DATA_RESULT
    assert first[0].status == ArtifactStatus.READY
    assert first[0].fingerprint == second[0].fingerprint
    assert first[0].payload["data"][0]["valid_orders"] == "414"


def test_persistence_projection_includes_reusable_model_view() -> None:
    state = RuntimeState.observing()
    state.ledger.record(
        collect_tool_result(_data_result(), tool_call_id="call-1")[0]
    )

    item = state.persistence_projection()[0]

    assert item["model_view"]["tier"] == "full"
    assert item["model_view"]["rows"] == [
        {"platform": "淘宝", "valid_orders": "414"},
    ]
    assert item["model_view"]["metric_definitions"] == {}
    assert item["byte_size"] > 0
    assert len(item["content_hash"]) == 64
    assert item["expires_at"] is None


def test_oversized_rows_keep_metadata_view_instead_of_dropping_evidence() -> None:
    state = RuntimeState.observing()
    result = AgentResult(
        summary="超大结果",
        data=[{"payload": "数" * 1_100_000}],
        columns=[ColumnMeta(name="payload", dtype="str", label="数据")],
        source="erp_agent",
    )
    state.ledger.record(
        collect_tool_result(result, tool_call_id="call-large")[0]
    )

    item = state.persistence_projection()[0]

    assert item["rows"] is None
    assert item["model_view"]["tier"] == "metadata"
    assert item["model_view"]["row_count"] == 1


def test_collector_ignores_plain_text_and_markdown() -> None:
    assert collect_tool_result("平台 | 有效订单\n淘宝 | 414", tool_call_id="call-1") == ()
    result = AgentResult(summary="平台 | 有效订单\n淘宝 | 414")
    assert collect_tool_result(result, tool_call_id="call-1") == ()


def test_ledger_deduplicates_same_tool_evidence() -> None:
    state = RuntimeState.observing()
    evidence = collect_tool_result(_data_result(), tool_call_id="call-1")[0]

    assert state.ledger.record(evidence) is True
    assert state.ledger.record(evidence) is False
    assert len(state.ledger.snapshot().evidence) == 1


def test_data_policy_rejects_invalid_rows() -> None:
    evidence = collect_tool_result(_data_result(), tool_call_id="call-1")[0]
    invalid = dict(evidence.payload)
    invalid["data"] = ["not-an-object"]

    result = DataAccuracyPolicy().validate_artifact(
        RunContract.empty(),
        evidence,
        invalid,
    )

    assert result.accepted is False
    assert result.reason == "each data row must be an object"


def test_apply_tool_results_observes_without_changing_protocol() -> None:
    state = RuntimeState.observing()
    messages: list[dict] = []
    blocks = [
        {
            "type": "tool_step",
            "tool_call_id": "call-1",
            "status": "running",
        }
    ]
    context = SimpleNamespace(update_from_result=lambda *_: None)

    images = apply_tool_results(
        tool_results=[
            (
                {"id": "call-1", "name": "erp_agent"},
                _data_result(),
                False,
                "按平台统计",
            )
        ],
        messages=messages,
        content_blocks=blocks,
        start_times={},
        tool_context=context,
        runtime_state=state,
    )

    assert images == []
    assert messages[0]["role"] == "tool"
    assert messages[0]["content"][0] == {
        "type": "text",
        "text": "按平台统计",
    }
    assert blocks[0]["status"] == "completed"
    assert state.ledger.ready_kinds() == frozenset({ArtifactKind.DATA_RESULT})


def test_tool_result_records_evidence_without_extra_model_message() -> None:
    state = RuntimeState.observing()
    state.user_text = "查询昨天付款订单，按照平台划分"
    messages: list[dict] = []
    result = AgentResult(
        summary="按平台统计",
        data=[
            {"platform": "tb", "total_orders": 10},
            {"platform": "pdd", "total_orders": 20},
        ],
        columns=[
            ColumnMeta(name="platform", dtype="str", label="平台"),
            ColumnMeta(name="total_orders", dtype="int", label="总订单数"),
        ],
        source="erp_agent",
    )

    apply_tool_results(
        tool_results=[
            (
                {"id": "erp-1", "name": "erp_agent"},
                result,
                False,
                result.summary,
            )
        ],
        messages=messages,
        content_blocks=[],
        start_times={},
        tool_context=SimpleNamespace(update_from_result=lambda *_: None),
        runtime_state=state,
    )

    assert len(messages) == 1
    assert messages[0]["tool_call_id"] == "erp-1"
    assert "runtime_validator" not in str(messages)
    assert len(state.ledger.snapshot().evidence) == 1


@pytest.mark.parametrize("status", ["error", "timeout", "empty", "partial"])
def test_non_ready_data_does_not_enter_observation_ledger(status: str) -> None:
    state = RuntimeState.observing()
    result = _data_result()
    result.status = status

    apply_tool_results(
        tool_results=[
            (
                {"id": "call-1", "name": "erp_agent"},
                result,
                status in {"error", "timeout"},
                result.summary,
            )
        ],
        messages=[],
        content_blocks=[],
        start_times={},
        tool_context=SimpleNamespace(update_from_result=lambda *_: None),
        runtime_state=state,
    )

    assert state.ledger.snapshot().evidence == ()


def test_explicit_contract_parses_known_artifacts() -> None:
    contract = build_run_contract(
        {
            "_run_contract": {
                "required_artifacts": ["data_result", "chart"],
                "optional_artifacts": ["table"],
                "policy_ids": ["data_accuracy"],
            }
        }
    )

    assert contract.source == ContractSource.CALLER
    assert contract.required_artifacts == frozenset(
        {ArtifactKind.DATA_RESULT, ArtifactKind.CHART}
    )
    assert contract.optional_artifacts == frozenset({ArtifactKind.TABLE})
    assert contract.policy_ids == ("data_accuracy",)


def test_contract_is_empty_without_explicit_caller_parameter() -> None:
    assert build_run_contract({}).enabled is False
    assert build_run_contract({"content": "帮我画图"}).enabled is False


def test_unknown_artifact_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown artifact kind"):
        build_run_contract(
            {"_run_contract": {"required_artifacts": ["unknown"]}}
        )


def test_completion_gate_continues_then_requests_final_synthesis() -> None:
    contract = RunContract(
        required_artifacts=frozenset({ArtifactKind.DATA_RESULT}),
        source=ContractSource.CALLER,
        confidence=1.0,
    )
    state = RuntimeState(contract=contract, observation_only=False)

    assert state.evaluate().decision == CompletionDecision.CONTINUE
    evidence = collect_tool_result(_data_result(), tool_call_id="call-1")[0]
    state.ledger.record(evidence)
    assert state.evaluate().decision == CompletionDecision.FINALIZE

    state.request_final_synthesis()
    assert state.final_synthesis_pending is True
    assert state.final_tools([{"function": {"name": "query"}}]) == []


def test_completion_gate_blocks_when_budget_exhausted() -> None:
    contract = RunContract(
        required_artifacts=frozenset({ArtifactKind.CHART}),
        source=ContractSource.CALLER,
        confidence=1.0,
    )
    state = RuntimeState(contract=contract, observation_only=False)

    result = state.evaluate(budget_exhausted=True)

    assert result.decision == CompletionDecision.BLOCKED
    assert result.reason == "missing required artifacts: chart"


def test_completion_snapshot_only_counts_ready_evidence() -> None:
    contract = RunContract(
        required_artifacts=frozenset({ArtifactKind.CHART}),
        source=ContractSource.CALLER,
        confidence=1.0,
    )
    state = RuntimeState(contract=contract, observation_only=False)
    state.ledger.record(
        ArtifactEvidence(
            kind=ArtifactKind.CHART,
            source=ArtifactSource.TOOL_RESULT,
            status=ArtifactStatus.FAILED,
            fingerprint="failed-chart",
        )
    )

    assert state.evaluate().decision == CompletionDecision.CONTINUE
    assert (
        state.evaluate(budget_exhausted=True).decision
        == CompletionDecision.FALLBACK
    )
