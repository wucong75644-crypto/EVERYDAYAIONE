from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.infrastructure.postgres.projection_recovery import (
    PostgresProjectionDeadRecovery,
)


@dataclass
class _Response:
    data: object

    async def execute(self) -> "_Response":
        return self


class _Database:
    def __init__(self, access_kind: DatabaseAccessKind) -> None:
        self.scope = DatabaseScope(
            actor_user_id=str(uuid4()),
            org_id=str(uuid4()),
            access_kind=access_kind,
            request_id="projection-recovery-test",
        )
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(self, name: str, params: dict[str, object]) -> _Response:
        self.calls.append((name, params))
        if name == "requeue_agent_projection_dead":
            return _Response({
                "outcome": "requeued",
                "outbox_id": params["p_outbox_id"],
                "audit_id": str(uuid4()),
                "recovery_version": 2,
                "recovery_count": 2,
                "attempt_count": 9,
                "next_attempt_at": datetime.now(UTC).isoformat(),
            })
        return _Response({"outcome": "found", "items": []})


def test_adapter_requires_runtime_actor_scope() -> None:
    with pytest.raises(ValueError, match="RUNTIME_SCOPED"):
        PostgresProjectionDeadRecovery(_Database(DatabaseAccessKind.WORKER))


@pytest.mark.asyncio
async def test_adapter_passes_strict_recovery_bindings() -> None:
    database = _Database(DatabaseAccessKind.RUNTIME)
    adapter = PostgresProjectionDeadRecovery(database)
    outbox_id = str(uuid4())
    request_id = str(uuid4())
    not_before = datetime.now(UTC)

    receipt = await adapter.requeue(
        outbox_id=outbox_id,
        expected_recovery_version=1,
        expected_attempt_count=9,
        recovery_request_id=request_id,
        reason="validated operator recovery",
        not_before=not_before,
    )

    assert receipt.outcome == "requeued"
    assert database.calls == [(
        "requeue_agent_projection_dead",
        {
            "p_outbox_id": outbox_id,
            "p_expected_status": "dead",
            "p_expected_recovery_version": 1,
            "p_expected_attempt_count": 9,
            "p_recovery_request_id": request_id,
            "p_reason": "validated operator recovery",
            "p_not_before": not_before.isoformat(),
        },
    )]
