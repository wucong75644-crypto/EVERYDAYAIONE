from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import (
    _apply, _seed_specialist_action, _worker_rpc,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "227_24_agent_runtime_provider_cancel_handoff.sql"
ROLLBACK = "rollback/227_24_agent_runtime_provider_cancel_handoff_rollback.sql"


def _prepare(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("DO $$ BEGIN IF to_regrole('everydayai_agent_model_gateway') IS NULL THEN CREATE ROLE everydayai_agent_model_gateway LOGIN; END IF; END $$")
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY,org_id UUID,user_id UUID,relative_path TEXT NOT NULL,oss_object_key TEXT NOT NULL,purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY,org_id UUID,user_id UUID,status TEXT NOT NULL DEFAULT 'active',updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    for migration in (
        "227_01_agent_runtime_production_closure.sql",
        "227_04_agent_runtime_provider_submission_facts.sql",
        "227_05_agent_runtime_scheduler_cas.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_08_agent_runtime_facts_recovery_fence.sql",
        MIGRATION,
    ):
        _apply(database, migration)


def _cancelled_provider_fact(database: str) -> tuple[dict[str, str], str]:
    ids = _seed_specialist_action(database)
    submission_id = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_actions SET status='accepted',accepted_at=clock_timestamp(),policy_snapshot=%s WHERE id=%s",
                     (Jsonb({"provider": "mock", "provider_revision": "provider-v1"}), ids["action"]))
        conn.execute("UPDATE agent_action_attempts SET status='accepted',dispatch_phase='accepted',accepted_at=clock_timestamp(),external_receipt=%s,next_reconcile_at=clock_timestamp(),state_version=1 WHERE id=%s", (Jsonb({"provider": "mock", "provider_task_ref": "task-1"}), ids["attempt"]))
        conn.execute("UPDATE agent_runs SET status='cancelled',blocking_action_count=0,execution_token=NULL,lease_expires_at=NULL,completed_at=clock_timestamp(),state_version=2 WHERE id=%s", (ids["run"],))
        conn.execute("INSERT INTO agent_runtime_owner_fences(owner_kind,owner_id,org_id,execution_token,tenant_kill_epoch,provider_revision,state_version,status) VALUES('attempt',%s,%s,%s,0,'provider-v1',1,'active')", (ids["attempt"], "22222222-2222-2222-2222-222222222222", ids["token"]))
        conn.execute("INSERT INTO agent_runtime_provider_submission_facts(id,attempt_id,action_id,run_id,org_id,user_id,scope_kind,scope_id,provider,provider_revision,external_idempotency_key,request_hash,execution_token,state,cancel_requested_at,cancel_confirmed_at,state_version) VALUES(%s,%s,%s,%s,%s,%s,'user',%s,'mock','provider-v1',%s,%s,%s,'cancelled',clock_timestamp(),clock_timestamp(),3)",
                     (submission_id,ids["attempt"],ids["action"],ids["run"],"22222222-2222-2222-2222-222222222222","44444444-4444-4444-4444-444444444444","44444444-4444-4444-4444-444444444444",ids["attempt"],ids["request_hash"],ids["token"]))
        conn.commit()
    return ids, submission_id


def _expired_dispatching(
    database: str, *, cancelled: bool = False, without_intent: bool = False,
) -> dict[str, str]:
    conversation_id = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) VALUES(%s,%s,%s,'user',%s)",
                     (conversation_id,"44444444-4444-4444-4444-444444444444","22222222-2222-2222-2222-222222222222","44444444-4444-4444-4444-444444444444"))
        conn.commit()
    ids = _seed_specialist_action(database, conversation_id)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_policy_receipts SET receipt_hash=%s WHERE id=%s",
                     (uuid4().hex * 2, ids["policy"]))
        conn.execute("UPDATE agent_action_attempts SET lease_expires_at=clock_timestamp()-interval '1 second',updated_at=clock_timestamp()-interval '2 minutes' WHERE id=%s", (ids["attempt"],))
        if cancelled:
            conn.execute("UPDATE agent_runs SET status='cancelled',blocking_action_count=0,execution_token=NULL,lease_expires_at=NULL,completed_at=clock_timestamp(),state_version=state_version+1 WHERE id=%s", (ids["run"],))
        if without_intent:
            conn.execute("DELETE FROM agent_action_dispatch_intents WHERE attempt_id=%s", (ids["attempt"],))
        conn.commit()
    return ids


