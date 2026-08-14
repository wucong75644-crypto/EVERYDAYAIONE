import asyncio

import psycopg
import pytest

from core.db_scope import AsyncScopedDatabaseClient, DatabaseAccessKind, DatabaseScope
from core.local_db import AsyncLocalDBClient
from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.resolver import PostgresActionExecutorResolver
from services.agent.runtime.executors.specialist_contracts import ProviderReceipt, ProviderState
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.executors.specialist_registry import specialist_descriptor
from services.agent.runtime.infrastructure.postgres.action_repository import (
    PostgresActionRepository,
)
from services.agent.runtime.infrastructure.postgres.authorization import (
    PostgresActionAuthorizationRepository,
)
from services.agent.runtime.infrastructure.postgres.coordinator_recovery import (
    PostgresCoordinatorRecoveryRepository,
)
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _worker_rpc
from tests.test_agent_runtime_ar18_b6_child_cancel_postgres_external import (
    ORG, USER, _cancel, _claim_and_apply, _create, _prepare, _seed,
    _seed_child_action,
)


pytestmark = pytest.mark.external
ORG_B = "88888888-8888-4888-8888-888888888888"
USER_B = "77777777-7777-7777-7777-777777777777"


def test_b6_child_create_rejects_cross_tenant_scope(database: str) -> None:
    _prepare(database)
    ids = _seed(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO organizations(id) VALUES(%s)", (ORG_B,))
        conn.execute(
            "INSERT INTO org_members(org_id,user_id) VALUES(%s,%s)",
            (ORG_B, USER_B),
        )
        conn.commit()
    with pytest.raises(Exception, match="AGENT_CHILD_SCOPE_INVALID"):
        _create(database, ids, {"org_id": ORG_B, "user_id": USER_B})
    with pytest.raises(Exception, match="AGENT_CHILD_SCOPE_INVALID"):
        _create(database, ids, {"org_id": ORG, "user_id": USER_B})
    assert _create(database, ids, {"org_id": ORG, "user_id": USER})[
        "outcome"
    ] == "created"


def test_b6_terminal_child_with_unsettled_action_cannot_confirm(database: str) -> None:
    _prepare(database)
    root = _seed(database)
    child = _create(database, root)
    _seed_child_action(database, child_run_id=child["child_run_id"], parent=root)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runs SET status='completed',completed_at=clock_timestamp(),"
            "execution_token=NULL,lease_expires_at=NULL,blocking_action_count=0,"
            "child_terminal_result='{}',result_hash=%s,aggregation_revision=1 WHERE id=%s",
            ("8" * 64, child["child_run_id"]),
        )
        conn.commit()
    assert _cancel(database, root)["outcome"] == "cancelled"
    assert _claim_and_apply(database, "terminal-child-pending")["outcome"] == "applied"
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT status,terminal_kind,proof_hash FROM "
            "agent_runtime_child_run_cancel_intents WHERE parent_action_id=%s",
            (root["action"],),
        ).fetchone() == ("applied", None, None)


def test_b6_not_created_cancel_releases_reserve_exactly_once(database: str) -> None:
    _prepare(database)
    ids = _seed(database)
    assert _cancel(database, ids)["outcome"] == "cancelled"
    with pytest.raises(Exception, match="AGENT_CHILD_SCOPE_INVALID"):
        _create(database, ids, {"org_id": ORG_B, "user_id": USER_B})
    fenced = _create(database, ids)
    assert fenced["outcome"] == "cancel_fenced"
    assert fenced["status"] == "confirmed"
    claim = _worker_rpc(database, "claim_next_agent_action_reconciliation", (
        "release-finalizer", 120, 0,
    ))
    proof = _worker_rpc(database, "read_agent_child_run_cancel_intent_v1", (
        ids["action"], ids["attempt"], claim["execution_token"],
        claim["state_version"], ids["request_hash"],
    ))
    params = (
        ids["attempt"], claim["execution_token"], claim["state_version"],
        ids["request_hash"], proof["intent_id"], proof["proof_hash"], 7,
    )
    assert _worker_rpc(
        database, "finalize_agent_action_child_cancel_v1", params,
    )["outcome"] == "cancelled"
    assert _worker_rpc(
        database, "finalize_agent_action_child_cancel_v1", params,
    )["outcome"] == "already_cancelled"
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_action_cost_settlements "
            "WHERE attempt_id=%s AND kind='release'", (ids["attempt"],),
        ).fetchone()[0] == 1


def test_b6_real_action_loop_refunds_child_cancel_once(database: str) -> None:
    _prepare(database)
    ids = _seed(database)
    assert _create(database, ids)["outcome"] == "created"
    assert _cancel(database, ids)["outcome"] == "cancelled"
    proof = _claim_and_apply(database, "action-loop-proof")
    assert proof["outcome"] == "confirmed"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_action_attempts SET updated_at=clock_timestamp()-interval "
            "'2 minutes' WHERE id=%s", (ids["attempt"],),
        )
        conn.commit()

    class _ChildProvider:
        async def cancel(self, attempt, _receipt):
            return ProviderReceipt(
                state=ProviderState.CANCELLED, provider="child_run",
                request_hash=attempt.request_hash,
                evidence={
                    "cancel_confirmed": True, "fencing_confirmed": True,
                    "child_cancel_intent_id": proof["intent_id"],
                    "proof_hash": proof["proof_hash"],
                },
            )

    async def run() -> None:
        client = AsyncLocalDBClient(
            database.replace("postgres@", "everydayai_agent_runtime_worker@"),
            min_size=1, max_size=2,
        )
        await client.open()
        scoped = AsyncScopedDatabaseClient(client, DatabaseScope(
            actor_user_id=USER, org_id=ORG,
            access_kind=DatabaseAccessKind.AGENT_RUNTIME,
            request_id="b6-real-action-loop",
        ))
        try:
            descriptor = specialist_descriptor("image_agent")
            registry = ExecutorRegistry()
            registry.register(descriptor, SpecialistExecutor(
                executor_type=descriptor.executor_type, revision=1,
                provider=_ChildProvider(),
            ), safety_level="dangerous")
            driver = ActionLoopDriver(
                recovery_repository=PostgresCoordinatorRecoveryRepository(scoped),
                action_repository=PostgresActionRepository(scoped),
                authorization_repository=PostgresActionAuthorizationRepository(scoped),
                resolver=PostgresActionExecutorResolver(registry),
                worker_id="b6-real-action-loop", renew_interval=60,
            )
            assert await driver.reconcile_once() is True
        finally:
            await client.close()

    asyncio.run(run())
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_action_cost_settlements "
            "WHERE attempt_id=%s AND kind='refund' AND reserved_amount=7",
            (ids["attempt"],),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT status FROM agent_actions WHERE id=%s", (ids["action"],),
        ).fetchone()[0] == "cancelled"
