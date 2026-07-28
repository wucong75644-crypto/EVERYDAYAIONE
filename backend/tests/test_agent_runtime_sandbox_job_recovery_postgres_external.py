"""Real PostgreSQL contracts for 222_03 Sandbox recovery RPCs."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from tests import test_agent_runtime_sandbox_job_postgres_external as base


pytestmark = pytest.mark.external
MIGRATION_ROOT = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture(scope="module", autouse=True)
def dedicated_database() -> None:
    if os.getenv("RUN_AR222_DB_TEST") != "1" or not base.DATABASE_URL:
        pytest.skip("RUN_AR222_DB_TEST=1 and AR222_TEST_DATABASE_URL required")
    if "ar222" not in base.DATABASE_URL.lower():
        pytest.skip("dedicated AR222 database name required")


def _readback(ids: dict[str, object], **changes: object) -> dict:
    values = {
        "key": ids["external_key"],
        "action": ids["action"],
        "attempt": ids["attempt"],
        "intent": ids["intent"],
        "request_hash": "b" * 64,
        "org": None,
        "user": ids["user"],
        "session": ids["session"],
        "run": ids["run"],
        "executor_type": "sandbox.python",
        "executor_revision": 1,
        "runtime_revision": "python-v1",
    }
    values.update(changes)
    row = base._execute(
        """
        SELECT get_sandbox_job_by_binding(
          %(key)s,%(action)s,%(attempt)s,%(intent)s,%(request_hash)s,
          %(org)s,%(user)s,%(session)s,%(run)s,%(executor_type)s,
          %(executor_revision)s,%(runtime_revision)s
        ) AS value
        """,
        values, role="everydayai_runtime", user_id=str(ids["user"]),
    )[0]["value"]
    return base._decoded(row)


def test_response_loss_unknown_attempt_reads_back_exact_job() -> None:
    ids = base._seed_dispatch()
    created = base._create(ids)["job"]
    base._execute(
        """
        SET ROLE everydayai_owner;
        UPDATE agent_action_attempts
           SET status='unknown',
               ambiguity_evidence='{"kind":"SANDBOX_SUBMIT_RESULT_UNKNOWN"}';
        RESET ROLE
        """
    )
    found = _readback(ids)
    assert found["outcome"] == "found"
    assert found["job"]["id"] == created["id"]
    assert found["job"]["external_idempotency_key"] == ids["external_key"]


def test_readback_wrong_key_missing_and_every_wrong_binding_conflicts() -> None:
    ids = base._seed_dispatch()
    base._create(ids)
    assert _readback(ids, key="action:missing:key")["outcome"] == "not_found"
    wrong_uuid = "99999999-9999-9999-9999-999999999999"
    changes = (
        {"action": wrong_uuid}, {"attempt": wrong_uuid},
        {"intent": wrong_uuid}, {"request_hash": "9" * 64},
        {"org": wrong_uuid}, {"user": wrong_uuid},
        {"session": wrong_uuid}, {"run": wrong_uuid},
        {"executor_type": "other"}, {"executor_revision": 2},
        {"runtime_revision": "python-v2"},
    )
    for change in changes:
        assert _readback(ids, **change)["outcome"] == "idempotency_conflict"


def test_50_concurrent_readbacks_return_same_job() -> None:
    ids = base._seed_dispatch()
    expected = base._create(ids)["job"]["id"]
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: _readback(ids), range(50)))
    assert {result["outcome"] for result in results} == {"found"}
    assert {result["job"]["id"] for result in results} == {expected}


def _expire(job_id: str) -> None:
    base._execute(
        """
        SET ROLE everydayai_owner;
        UPDATE agent_sandbox_jobs
           SET lease_expires_at=clock_timestamp()-interval '1 second'
         WHERE id=%s;
        RESET ROLE
        """,
        (job_id,),
    )


def _claim_recoverable(worker: str) -> dict:
    return base._worker_rpc(
        "SELECT claim_next_recoverable_sandbox_job(%s,60) AS value",
        (worker,),
    )


def _claim_reconciliation(worker: str) -> dict:
    return base._worker_rpc(
        "SELECT claim_next_sandbox_job_reconciliation(%s,60) AS value",
        (worker,),
    )


def _execute_script(sql: str) -> None:
    with psycopg.connect(base.DATABASE_URL) as connection:
        connection.execute(sql)


def test_expired_unstarted_claim_has_one_new_execution_owner() -> None:
    ids = base._seed_dispatch()
    job = base._create(ids)["job"]
    old = base._claim()
    _expire(job["id"])
    with ThreadPoolExecutor(max_workers=10) as pool:
        outcomes = list(pool.map(
            lambda index: _claim_recoverable(f"recovery-{index}"),
            range(50),
        ))
    claimed = [item for item in outcomes if item["outcome"] == "claimed"]
    assert len(claimed) == 1
    current = claimed[0]["job"]
    assert current["fencing_token"] == old["fencing_token"] + 1
    assert current["claim_token"] != old["claim_token"]
    stale = base._worker_rpc(
        "SELECT renew_sandbox_job_lease(%s,%s,%s,%s,60) AS value",
        (
            job["id"], old["claim_token"], old["fencing_token"],
            old["state_version"],
        ),
    )
    assert stale["outcome"] == "ownership_lost"
    leaked = base._worker_rpc(
        "SELECT get_owned_sandbox_job(%s,%s,%s,%s) AS value",
        (
            job["id"], "sandbox-1", old["claim_token"],
            old["fencing_token"],
        ),
    )
    assert leaked["outcome"] == "ownership_lost"


@pytest.mark.parametrize("phase", ["starting", "running"])
def test_started_phases_only_enter_reconciliation(phase: str) -> None:
    ids = base._seed_dispatch()
    job = base._create(ids)["job"]
    claimed = base._claim()
    started_result = base._worker_rpc(
        "SELECT mark_sandbox_job_started(%s,%s,%s,%s,%s) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"], "starting",
        ),
    )
    started = started_result["job"]
    if phase == "running":
        started = base._worker_rpc(
            "SELECT mark_sandbox_job_started(%s,%s,%s,%s,%s) AS value",
            (
                job["id"], claimed["claim_token"],
                claimed["fencing_token"], started["state_version"], "running",
            ),
        )["job"]
    _expire(job["id"])
    assert _claim_recoverable("execution-recovery")["outcome"] == "not_found"
    reconciled = _claim_reconciliation("reconciler")
    assert reconciled["outcome"] == "claimed"
    assert reconciled["job"]["status"] == "unknown"
    assert reconciled["job"]["reconciliation_token"]
    assert reconciled["job"]["claim_token"] is None
    assert started["fencing_token"] == reconciled["job"]["fencing_token"]


def test_unknown_and_cancel_requested_are_reconciliation_only() -> None:
    ids = base._seed_dispatch()
    job = base._create(ids)["job"]
    claimed = base._claim()
    unknown = base._worker_rpc(
        """
        SELECT record_sandbox_job_unknown(
          %s,%s,%s,%s,'{"kind":"EXECUTION_UNPROVEN"}',
          '{"schema_revision":1,"items":[]}',NULL
        ) AS value
        """,
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"],
        ),
    )
    assert unknown["outcome"] == "unknown"
    assert _claim_recoverable("execution-recovery")["outcome"] == "not_found"
    assert _claim_reconciliation("reconciler")["outcome"] == "claimed"

    ids = base._seed_dispatch()
    job = base._create(ids)["job"]
    claimed = base._claim()
    cancelled = base._decoded(base._execute(
        "SELECT request_sandbox_job_cancel(%s,%s) AS value",
        (job["id"], claimed["state_version"]),
        role="everydayai_runtime", user_id=str(ids["user"]),
    )[0]["value"])
    assert cancelled["outcome"] == "cancel_requested"
    _expire(job["id"])
    assert _claim_recoverable("execution-recovery")["outcome"] == "not_found"
    assert _claim_reconciliation("reconciler")["outcome"] == "claimed"


def test_reconciler_freezes_checkpoint_partials_before_cleanup() -> None:
    ids = base._seed_dispatch()
    job = base._create(ids)["job"]
    claimed = base._claim()
    started = base._worker_rpc(
        "SELECT mark_sandbox_job_started(%s,%s,%s,%s,'starting') AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"],
        ),
    )["job"]
    _expire(job["id"])
    reconciled = _claim_reconciliation("reconciler")["job"]
    assert reconciled["status"] == "unknown"
    assert reconciled["partial_effects"]["items"] == []
    partials = base._receipt(partial=True)["partial_effects"]
    recorded = base._worker_rpc(
        "SELECT record_reconciled_sandbox_partials(%s,%s,%s,%s) AS value",
        (
            job["id"], reconciled["reconciliation_token"],
            reconciled["state_version"], json.dumps(partials),
        ),
    )
    assert recorded["outcome"] == "partials_recorded"
    assert recorded["job"]["partial_effects"] == partials
    recorded_at = datetime.fromisoformat(
        recorded["job"]["partial_effects_recorded_at"],
    )
    deadline = datetime.fromisoformat(recorded["job"]["cleanup_deadline_at"])
    assert deadline == recorded_at + timedelta(hours=24)
    duplicate = base._worker_rpc(
        "SELECT record_reconciled_sandbox_partials(%s,%s,%s,%s) AS value",
        (
            job["id"], reconciled["reconciliation_token"],
            recorded["job"]["state_version"], json.dumps(partials),
        ),
    )
    assert duplicate["outcome"] == "already_partials_recorded"
    assert started["fencing_token"] == recorded["job"]["fencing_token"]


def test_expired_reconciliation_fences_the_old_owner() -> None:
    ids = base._seed_dispatch()
    job = base._create(ids)["job"]
    claimed = base._claim()
    unknown = base._worker_rpc(
        """
        SELECT record_sandbox_job_unknown(
          %s,%s,%s,%s,'{"kind":"EXECUTION_UNPROVEN"}',
          '{"schema_revision":1,"items":[]}',NULL
        ) AS value
        """,
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"],
        ),
    )
    old = _claim_reconciliation("reconciler-old")["job"]
    base._execute(
        """
        SET ROLE everydayai_owner;
        UPDATE agent_sandbox_jobs
           SET reconciliation_lease_expires_at =
               clock_timestamp() - interval '1 second'
         WHERE id=%s;
        RESET ROLE
        """,
        (job["id"],),
    )
    new = _claim_reconciliation("reconciler-new")["job"]
    assert new["reconciliation_token"] != old["reconciliation_token"]
    stale = base._worker_rpc(
        """
        SELECT record_sandbox_job_cleanup(
          %s,%s,%s,'completed','{"kind":"CLEANUP_CONFIRMED"}'
        ) AS value
        """,
        (job["id"], old["reconciliation_token"], old["state_version"]),
    )
    assert stale["outcome"] == "ownership_lost"
    assert unknown["job"]["id"] == new["id"]


def test_terminal_jobs_and_unprivileged_roles_cannot_claim() -> None:
    ids = base._seed_dispatch()
    job = base._create(ids)["job"]
    cancelled = base._decoded(base._execute(
        "SELECT request_sandbox_job_cancel(%s,%s) AS value",
        (job["id"], job["state_version"]),
        role="everydayai_runtime", user_id=str(ids["user"]),
    )[0]["value"])
    assert cancelled["outcome"] == "cancelled"
    assert _claim_recoverable("execution-recovery")["outcome"] == "not_found"
    assert _claim_reconciliation("reconciler")["outcome"] == "not_found"

    rows = base._execute(
        """
        SELECT
          has_function_privilege(
            'everydayai_runtime',
            'get_sandbox_job_by_binding(text,uuid,uuid,uuid,text,uuid,uuid,uuid,uuid,text,integer,text)',
            'EXECUTE') AS runtime_readback,
          has_function_privilege(
            'everydayai_sandbox_worker',
            'claim_next_recoverable_sandbox_job(text,integer)',
            'EXECUTE') AS sandbox_recovery,
          has_function_privilege(
            'everydayai_worker',
            'claim_next_recoverable_sandbox_job(text,integer)',
            'EXECUTE') AS worker_recovery,
          has_function_privilege(
            'everydayai_sandbox_worker',
            'get_owned_sandbox_job(uuid,text,uuid,bigint)',
            'EXECUTE') AS sandbox_owned_read,
          has_function_privilege(
            'everydayai_sandbox_worker',
            'get_sandbox_job(uuid)',
            'EXECUTE') AS sandbox_unbound_read,
          NOT EXISTS (
            SELECT 1
              FROM pg_proc p,
                   LATERAL aclexplode(COALESCE(
                     p.proacl, acldefault('f', p.proowner)
                   )) acl
             WHERE p.oid =
               'claim_next_sandbox_job_reconciliation(text,integer)'::regprocedure
               AND acl.grantee = 0
               AND acl.privilege_type = 'EXECUTE'
          ) AS public_reconcile_denied
        """
    )[0]
    assert rows == {
        "runtime_readback": True, "sandbox_recovery": True,
        "worker_recovery": False, "sandbox_owned_read": True,
        "sandbox_unbound_read": False, "public_reconcile_denied": True,
    }


def test_rollback_guards_active_jobs_and_terminal_history_remains_readable() -> None:
    rollback = (
        MIGRATION_ROOT / "rollback"
        / "222_03_agent_runtime_sandbox_job_recovery_rpcs_rollback.sql"
    ).read_text()
    migration = (
        MIGRATION_ROOT / "222_03_agent_runtime_sandbox_job_recovery_rpcs.sql"
    ).read_text()
    ids = base._seed_dispatch()
    job = base._create(ids)["job"]
    with pytest.raises(
        psycopg.Error,
        match="AGENT_SANDBOX_RECOVERY_ROLLBACK_HAS_ACTIVE_JOBS",
    ):
        _execute_script(rollback)
    cancelled = base._decoded(base._execute(
        "SELECT request_sandbox_job_cancel(%s,%s) AS value",
        (job["id"], job["state_version"]),
        role="everydayai_runtime", user_id=str(ids["user"]),
    )[0]["value"])
    assert cancelled["outcome"] == "cancelled"

    _execute_script(rollback)
    terminal = base._decoded(base._execute(
        "SELECT get_sandbox_job(%s) AS value",
        (job["id"],), role="everydayai_runtime",
        user_id=str(ids["user"]),
    )[0]["value"])
    assert terminal["job"]["status"] == "cancelled"
    assert base._execute(
        """
        SELECT to_regprocedure(
          'get_sandbox_job_by_binding(text,uuid,uuid,uuid,text,uuid,uuid,uuid,uuid,text,integer,text)'
        ) IS NULL AS removed
        """
    )[0]["removed"] is True
    _execute_script(migration)
    assert _readback(ids)["job"]["id"] == job["id"]
