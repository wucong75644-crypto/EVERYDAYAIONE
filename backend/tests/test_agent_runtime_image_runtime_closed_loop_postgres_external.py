"""Current-version disposable Runtime image ActionLoop closure."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from core.db_scope import (
    AsyncScopedDatabaseClient,
    DatabaseAccessKind,
    DatabaseScope,
)
from core.local_db import AsyncLocalDBClient
from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.resolver import PostgresActionExecutorResolver
from services.agent.runtime.executors.specialist_contracts import (
    ProviderReceipt,
    ProviderState,
)
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
from services.agent.runtime.infrastructure.postgres.specialist_repository import (
    PostgresSpecialistRepository,
)
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from services.agent.runtime.executors.contracts import canonical_request_hash


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
CURRENT_DISPATCH_MIGRATIONS = (
    "227_01_agent_runtime_production_closure.sql",
    "227_06_agent_runtime_tenant_kill_control.sql",
    "227_07_agent_runtime_kill_epoch_fence.sql",
    "227_09_agent_runtime_claim_fence_ambiguity_fix.sql",
    "227_13_agent_runtime_additive_ingress_compatibility.sql",
    "227_14_agent_runtime_owner_transition.sql",
    "227_15_agent_runtime_owner_rpc_acl_closure.sql",
    "227_16_agent_runtime_safe_read_release.sql",
    "227_17_agent_runtime_safe_policy_activation.sql",
    "227_61_agent_runtime_web_ingress_required.sql",
    "228_08j_agent_runtime_web_scope_owner_atomicity.sql",
    "228_08k_agent_runtime_web_ingress_binding_terminal.sql",
    "228_08q_agent_runtime_single_owner_convergence.sql",
    "231_01_agent_runtime_admin_rollout_acl_closure.sql",
)


class _LocalImageProvider:
    calls = 0

    async def submit(self, attempt, request, *, idempotency_key):
        del request, idempotency_key
        self.calls += 1
        return ProviderReceipt(
            state=ProviderState.COMPLETED,
            provider="local-mock",
            request_hash=attempt.request_hash,
            result={"summary": "ok", "count": 1},
            cost={"credits": 2},
        )

    async def reconcile(self, attempt, receipt):
        del receipt
        return ProviderReceipt(
            state=ProviderState.COMPLETED,
            provider="local-mock",
            request_hash=attempt.request_hash,
            result={"summary": "ok"},
            cost={"credits": 2},
        )

    async def cancel(self, attempt, receipt):
        del receipt
        return ProviderReceipt(
            state=ProviderState.UNKNOWN,
            provider="local-mock",
            request_hash=attempt.request_hash,
            evidence={"error_code": "cancel_unproven"},
        )


def _install_current_dispatch_stack(url: str) -> None:
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, "
            "user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, "
            "purged BOOLEAN NOT NULL DEFAULT FALSE)"
        )
        connection.execute(
            "CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, "
            "user_id UUID, status TEXT NOT NULL DEFAULT 'active', "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())"
        )
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'web'"
        )
        connection.commit()
    for index in range(1, 20):
        _apply(url, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    for name in CURRENT_DISPATCH_MIGRATIONS:
        _apply(url, name)
    with psycopg.connect(url.replace("postgres@", "everydayai_runtime_admin@")) as connection:
        assert connection.execute(
            "SELECT has_function_privilege(current_user, "
            "'set_agent_runtime_org_rollout(uuid,uuid,boolean,text)', 'EXECUTE')"
        ).fetchone()[0] is True


def _seed_current_image_action(database: str) -> dict[str, str]:
    ids = {name: str(uuid4()) for name in ("session", "command", "run", "step", "action", "token", "policy")}
    org = "22222222-2222-2222-2222-222222222222"
    user = "44444444-4444-4444-4444-444444444444"
    conversation = "55555555-5555-5555-5555-555555555555"
    request_hash = canonical_request_hash({})
    arguments_hash = canonical_request_hash({})
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO agent_runtime_sessions(id,conversation_id,org_id,user_id,scope_kind,scope_id,"
            "created_by_user_id,agent_definition_id,agent_definition_revision) VALUES(%s,%s,%s,%s,'user',%s,%s,'fixture','v1')",
            (ids["session"], conversation, org, user, user, user),
        )
        connection.execute(
            "INSERT INTO agent_session_commands(id,session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash) "
            "VALUES(%s,%s,%s,%s,'submit_input',%s,'{}',%s)",
            (ids["command"], ids["session"], org, user, ids["command"], "b" * 32),
        )
        connection.execute(
            "INSERT INTO agent_runs(id,session_id,command_id,org_id,user_id,run_kind,idempotency_key,request_hash,status,"
            "execution_token,lease_expires_at,context_receipt,config_snapshot,capability_snapshot,blocking_action_count) "
            "VALUES(%s,%s,%s,%s,%s,'user',%s,%s,'running',%s,clock_timestamp()+interval '10 minutes','{}','{}','{}',1)",
            (ids["run"], ids["session"], ids["command"], org, user, ids["run"], "b" * 32, ids["token"]),
        )
        connection.execute(
            "INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,step_number,model_id,provider,"
            "model_revision,prompt_revision,tool_catalog_revision) VALUES(%s,%s,%s,%s,%s,1,'fixture','fixture','v1','v1','v1')",
            (ids["step"], ids["run"], ids["session"], org, user),
        )
        connection.execute(
            "INSERT INTO agent_actions(id,session_id,run_id,model_step_id,org_id,user_id,action_index,stable_tool_call_id,"
            "tool_name,arguments,arguments_hash,request_hash,batch_hash,policy_decision,policy_snapshot,policy_revision,"
            "retry_disposition,status) VALUES(%s,%s,%s,%s,%s,%s,0,%s,'generate_image','{}',%s,%s,%s,'preauthorized',%s,'v1',"
            "'retry_after_reconcile','queued')",
            (
                ids["action"], ids["session"], ids["run"], ids["step"], org, user, ids["action"],
                arguments_hash, request_hash, "c" * 64,
                Jsonb({"dispatch_policy_receipt_id": ids["policy"], "safety_level": "safe"}),
            ),
        )
        connection.execute(
            "INSERT INTO agent_policy_receipts(id,action_id,session_id,run_id,org_id,user_id,decision,arguments_hash,"
            "executor_type,executor_revision,policy_revision,effective_scope,reason_codes,receipt_hash,expires_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,'allow',%s,'runtime_media_generation:generate_image',1,'v1','{}',ARRAY['fixture'],%s,"
            "clock_timestamp()+interval '10 minutes')",
            (ids["policy"], ids["action"], ids["session"], ids["run"], org, user, arguments_hash, "d" * 64),
        )
        connection.execute(
            "INSERT INTO agent_runtime_org_rollout(org_id,enabled,updated_by,update_reason) VALUES(%s,TRUE,%s,'isolated image runtime e2e')",
            (org, user),
        )
        connection.execute(
            "UPDATE agent_runtime_control SET action_dispatch_enabled=TRUE,safe_actions_enabled=TRUE,"
            "release_revision='isolated-image-e2e',config_revision='isolated-image-e2e' WHERE singleton"
        )
        connection.commit()
    return {**ids, "request_hash": request_hash}


def test_current_image_runtime_actionloop_closes_with_local_provider(
    database: str,
) -> None:
    """Run image Action claim, dispatch, terminalization and settlement."""
    _install_current_dispatch_stack(database)
    ids = _seed_current_image_action(database)

    async def run_once() -> int:
        client = AsyncLocalDBClient(
            database.replace("postgres@", "everydayai_agent_runtime_worker@"),
            min_size=1,
            max_size=4,
        )
        await client.open()
        scoped = AsyncScopedDatabaseClient(
            client,
            DatabaseScope(
                actor_user_id="44444444-4444-4444-4444-444444444444",
                org_id="22222222-2222-2222-2222-222222222222",
                access_kind=DatabaseAccessKind.AGENT_RUNTIME,
                request_id="image-runtime-closed-loop",
            ),
        )
        try:
            provider = _LocalImageProvider()
            descriptor = specialist_descriptor("generate_image")
            registry = ExecutorRegistry()
            registry.register(
                descriptor,
                SpecialistExecutor(
                    executor_type=descriptor.executor_type,
                    revision=descriptor.revision,
                    provider=provider,
                ),
                safety_level="dangerous",
            )
            registry.specialist_facts = PostgresSpecialistRepository(scoped)
            driver = ActionLoopDriver(
                recovery_repository=PostgresCoordinatorRecoveryRepository(scoped),
                action_repository=PostgresActionRepository(scoped),
                authorization_repository=PostgresActionAuthorizationRepository(scoped),
                resolver=PostgresActionExecutorResolver(registry),
                worker_id="image-closed-loop",
                renew_interval=60,
            )
            assert await driver.dispatch_once() is True
            return provider.calls
        finally:
            await client.close()

    assert asyncio.run(run_once()) == 1
    with psycopg.connect(database) as connection:
        state = connection.execute(
            "SELECT action.status,attempt.status,result.status,run.blocking_action_count "
            "FROM agent_actions action "
            "JOIN agent_action_attempts attempt ON attempt.action_id=action.id "
            "LEFT JOIN agent_action_results result ON result.action_id=action.id "
            "JOIN agent_runs run ON run.id=action.run_id WHERE action.id=%s",
            (ids["action"],),
        ).fetchone()
        assert state == ("completed", "completed", "success", 0)
        assert connection.execute(
            "SELECT count(*) FROM agent_action_cost_settlements "
            "WHERE action_id=%s AND kind='settle'",
            (ids["action"],),
        ).fetchone()[0] == 1
