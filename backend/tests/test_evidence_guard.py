"""通用 Evidence Guard 行为测试。"""

from decimal import Decimal

from services.agent.agent_result import AgentResult
from services.agent.runtime.artifact_collector import collect_tool_result
from services.agent.runtime.evidence_guard.claim_extractor import (
    extract_numeric_claims,
)
from services.agent.runtime.evidence_guard.finalize import (
    GUARD_BLOCKED_TEXT,
    append_retry_context,
    review_final_draft,
)
from services.agent.runtime.evidence_guard.guard import EvidenceGuard
from services.agent.runtime.evidence_guard.models import GuardDecision
from services.agent.runtime.runtime_state import RuntimeState
from services.agent.tool_output import ColumnMeta


def _state(
    values: list[dict],
    *,
    columns: list[ColumnMeta] | None = None,
    question: str = "",
) -> RuntimeState:
    state = RuntimeState.observing()
    state.user_text = question
    result = AgentResult(
        summary="结构化工具结果",
        data=values,
        columns=columns,
        source="test_tool",
    )
    state.ledger.record(collect_tool_result(result, tool_call_id="call-1")[0])
    return state


def test_extracts_formatted_currency_percent_and_units() -> None:
    claims = extract_numeric_claims("金额¥42,260.19，退款率8.32%，共1,053单。")

    assert [claim.value for claim in claims] == [
        Decimal("42260.19"),
        Decimal("8.32"),
        Decimal("1053"),
    ]
    assert [claim.unit for claim in claims] == ["¥", "%", "单"]


def test_ignores_markdown_list_ordinals() -> None:
    claims = extract_numeric_claims("1. 第一项\n2. 第二项，共20单")

    assert [claim.value for claim in claims] == [Decimal("20")]


def test_guard_skips_without_structured_evidence() -> None:
    receipt = EvidenceGuard().verify(
        "模型自由回答2026",
        RuntimeState.observing().ledger.snapshot(),
    )

    assert receipt.decision == GuardDecision.SKIP


def test_guard_accepts_direct_values_row_count_and_percent_ratio() -> None:
    state = _state(
        [
            {"platform": "tb", "orders": 1053, "rate": 0.0832},
            {"platform": "jd", "orders": 20, "rate": 0.02},
        ],
        columns=[
            ColumnMeta("platform", "text", "平台"),
            ColumnMeta("orders", "integer", "订单"),
            ColumnMeta("rate", "numeric", "退款率"),
        ],
    )

    receipt = EvidenceGuard().verify(
        "共2组，订单1,053单，退款率8.32%。",
        state.ledger.snapshot(),
    )

    assert receipt.decision == GuardDecision.PASS
    assert receipt.issues == ()


def test_guard_rejects_number_not_supported_by_any_evidence() -> None:
    state = _state([{"valid_orders": 1053}])

    receipt = EvidenceGuard().verify(
        "有效订单共1,457单。",
        state.ledger.snapshot(),
    )

    assert receipt.decision == GuardDecision.RETRY
    assert receipt.issues[0].claim.value == Decimal("1457")


def test_retry_returns_to_model_then_blocks_after_limit() -> None:
    state = _state(
        [{"valid_orders": 1053}],
        columns=[ColumnMeta("valid_orders", "integer", "有效订单")],
        question="有效订单有多少？",
    )
    messages: list[dict] = []

    first = review_final_draft(state, "有效订单共1457单")
    append_retry_context(messages, "有效订单共1457单", first)
    second = review_final_draft(state, "还是1457单")
    third = review_final_draft(state, "仍然1457单")

    assert first.decision == GuardDecision.RETRY
    assert second.decision == GuardDecision.RETRY
    assert third.decision == GuardDecision.BLOCK
    assert third.text == GUARD_BLOCKED_TEXT
    assert messages[0]["role"] == "assistant"
    assert "evidence_validation_error" in messages[1]["content"]


def test_verified_sandbox_result_allows_derived_number() -> None:
    state = _state(
        [{"valid_orders_without_pdd": 1053}],
        columns=[
            ColumnMeta(
                "valid_orders_without_pdd",
                "integer",
                "有效订单",
            )
        ],
        question="有效订单共多少？",
    )

    decision = review_final_draft(state, "计算完成，共1,053单。")

    assert decision.decision == GuardDecision.PASS
    assert decision.text == "计算完成，共1,053单。"


def test_structured_string_dimensions_and_dates_are_supported() -> None:
    state = _state(
        [{"platform": "1688", "date": "2026-07-17"}],
        columns=[
            ColumnMeta("platform", "text", "平台"),
            ColumnMeta("date", "text", "日期"),
        ],
    )

    receipt = EvidenceGuard().verify(
        "1688平台在日期2026-07-17有数据。",
        state.ledger.snapshot(),
    )

    assert receipt.decision == GuardDecision.PASS


def test_sandbox_emit_table_becomes_guard_evidence() -> None:
    state = RuntimeState.observing()
    result = AgentResult(
        summary="stdout",
        source="code_execute",
        emit_payloads=[
            {
                "kind": "table",
                "title": "计算结果",
                "columns": ["合计"],
                "rows": [{"合计": 1053}],
            }
        ],
    )
    for evidence in collect_tool_result(result, tool_call_id="sandbox-1"):
        state.ledger.record(evidence)

    decision = review_final_draft(state, "最终合计1,053单。")

    assert decision.decision == GuardDecision.PASS
    assert len(state.ledger.snapshot().evidence) == 2


def test_same_number_in_unrelated_field_does_not_prove_claim() -> None:
    state = _state(
        [{"product_code": "1053", "amount": 20}],
        columns=[
            ColumnMeta("product_code", "integer", "商品编码"),
            ColumnMeta("amount", "numeric", "金额"),
        ],
        question="金额是多少？",
    )

    wrong = review_final_draft(state, "金额是1,053元。")
    correct = EvidenceGuard().verify(
        "商品编码是1,053。",
        state.ledger.snapshot(),
        question="商品编码是什么？",
    )

    assert wrong.decision == GuardDecision.RETRY
    assert correct.decision == GuardDecision.PASS