def test_b3_cancel_claim_finalizer_acl_and_rollback(database: str) -> None:
    _prepare(database)
    ids, submission_id = _cancelled_provider_fact(database)
    claim = _worker_rpc(database, "claim_next_agent_action_reconciliation", ("b3-worker", 120, 0))
    assert claim["operation"] == "cancel"
    assert claim["parent_run_id"] == ids["run"]
    assert claim["parent_run_status"] == "cancelled"
    readback = _worker_rpc(database, "get_claimed_agent_action_reconciliation", ("b3-worker",))
    assert readback["operation"] == claim["operation"]
    assert readback["parent_run_state_version"] == claim["parent_run_state_version"]
    receipt = {"state": "cancelled", "evidence": {"cancel_confirmed": True,
               "submission_id": submission_id, "state_version": 3}}
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_action_attempts SET reconciliation_lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (ids["attempt"],))
        conn.commit()
    with pytest.raises(Exception, match="AGENT_FINALIZE_FENCED"):
        _worker_rpc(database, "finalize_agent_action_provider_v2", (
            ids["attempt"], None, claim["execution_token"], claim["state_version"],
            ids["request_hash"], "cancelled", receipt, {}, "refund", 7, 0,
            "credits", "runtime", "f" * 64,
        ))
    claim = _worker_rpc(database, "claim_next_agent_action_reconciliation", ("b3-worker-2", 120, 0))
    assert claim["operation"] == "cancel"
    finalized = _worker_rpc(database, "finalize_agent_action_provider_v2", (
        ids["attempt"], None, claim["execution_token"], claim["state_version"],
        ids["request_hash"], "cancelled", receipt,
        {"status": "empty", "summary": "cancelled", "data": {}, "artifact_ids": [],
         "usage": {}, "cost": {}, "external_receipt": receipt},
        "refund", 7, 0, "credits", "runtime", "f" * 64,
    ))
    assert finalized["outcome"] == "cancelled"
    assert finalized["blocking_action_count"] == 0
    with psycopg.connect(database) as conn:
        row = conn.execute("SELECT a.status,t.status,r.status,r.blocking_action_count FROM agent_actions a JOIN agent_action_attempts t ON t.id=%s JOIN agent_runs r ON r.id=a.run_id WHERE a.id=%s", (ids["attempt"],ids["action"])).fetchone()
        assert row == ("cancelled", "cancelled", "cancelled", 0)
        assert conn.execute("SELECT count(*) FROM agent_action_cost_settlements WHERE attempt_id=%s AND kind='refund'", (ids["attempt"],)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM agent_runtime_events WHERE correlation_id=%s AND event_type='action.cancelled'", (ids["action"],)).fetchone()[0] == 1
        assert conn.execute("SELECT has_function_privilege('everydayai_worker','request_agent_runtime_provider_cancel(uuid,uuid,text,bigint,text)','EXECUTE')").fetchone()[0] is False
        assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker','request_agent_runtime_provider_cancel(uuid,uuid,text,bigint,text)','EXECUTE')").fetchone()[0] is True
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_actions SET status='unknown',completed_at=NULL WHERE id=%s", (ids["action"],))
        conn.execute("UPDATE agent_action_attempts SET status='unknown',ambiguity_evidence=%s,reconciliation_token=NULL,reconciliation_lease_expires_at=NULL,next_reconcile_at=clock_timestamp(),ended_at=NULL WHERE id=%s", (Jsonb({"error_code": "cancel_unknown"}), ids["attempt"]))
        conn.execute("UPDATE agent_runtime_provider_submission_facts SET state='cancel_requested',cancel_confirmed_at=NULL WHERE id=%s", (submission_id,))
        conn.commit()
    unknown_claim = _worker_rpc(database, "claim_next_agent_action_reconciliation", ("b3-unknown", 120, 0))
    assert unknown_claim["operation"] == "cancel"
    with pytest.raises(Exception, match="ROLLBACK_PENDING_FACTS"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_runs SET status='running',execution_token=%s,lease_expires_at=clock_timestamp()+interval '10 minutes',completed_at=NULL,terminal_reason=NULL WHERE id=%s", (ids["token"], ids["run"]))
        conn.commit()
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT has_function_privilege('everydayai_worker','request_agent_runtime_provider_cancel(uuid,uuid,text,bigint,text)','EXECUTE')").fetchone()[0] is True
        restored = conn.execute("SELECT pg_get_functiondef('claim_next_agent_action_reconciliation(text,integer,integer)'::regprocedure)").fetchone()[0]
        assert "dispatch_intent_outcome_unproven" in restored
        assert "reconciliation_operation" not in restored
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)


def test_b3_preserves_22025_dispatching_recovery_and_fencing(database: str) -> None:
    _prepare(database)
    running = _expired_dispatching(database)
    running_claim = _worker_rpc(
        database, "claim_next_agent_action_reconciliation", ("b3-running",120,0),
    )
    assert running_claim["operation"] == "reconcile"
    assert running_claim["snapshot"]["status"] == "unknown"
    assert running_claim["snapshot"]["retry_disposition"] == "retry_after_reconcile"

    cancelled = _expired_dispatching(database, cancelled=True)
    cancelled_claim = _worker_rpc(
        database, "claim_next_agent_action_reconciliation", ("b3-cancelled",120,0),
    )
    assert cancelled_claim["operation"] == "cancel"
    assert cancelled_claim["snapshot"]["status"] == "unknown"

    no_intent = _expired_dispatching(database, without_intent=True)
    assert _worker_rpc(
        database, "claim_next_agent_action_reconciliation", ("b3-no-intent",120,0),
    )["outcome"] == "not_found"
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT status FROM agent_action_attempts WHERE id=%s", (no_intent["attempt"],),
        ).fetchone()[0] == "dispatching"

    raced = _expired_dispatching(database)
    barrier = Barrier(2)

    def claim(worker: str):
        barrier.wait()
        return _worker_rpc(
            database, "claim_next_agent_action_reconciliation", (worker,120,0),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, ("b3-race-a", "b3-race-b")))
    assert sorted(row["outcome"] for row in outcomes) == ["claimed", "not_found"]
    claimed = next(row for row in outcomes if row["outcome"] == "claimed")
    with psycopg.connect(database) as conn:
        row = conn.execute(
            "SELECT status,reconciliation_token,state_version FROM agent_action_attempts WHERE id=%s",
            (raced["attempt"],),
        ).fetchone()
        assert (row[0], str(row[1]), row[2]) == (
            "unknown", claimed["execution_token"], claimed["state_version"],
        )
