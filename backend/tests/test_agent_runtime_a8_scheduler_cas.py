from pathlib import Path
from types import SimpleNamespace
import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.scheduler_cas import (
    PostgresTenantScopedSchedulerCasStore,
    SchedulerCasError,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_05_agent_runtime_scheduler_cas.sql"
ROLLBACK = ROOT / "migrations/rollback/227_05_agent_runtime_scheduler_cas_rollback.sql"


def test_a8_migration_is_runtime_owned_and_force_rls() -> None:
    sql = MIGRATION.read_text()
    assert "CREATE TABLE agent_runtime_scheduler_cas_facts" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "_assert_agent_runtime_actor(TRUE)" in sql
    assert "everydayai_agent_runtime_worker" in sql
    assert "REVOKE ALL ON TABLE agent_runtime_scheduler_cas_facts" in sql
    for forbidden in ("GRANT SELECT", "GRANT INSERT", "GRANT UPDATE", "GRANT DELETE"):
        assert forbidden not in sql
    for field in (
        "org_id", "user_id", "task_id", "run_id", "action_id", "attempt_id",
        "request_hash", "execution_token", "idempotency_key",
        "state_version", "lease_expires_at",
    ):
        assert field in sql


def test_a8_rollback_is_guarded_and_identity_preserving() -> None:
    rollback = ROLLBACK.read_text()
    assert "AR174_A8_ROLLBACK_GUARD_FACTS_EXIST" in rollback
    assert "DROP TABLE IF EXISTS agent_runtime_scheduler_cas_facts" in rollback
    assert "227_04" not in rollback


class _RpcCall:
    def __init__(self, result):
        self._result = result

    async def execute(self):
        return SimpleNamespace(data=self._result)


class _Database:
    scope = DatabaseScope(
        actor_user_id=None,
        org_id="00000000-0000-0000-0000-000000000001",
        access_kind=DatabaseAccessKind.AGENT_RUNTIME,
        request_id="a8-test",
    )

    def __init__(self, result):
        self.result = result
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RpcCall(self.result)


def _attempt():
    return SimpleNamespace(
        attempt_id="00000000-0000-0000-0000-000000000011",
        action_id="00000000-0000-0000-0000-000000000012",
        run_id="00000000-0000-0000-0000-000000000013",
        request_hash="a" * 64,
        idempotency_key="scheduler-a-create",
        scope=SimpleNamespace(
            kind=SimpleNamespace(value="user"), scope_id="user-a",
            org_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
        ),
        lease=SimpleNamespace(fencing_token="00000000-0000-0000-0000-000000000014"),
    )


@pytest.mark.asyncio
async def test_postgres_store_uses_only_worker_scoped_narrow_rpc() -> None:
    database = _Database({"outcome": "created", "state_version": 1})
    store = PostgresTenantScopedSchedulerCasStore(database)
    result = await store.cas(
        attempt=_attempt(), task_id="task-a", expected_version=0,
        operation="create", payload={"schedule": "daily"},
    )
    assert result["outcome"] == "created"
    assert store.non_production_ready is True
    assert store.production_ready is False
    name, params = database.calls[0]
    assert name == "mutate_agent_runtime_scheduler_cas"
    assert params["p_execution_token"].endswith("0014")
    assert params["p_request_hash"] == "a" * 64


@pytest.mark.asyncio
async def test_postgres_store_maps_fencing_and_cas_conflicts_failure_closed() -> None:
    for outcome, error in (("fenced", "SCHEDULER_FENCED"), ("cas_conflict", "SCHEDULER_VERSION_CONFLICT")):
        database = _Database({"outcome": outcome})
        store = PostgresTenantScopedSchedulerCasStore(database)
        with pytest.raises(SchedulerCasError, match=error):
            await store.cas(
                attempt=_attempt(), task_id="task-a", expected_version=1,
                operation="update", payload={},
            )


def test_postgres_store_rejects_non_runtime_database_scope() -> None:
    database = SimpleNamespace(scope=DatabaseScope(
        actor_user_id=None, org_id="00000000-0000-0000-0000-000000000001",
        access_kind=DatabaseAccessKind.RUNTIME, request_id="a8-test",
    ))
    with pytest.raises(ValueError, match="WORKER_SCOPED_DATABASE_CLIENT_REQUIRED"):
        PostgresTenantScopedSchedulerCasStore(database)
