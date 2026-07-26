"""AR-05 ports 仅依赖领域协议且保持可导入。"""

from __future__ import annotations

from inspect import iscoroutinefunction, signature

import pytest

from services.agent.runtime.domain import (
    ActionId,
    ActionResult,
    ActionResultStatus,
    RuntimeScope,
    ScopeKind,
)
from services.agent.runtime.ports import (
    ExecutionOutcome,
    ExecutionReceipt,
    ExecutorPort,
    ModelPort,
    ProjectionPort,
    RuntimeEventPort,
    RuntimeRepositoryPort,
)


def test_required_ports_are_protocols() -> None:
    for port in (
        RuntimeRepositoryPort,
        ModelPort,
        ExecutorPort,
        RuntimeEventPort,
        ProjectionPort,
    ):
        assert port._is_protocol  # type: ignore[attr-defined]


def test_io_port_operations_are_async() -> None:
    operations = (
        RuntimeRepositoryPort.submit_command,
        RuntimeRepositoryPort.transition_run,
        ModelPort.complete,
        ExecutorPort.dispatch,
        ExecutorPort.reconcile,
        RuntimeEventPort.append,
        ProjectionPort.project,
    )
    assert all(iscoroutinefunction(operation) for operation in operations)


def test_executor_separates_dispatch_from_reconcile() -> None:
    dispatch = signature(ExecutorPort.dispatch)
    reconcile = signature(ExecutorPort.reconcile)
    assert "request" in dispatch.parameters
    assert "request" not in reconcile.parameters


def _result() -> ActionResult:
    return ActionResult(
        action_id=ActionId("action-1"),
        scope=RuntimeScope(ScopeKind.USER, "user:u-1", "u-1", None),
        status=ActionResultStatus.SUCCESS,
        result_hash="result-hash",
    )


def _receipt_combination_is_valid(
    outcome: ExecutionOutcome,
    has_result: bool,
    has_external_receipt: bool,
    has_ambiguity_evidence: bool,
) -> bool:
    if (outcome is ExecutionOutcome.COMPLETED) != has_result:
        return False
    if outcome is ExecutionOutcome.ACCEPTED and not has_external_receipt:
        return False
    if outcome is ExecutionOutcome.UNKNOWN and not has_ambiguity_evidence:
        return False
    return True


RECEIPT_COMBINATIONS = tuple(
    (outcome, has_result, has_external, has_ambiguity)
    for outcome in ExecutionOutcome
    for has_result in (False, True)
    for has_external in (False, True)
    for has_ambiguity in (False, True)
)


@pytest.mark.parametrize(
    ("outcome", "has_result", "has_external", "has_ambiguity"),
    RECEIPT_COMBINATIONS,
)
def test_every_execution_receipt_combination(
    outcome: ExecutionOutcome,
    has_result: bool,
    has_external: bool,
    has_ambiguity: bool,
) -> None:
    arguments = {
        "outcome": outcome,
        "request_hash": "request-hash",
        "result": _result() if has_result else None,
        "external_receipt": {"task_id": "external-1"} if has_external else {},
        "ambiguity_evidence": {"timeout": True} if has_ambiguity else {},
    }
    if _receipt_combination_is_valid(
        outcome,
        has_result,
        has_external,
        has_ambiguity,
    ):
        assert ExecutionReceipt(**arguments).outcome is outcome
    else:
        with pytest.raises(ValueError):
            ExecutionReceipt(**arguments)


@pytest.mark.parametrize("request_hash", ("", " "))
def test_execution_receipt_rejects_blank_request_hash(
    request_hash: str,
) -> None:
    with pytest.raises(ValueError, match="request_hash is required"):
        ExecutionReceipt(
            outcome=ExecutionOutcome.FAILED,
            request_hash=request_hash,
        )
