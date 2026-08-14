from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import (
    ORG, USER, _create_runtime_task, _legacy_task, _setup,
)
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import _mutate


pytestmark = pytest.mark.external
MIGRATION = "227_30_agent_runtime_scheduled_submission.sql"
ROLLBACK = "rollback/227_30_agent_runtime_scheduled_submission_rollback.sql"


def _worker_rpc(url: str, name: str, args: tuple):
    placeholders = ",".join(["%s"] * len(args))
    with psycopg.connect(url) as conn:
        conn.execute("SET SESSION AUTHORIZATION everydayai_worker")
        conn.execute("SELECT set_config('app.access_kind','worker',true)")
        return conn.execute(
            f"SELECT {name}({placeholders})", args,
        ).fetchone()[0]


def _runtime_request(
    url: str, task_id: str, request_id: str, version: int, *, actor: str = USER,
):
    with psycopg.connect(url) as conn:
        conn.execute("SET SESSION AUTHORIZATION everydayai_runtime")
        conn.execute("SELECT set_config('app.access_kind','runtime',false)")
        conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (actor,))
        conn.execute("SELECT set_config('app.org_id',%s,false)", (ORG,))
        return conn.execute(
            "SELECT request_agent_runtime_scheduled_execution_v1(%s,%s,%s,%s,%s,%s)",
            (request_id, task_id, ORG, actor, version, datetime.now(timezone.utc)),
        ).fetchone()[0]


def _enable(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runtime_scheduled_submission_control "
            "SET mode='disposable',state_version=state_version+1"
        )
        conn.commit()


def _prepare_runtime_due(url: str) -> str:
    task_id, ids = _create_runtime_task(url)
    due = datetime.now(timezone.utc) - timedelta(minutes=1)
    with psycopg.connect(url) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_execution_profiles "
            "WHERE scheduled_task_id=%s", (task_id,),
        ).fetchone()[0] == 1
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE scheduled_tasks SET status='active',next_run_at=%s WHERE id=%s",
            (due, task_id),
        )
        conn.commit()
    return task_id


def _prepare_a2(url: str) -> None:
    _setup(url)
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS source varchar(20) DEFAULT 'web'"
        )
        conn.commit()
    _apply(url, MIGRATION)


def test_migration_fails_closed_for_existing_runtime_task_without_profile(database: str) -> None:
    _setup(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS source varchar(20) DEFAULT 'web'"
        )
        conn.commit()
    task_id, _ = _create_runtime_task(database)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_execution_profiles "
            "WHERE scheduled_task_id=%s", (task_id,),
        ).fetchone()[0] == 0

    with pytest.raises(Exception, match="PROFILE_BACKFILL_REQUIRED"):
        _apply(database, MIGRATION)


def test_runtime_submission_is_single_owner_and_claim_binds_run(database: str) -> None:
    _prepare_a2(database)
    _enable(database)
    task_id = _prepare_runtime_due(database)
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(
            lambda _: _worker_rpc(
                database, "worker_claim_due_scheduled_executions_v1", (now, 5),
            ),
            range(3),
        ))
    submitted = [item for rows in results for item in rows]
    assert len(submitted) == 1
    assert submitted[0]["outcome"] == "submitted"
    command_id = submitted[0]["command_id"]

    readback = _worker_rpc(
        database, "read_agent_runtime_scheduled_submission_v1",
        (task_id, "scheduled", submitted[0]["binding"]["trigger_key"]),
    )
    assert readback["command_id"] == command_id

    with psycopg.connect(database) as conn:
        envelope = conn.execute(
            "SELECT _agent_command_run_envelope(command) FROM "
            "agent_session_commands command WHERE id=%s", (command_id,),
        ).fetchone()[0]
        assert envelope is not None

    with psycopg.connect(database) as conn:
        conn.execute("SET SESSION AUTHORIZATION everydayai_agent_runtime_worker")
        conn.execute("SELECT set_config('app.access_kind','agent_runtime',true)")
        claimed = None
        for _ in range(10):
            candidate = conn.execute(
                "SELECT claim_pending_agent_command_and_ensure_run(%s,90,3)",
                ("a2-runtime-worker",),
            ).fetchone()[0]
            if candidate.get("command_id") == command_id:
                claimed = candidate
                break
        conn.commit()
    assert claimed is not None
    assert claimed["outcome"] == "claimed"
    with psycopg.connect(database) as conn:
        binding = conn.execute(
            "SELECT runtime_command_id,runtime_run_id,owner_status FROM "
            "agent_runtime_scheduled_run_bindings WHERE scheduled_task_id=%s",
            (task_id,),
        ).fetchone()
        assert str(binding[0]) == command_id
        assert str(binding[1]) == claimed["run_id"]
        assert binding[2] == "runtime_claimed"
        assert conn.execute(
            "SELECT count(*) FROM conversations WHERE source='scheduler'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM agent_session_commands WHERE id=%s", (command_id,)
        ).fetchone()[0] == 1
    with pytest.raises(Exception, match="SCHEDULED_RUN_RUNTIME_OWNED"):
        _worker_rpc(database, "worker_assert_scheduled_task_legacy_owner_v1", (task_id,))
    with pytest.raises(Exception, match="ROLLBACK_FACTS_EXIST"):
        _apply(database, ROLLBACK)


