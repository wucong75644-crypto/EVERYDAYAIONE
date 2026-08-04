from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar173_postgres_external import _seed_specialist_action, _worker_rpc
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_tenant_kill_control_postgres_external import _set_gate


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ORG = "22222222-2222-2222-2222-222222222222"


def _apply(url: str, name: str) -> None:
    path = ROOT / "migrations" / name
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))


def test_c_provider_and_scheduler_facts_fail_closed_after_kill(database: str) -> None:
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_04_agent_runtime_provider_submission_facts.sql",
        "227_05_agent_runtime_scheduler_cas.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_08_agent_runtime_facts_recovery_fence.sql",
    ):
        _apply(database, name)
    ids = _seed_specialist_action(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO agent_runtime_owner_fences "
            "(owner_kind,owner_id,org_id,execution_token,tenant_kill_epoch,status) "
            "VALUES('attempt',%s,%s,%s,0,'active')",
            (ids["attempt"], ORG, ids["token"]),
        )
        conn.commit()
    created = _worker_rpc(database, "create_agent_runtime_provider_submission", (
        ids["attempt"], ids["action"], ids["run"], ORG,
        "44444444-4444-4444-4444-444444444444", "user",
        "44444444-4444-4444-4444-444444444444", ids["token"], ids["request_hash"],
        "mock-provider", "mock-v1", "facts-key",
    ))
    assert created["outcome"] == "created"
    _set_gate(database, str(uuid4()), "tenant", "tenant", True, 0)
    with pytest.raises(psycopg.Error, match="RUNTIME_KILL_EPOCH_FENCED"):
        _worker_rpc(database, "mutate_agent_runtime_scheduler_cas", (
            ids["attempt"], ids["action"], ids["run"], ORG,
            "44444444-4444-4444-4444-444444444444", "user",
            "44444444-4444-4444-4444-444444444444", "task-facts", 0,
            "create", {}, ids["request_hash"], ids["token"], "scheduler-facts-key",
        ))
    unknown = _worker_rpc(database, "record_agent_runtime_provider_unknown", (
        created["submission_id"], ids["token"], ids["request_hash"], 0,
        {"transport": "response_loss"},
    ))
    assert unknown["state"] == "unknown"


def test_c_apply_rollback_reapply_without_facts(database: str) -> None:
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_04_agent_runtime_provider_submission_facts.sql",
        "227_05_agent_runtime_scheduler_cas.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_08_agent_runtime_facts_recovery_fence.sql",
    ):
        _apply(database, name)
    rollback = ROOT / "migrations/rollback/227_08_agent_runtime_facts_recovery_fence_rollback.sql"
    _apply(database, rollback.relative_to(ROOT / "migrations").as_posix())
    _apply(database, "227_08_agent_runtime_facts_recovery_fence.sql")
    _apply(database, rollback.relative_to(ROOT / "migrations").as_posix())


def test_c_child_run_claim_is_fenced_after_tenant_kill(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("ALTER TABLE agent_runs ADD COLUMN parent_run_id UUID")
        conn.execute("ALTER TABLE agent_runs ADD COLUMN parent_action_id UUID")
        conn.execute("ALTER TABLE agent_runs ADD COLUMN child_ordinal INTEGER")
        conn.execute("ALTER TABLE agent_runs ADD COLUMN parent_request_hash TEXT")
        conn.commit()
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_04_agent_runtime_provider_submission_facts.sql",
        "227_05_agent_runtime_scheduler_cas.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_08_agent_runtime_facts_recovery_fence.sql",
    ):
        _apply(database, name)
    ids = _seed_specialist_action(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO agent_runtime_owner_fences "
            "(owner_kind,owner_id,org_id,execution_token,tenant_kill_epoch,status) "
            "VALUES('attempt',%s,%s,%s,0,'active')",
            (ids["attempt"], ORG, ids["token"]),
        )
        child_command = str(uuid4())
        child_run = str(uuid4())
        conn.execute(
            "INSERT INTO agent_session_commands "
            "(id,session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash) "
            "VALUES(%s,%s,%s,%s,'submit_input',%s,'{}',%s)",
            (child_command, ids["session"], ORG,
             "44444444-4444-4444-4444-444444444444", "child-facts", "e" * 32),
        )
        conn.execute(
            "INSERT INTO agent_runs "
            "(id,session_id,command_id,org_id,user_id,run_kind,status,idempotency_key,request_hash,parent_run_id,parent_action_id,child_ordinal,parent_request_hash) "
            "VALUES(%s,%s,%s,%s,%s,'continuation','queued',%s,%s,%s,%s,1,%s)",
            (child_run, ids["session"], child_command, ORG,
             "44444444-4444-4444-4444-444444444444", "child-facts-run", "f" * 32,
             ids["run"], ids["action"], ids["request_hash"]),
        )
        conn.commit()
    _set_gate(database, str(uuid4()), "tenant", "tenant", True, 0)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        with pytest.raises(psycopg.Error, match="RUNTIME_KILL_EPOCH_FENCED"):
            conn.execute(
                "UPDATE agent_runs SET status='running',execution_token=%s,"
                "lease_expires_at=clock_timestamp()+interval '1 minute' WHERE id=%s",
                (str(uuid4()), child_run),
            )
