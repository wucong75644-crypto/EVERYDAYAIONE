"""Real migration/locking tests in a disposable socket-only PostgreSQL cluster."""
import getpass
import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_scheduled_task_draft_delete_integration import postgres_socket

MIGRATIONS = Path(__file__).parents[1] / "migrations"


@pytest.fixture
def database(postgres_socket):
    name = "scheduled_upgrade_" + uuid4().hex
    from psycopg import sql
    with psycopg.connect(host=postgres_socket, dbname="postgres", user=getpass.getuser(), autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname='everydayai'").fetchone():
            admin.execute("CREATE ROLE everydayai")
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with psycopg.connect(host=postgres_socket, dbname=name, user=getpass.getuser()) as conn:
        conn.execute("CREATE TABLE organizations(id uuid PRIMARY KEY); CREATE TABLE users(id uuid PRIMARY KEY)")
        conn.execute((MIGRATIONS / "248_change_sets.sql").read_text())
        conn.execute((MIGRATIONS / "254_scheduled_task_direct_submission.sql").read_text())
        try:
            yield conn
        finally:
            conn.rollback()
    with psycopg.connect(host=postgres_socket, dbname="postgres", user=getpass.getuser(), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def seed_change(conn, *, override=None, bad_check=None):
    org, actor, change = uuid4(), str(uuid4()), uuid4()
    policy = {"risk_level": "medium", "requires_approval": False, "submission": {
        "version": "scheduled_task.submit.v1", "mode": "apply_if_allowed", "actor_id": actor}}
    if override: policy.update(override)
    conn.execute("INSERT INTO organizations VALUES (%s)", (org,))
    conn.execute("""INSERT INTO change_sets(id,org_id,resource_type,resource_id,operation,base_revision,
                 policy_snapshot,status,idempotency_key,created_by)
                 VALUES (%s,%s,'scheduled_task','task','pause','0',%s,'preflighting','key',%s)""",
                 (change, org, json.dumps(policy), actor))
    for check in ("authorization", "validation", "preflight"):
        conn.execute("""INSERT INTO change_checks(change_set_id,org_id,check_type,check_key,status)
                     VALUES (%s,%s,%s,%s,%s)""", (change, org, check, check, "skipped" if check == bad_check else "passed"))
    def transition(**kw):
        return conn.execute("SELECT transition_change_set(%s,%s,'preflighting','committing',%s,'user','request_accepted','{}')",
                            (change, kw.get("org", org), kw.get("actor", actor))).fetchone()[0]
    return change, transition


@pytest.mark.parametrize("bad", ["approval", "high", "missing_submission", "authorization", "validation", "actor", "org", None])
def test_database_enforces_direct_submission_evidence(database, bad):
    conn = database
    override = {"approval": {"requires_approval": True}, "high": {"risk_level": "high"}, "missing_submission": {"submission": {}}}.get(bad)
    change, transition = seed_change(conn, override=override, bad_check=bad)
    kw = {bad: str(uuid4())} if bad in {"actor", "org"} else {}
    result = transition(**kw)
    assert result["outcome"] == ("transitioned" if bad is None else "missing" if bad == "org" else "invalid_transition")
    assert conn.execute("SELECT count(*) FROM change_events WHERE event_type='confirmed'").fetchone()[0] == 0
    if bad is None:
        assert transition()["outcome"] == "state_conflict"


def test_migration_reapply_and_rollback_preserve_old_confirmation(database):
    conn = database
    conn.execute((MIGRATIONS / "254_scheduled_task_direct_submission.sql").read_text())
    change, transition = seed_change(conn)
    conn.execute((MIGRATIONS / "rollback/254_scheduled_task_direct_submission_rollback.sql").read_text())
    assert transition()["outcome"] == "invalid_transition"
    row = conn.execute("SELECT org_id,created_by FROM change_sets WHERE id=%s", (change,)).fetchone()
    conn.execute("UPDATE change_sets SET status='awaiting_approval' WHERE id=%s", (change,))
    assert conn.execute("SELECT transition_change_set(%s,%s,'awaiting_approval','committing',%s,'user','confirmed','{}')",
                        (change, *row)).fetchone()[0]["outcome"] == "transitioned"


@pytest.fixture
def lifecycle(database):
    conn = database
    for filename in ("069_scheduled_tasks.sql", "071_scheduled_task_schedule_type.sql",
                     "242_scheduled_task_delivery_outbox.sql", "244_scheduled_task_preflight_workflow.sql",
                     "245_scheduled_task_lifecycle_integrity.sql", "249_scheduled_task_changeset_adapter.sql",
                     "255_scheduled_task_schedule_intent.sql"):
        conn.execute((MIGRATIONS / filename).read_text())
    org, user, task = uuid4(), uuid4(), uuid4()
    conn.execute("INSERT INTO organizations VALUES (%s)", (org,))
    conn.execute("INSERT INTO users VALUES (%s)", (user,))
    conn.execute("""INSERT INTO scheduled_tasks(id,org_id,user_id,name,prompt,cron_expr,push_target,schedule_type,next_run_at,execution_policy)
                 VALUES (%s,%s,%s,'test','read synthetic orders','0 9 * * *','{}','daily',NOW()-INTERVAL '1 minute','{"version":1,"allowed_tools":["erp_agent"]}')""", (task, org, user))
    return conn, org, user, task


def taskrow(conn, task):
    return conn.execute("SELECT to_jsonb(t) FROM scheduled_tasks t WHERE id=%s", (task,)).fetchone()[0]


def claim(conn, task, org, *, manual=False):
    if manual:
        return conn.execute("SELECT claim_scheduled_task_now(%s,%s)", (task,org)).fetchone()[0]["task"]
    return conn.execute("SELECT to_jsonb(t) FROM claim_due_tasks(NOW(),10) t").fetchone()[0]


def start(conn, task, org, token):
    return conn.execute("SELECT start_scheduled_task_run(%s,%s,%s)", (task,org,token)).fetchone()[0]


def manage(conn, org, user, task, action, revision=None):
    revision = taskrow(conn,task)["revision"] if revision is None else revision
    return conn.execute("SELECT commit_scheduled_task_changeset(%s,%s,%s,%s,%s,%s,%s,%s)",
                        (uuid4(),org,user,task,action,revision,json.dumps({"next_run_at":"2030-01-01T01:00:00+00:00"}),str(uuid4()))).fetchone()[0]


def finish(conn, task, org, token, *, success=False, stale=False):
    if success:
        return conn.execute("SELECT complete_scheduled_task_success(%s,%s,'active',NOW()+INTERVAL '1 day','ok','{}',0,0,1,'[]')", (task,token)).fetchone()[0]
    return conn.execute("SELECT finish_scheduled_task_failure(%s,%s,%s,%s,'test failure',0,1,%s)",
                        (task,org,token,json.dumps({"status":"active","next_run_at":"2030-01-01T01:00:00+00:00","consecutive_failures":1}),
                         "2030-01-01T00:00:00+00:00" if stale else None)).fetchone()[0]


def test_pause_between_claim_and_start_blocks_work(lifecycle):
    conn,org,user,task = lifecycle
    claimed = claim(conn,task,org)
    assert claimed["revision"] == 0
    assert manage(conn,org,user,task,"pause",revision=0)["outcome"] == "paused"
    assert start(conn,task,org,claimed["run_token"])["outcome"] == "paused_before_start"
    assert conn.execute("SELECT count(*) FROM scheduled_task_runs").fetchone()[0] == 0
    assert taskrow(conn,task)["status"] == "paused"


@pytest.mark.parametrize("success,stale", [(True,False),(False,False),(False,True)])
def test_running_pause_survives_success_failure_and_recovery(lifecycle,success,stale):
    conn,org,user,task = lifecycle
    claimed = claim(conn,task,org)
    assert start(conn,task,org,claimed["run_token"])["outcome"] == "started"
    assert start(conn,task,org,claimed["run_token"])["outcome"] == "already_started"
    assert manage(conn,org,user,task,"pause")["outcome"] == "paused"
    paused = taskrow(conn,task)
    assert paused["status"] == "running" and not paused["schedule_enabled"]
    assert start(conn,task,org,claimed["run_token"])["outcome"] == "already_started"
    assert taskrow(conn,task)["run_token"] == claimed["run_token"]
    finish(conn,task,org,claimed["run_token"],success=success,stale=stale)
    final = taskrow(conn,task)
    assert final["status"] == "paused" and final["next_run_at"] is None and final["run_token"] is None
    assert conn.execute("SELECT count(*) FROM claim_due_tasks(NOW()+INTERVAL '1 year',10)").fetchone()[0] == 0


def test_resume_running_keeps_claim_and_old_worker_cannot_finish_new_run(lifecycle):
    conn,org,user,task = lifecycle
    claimed = claim(conn,task,org)
    token = claimed["run_token"]
    start(conn,task,org,token)
    manage(conn,org,user,task,"pause")
    manage(conn,org,user,task,"resume")
    assert taskrow(conn,task)["run_token"] == token
    assert conn.execute("SELECT claim_scheduled_task_now(%s,%s)",(task,org)).fetchone()[0]["outcome"] == "already_running"
    finish(conn,task,org,token,stale=True)
    newer = claim(conn,task,org,manual=True)
    start(conn,task,org,newer["run_token"])
    assert finish(conn,task,org,token)["outcome"] == "claim_lost"
    assert finish(conn,task,org,token,success=True)["outcome"] == "run_not_running"
    assert taskrow(conn,task)["run_token"] == newer["run_token"]


@pytest.mark.parametrize("success", [True,False])
def test_manual_run_does_not_resume_paused_task(lifecycle,success):
    conn,org,user,task = lifecycle
    manage(conn,org,user,task,"pause")
    claimed = claim(conn,task,org,manual=True)
    assert not claimed["schedule_enabled"] and claimed["run_manual"]
    assert start(conn,task,org,claimed["run_token"])["outcome"] == "started"
    finish(conn,task,org,claimed["run_token"],success=success)
    assert taskrow(conn,task)["status"] == "paused"


def test_concurrent_claims_start_once_and_pause_wins_when_locked_first(lifecycle):
    from concurrent.futures import ThreadPoolExecutor
    conn,org,user,task = lifecycle
    conn.commit()
    def manual():
        with psycopg.connect(conn.info.dsn) as worker:
            return worker.execute("SELECT claim_scheduled_task_now(%s,%s)",(task,org)).fetchone()[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: manual(), range(2)))
    assert sorted(x["outcome"] for x in results) == ["already_running","claimed"]
    token = taskrow(conn,task)["run_token"]
    manage(conn,org,user,task,"pause")  # hold row lock; starter must wait for commit
    with ThreadPoolExecutor(max_workers=1) as pool:
        def attempt_start():
            with psycopg.connect(conn.info.dsn) as worker:
                return start(worker,task,org,token)
        pending = pool.submit(attempt_start)
        conn.commit()
        assert pending.result(timeout=5)["outcome"] == "paused_before_start"


def test_lifecycle_migration_reapply_and_rollback_guards(lifecycle):
    conn,org,user,task = lifecycle
    claim(conn,task,org)
    conn.execute((MIGRATIONS/"255_scheduled_task_schedule_intent.sql").read_text())
    with pytest.raises(psycopg.errors.RaiseException,match="DRAINED_RUNS"):
        with conn.transaction():
            conn.execute((MIGRATIONS/"rollback/255_scheduled_task_schedule_intent_rollback.sql").read_text())
    manage(conn,org,user,task,"pause")
    start(conn,task,org,taskrow(conn,task)["run_token"])
    conn.execute((MIGRATIONS/"rollback/255_scheduled_task_schedule_intent_rollback.sql").read_text())
    assert taskrow(conn,task)["status"] == "paused"
    conn.execute("UPDATE scheduled_tasks SET status='running' WHERE id=%s",(task,))
    with pytest.raises(psycopg.errors.RaiseException,match="DRAINED_RUNS"):
        with conn.transaction():
            conn.execute((MIGRATIONS/"255_scheduled_task_schedule_intent.sql").read_text())


def test_existing_actor_uncertain_and_replay_contract_on_disposable_database(database, monkeypatch):
    """Run the existing opt-in test body against our disposable database, unchanged."""
    conn = database
    conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT")
    conn.execute("CREATE TABLE conversations(id UUID PRIMARY KEY,user_id UUID,org_id UUID,title TEXT)")
    conn.execute("""CREATE TABLE tasks(id UUID PRIMARY KEY,user_id UUID,conversation_id UUID,type TEXT,status TEXT,
                 delivery_context JSONB,turn_id UUID,execution_token UUID,lease_expires_at TIMESTAMPTZ)""")
    conn.execute((MIGRATIONS/"139_tool_invocations.sql").read_text())
    migration = (MIGRATIONS/"239_conversation_actor_production_contract_compat.sql").read_text()
    start_at = migration.index("CREATE OR REPLACE FUNCTION public.mark_stale_tool_invocation_uncertain(")
    end_at = migration.index("$$;",start_at)+3
    conn.execute(migration[start_at:end_at])
    conn.commit()
    from tests import test_tool_invocation_uncertain_integration as legacy
    monkeypatch.setattr(legacy,"_DATABASE_URL",conn.info.dsn)
    legacy.test_stale_running_is_uncertain_and_success_is_replayed()