def test_runtime_cannot_adopt_profileless_legacy_task(database: str) -> None:
    _prepare_a2(database)
    _, ids = _create_runtime_task(database)
    legacy_id = _legacy_task(database)

    with pytest.raises(Exception, match="PROFILE_REQUIRED_BEFORE_MUTATION"):
        _mutate(database, ids, legacy_id, "pause", 0, "adopt-legacy", {})

    with psycopg.connect(database) as conn:
        task = conn.execute(
            "SELECT status,runtime_action_id FROM scheduled_tasks WHERE id=%s",
            (legacy_id,),
        ).fetchone()
        assert task == ("active", None)


def test_disabled_mode_legacy_acl_and_empty_rollback(database: str) -> None:
    _prepare_a2(database)
    task_id = _prepare_runtime_due(database)
    legacy_id = _legacy_task(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE scheduled_tasks SET next_run_at=%s WHERE id=%s",
            (datetime.now(timezone.utc) - timedelta(seconds=30), legacy_id),
        )
        conn.commit()
    claims = _worker_rpc(
        database, "worker_claim_due_scheduled_executions_v1",
        (datetime.now(timezone.utc), 5),
    )
    assert len(claims) == 1
    assert claims[0]["owner_kind"] == "legacy"
    assert claims[0]["task"]["id"] == legacy_id
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT status FROM scheduled_tasks WHERE id=%s", (task_id,)
        ).fetchone()[0] == "active"
        for role in ("everydayai_worker", "everydayai_agent_runtime_worker"):
            assert conn.execute(
                "SELECT has_table_privilege(%s,'agent_runtime_scheduled_submission_intents','SELECT')",
                (role,),
            ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_worker',"
            "'worker_claim_due_scheduled_executions_v1(timestamp with time zone,integer)','EXECUTE')"
        ).fetchone()[0] is True
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_worker',"
            "'request_agent_runtime_scheduled_execution_v1(text,uuid,uuid,uuid,bigint,timestamp with time zone)','EXECUTE')"
        ).fetchone()[0] is False
        conn.execute("SET ROLE everydayai_owner")
        with pytest.raises(Exception, match="ROLLBACK_FACTS_EXIST"):
            _apply(database, ROLLBACK)
        conn.execute("TRUNCATE agent_runtime_scheduled_execution_profiles")
        conn.execute("DELETE FROM scheduled_tasks WHERE id=%s", (task_id,))
        conn.commit()
    _apply(database, ROLLBACK)
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)


