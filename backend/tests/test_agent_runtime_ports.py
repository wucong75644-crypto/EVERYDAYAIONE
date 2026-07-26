"""AR-05 ports 仅依赖领域协议且保持可导入。"""

from __future__ import annotations

from inspect import iscoroutinefunction, signature

from services.agent.runtime.ports import (
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