def test_scheduled_and_manual_race_has_one_authoritative_command(database: str) -> None:
    _prepare_a2(database)
    _enable(database)
    task_id = _prepare_runtime_due(database)
    now = datetime.now(timezone.utc)

    def scheduled():
        return _worker_rpc(
            database, "worker_claim_due_scheduled_executions_v1", (now, 5),
        )

    def manual():
        try:
            return _runtime_request(database, task_id, "manual-race", 1)
        except psycopg.Error:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        scheduled_result = pool.submit(scheduled)
        manual_result = pool.submit(manual)
        results = [scheduled_result.result(), manual_result.result()]

    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(DISTINCT command_id),count(*) FROM "
            "agent_runtime_scheduled_submission_intents WHERE scheduled_task_id=%s",
            (task_id,),
        ).fetchone() == (1, 1)
        assert conn.execute(
            "SELECT count(*) FROM agent_session_commands command JOIN "
            "agent_runtime_scheduled_submission_intents intent ON "
            "intent.command_id=command.id WHERE intent.scheduled_task_id=%s",
            (task_id,),
        ).fetchone()[0] == 1
    assert any(result for result in results)


def test_manual_retry_reads_back_same_authoritative_command(database: str) -> None:
    _prepare_a2(database)
    _enable(database)
    task_id = _prepare_runtime_due(database)

    first = _runtime_request(database, task_id, "stable-manual-request", 1)
    second = _runtime_request(database, task_id, "stable-manual-request", 1)

    assert first["command_id"] == second["command_id"]
    assert second["outcome"] == "already_submitted"
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*),count(DISTINCT command_id) FROM "
            "agent_runtime_scheduled_submission_intents WHERE scheduled_task_id=%s",
            (task_id,),
        ).fetchone() == (1, 1)


def test_delegate_manual_retry_uses_requester_identity(database: str) -> None:
    _prepare_a2(database)
    _enable(database)
    task_id = _prepare_runtime_due(database)
    boss = "55555555-5555-5555-5555-555555555555"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO users(id) VALUES(%s)", (boss,))
        conn.execute(
            "INSERT INTO org_members(org_id,user_id,status) VALUES(%s,%s,'active')",
            (ORG, boss),
        )
        position = conn.execute(
            "INSERT INTO org_positions(org_id,code,name,level) "
            "VALUES(%s,'boss','Runtime Boss',1) RETURNING id", (ORG,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO org_member_assignments(org_id,user_id,position_id,data_scope) "
            "VALUES(%s,%s,%s,'all')", (ORG, boss, position),
        )
        conn.commit()

    first = _runtime_request(
        database, task_id, "delegate-stable-request", 1, actor=boss,
    )
    second = _runtime_request(
        database, task_id, "delegate-stable-request", 1, actor=boss,
    )

    assert first["command_id"] == second["command_id"]
    assert second["outcome"] == "already_submitted"
    with psycopg.connect(database) as conn:
        row = conn.execute(
            "SELECT user_id,requester_user_id FROM "
            "agent_runtime_scheduled_submission_intents WHERE scheduled_task_id=%s",
            (task_id,),
        ).fetchone()
        assert str(row[0]) == USER
        assert str(row[1]) == boss


def test_revision_pause_and_kill_fences_leave_no_submission(database: str) -> None:
    _prepare_a2(database)
    _enable(database)
    task_id = _prepare_runtime_due(database)
    with pytest.raises(Exception, match="SCHEDULED_MANUAL_TASK_FENCED"):
        _runtime_request(database, task_id, "stale-revision", 99)

    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE scheduled_tasks SET status='paused',next_run_at=NULL WHERE id=%s",
            (task_id,),
        )
        conn.commit()
    assert _worker_rpc(
        database, "worker_claim_due_scheduled_executions_v1",
        (datetime.now(timezone.utc), 5),
    ) == []

    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE scheduled_tasks SET status='active',next_run_at=%s WHERE id=%s",
            (datetime.now(timezone.utc) - timedelta(minutes=1), task_id),
        )
        conn.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls"
            "(org_id,gate_scope,scope_key,dispatch_blocked,kill_epoch,state_version,reason,updated_by) "
            "VALUES(%s,'tenant','tenant',true,1,1,'a2-test',%s)",
            (ORG, USER),
        )
        conn.commit()
    with pytest.raises(Exception, match="SCHEDULED_TENANT_FENCED"):
        _worker_rpc(
            database, "worker_claim_due_scheduled_executions_v1",
            (datetime.now(timezone.utc), 5),
        )
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_submission_intents "
            "WHERE scheduled_task_id=%s", (task_id,),
        ).fetchone()[0] == 0
