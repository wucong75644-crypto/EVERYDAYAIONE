from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import (
    _apply, _seed_specialist_action,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "227_28_agent_runtime_scheduler_control.sql"
ROLLBACK = "rollback/227_28_agent_runtime_scheduler_control_rollback.sql"
ORG = "22222222-2222-2222-2222-222222222222"
USER = "44444444-4444-4444-4444-444444444444"


def _prepare(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS ltree")
        conn.execute("SET ROLE everydayai_owner")
        for name in (
            "060_org_departments.sql", "061_org_positions.sql",
            "064_org_member_assignments.sql", "026_add_wecom_user_mappings.sql",
            "036_wecom_chat_targets.sql",
        ):
            conn.execute((ROOT / "migrations" / name).read_text())
        conn.execute("ALTER TABLE wecom_user_mappings ADD COLUMN org_id UUID REFERENCES organizations(id)")
        conn.execute("ALTER TABLE wecom_chat_targets ADD COLUMN org_id UUID REFERENCES organizations(id)")
        department = str(uuid4())
        position = str(uuid4())
        conn.execute(
            "INSERT INTO org_departments(id,org_id,name,type,path) "
            "VALUES(%s,%s,'Runtime Ops','ops','root.runtime_ops')",
            (department, ORG),
        )
        conn.execute(
            "INSERT INTO org_positions(id,org_id,code,name,level) "
            "VALUES(%s,%s,'manager','Runtime Manager',3)",
            (position, ORG),
        )
        conn.execute(
            "INSERT INTO org_member_assignments(org_id,user_id,department_id,"
            "position_id,data_scope) VALUES(%s,%s,%s,%s,'dept_subtree')",
            (ORG, USER, department, position),
        )
        conn.execute((ROOT / "migrations/069_scheduled_tasks.sql").read_text())
        conn.execute((ROOT / "migrations/071_scheduled_task_schedule_type.sql").read_text())
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY,org_id UUID,user_id UUID,relative_path TEXT NOT NULL,oss_object_key TEXT NOT NULL,purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.commit()
    _apply(database, "176_worker_scheduled_scanner.sql")
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_04_agent_runtime_provider_submission_facts.sql",
        "227_05_agent_runtime_scheduler_cas.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_08_agent_runtime_facts_recovery_fence.sql",
        MIGRATION,
    ):
        _apply(database, name)


def _seed(database: str) -> dict[str, str]:
    conversation_id = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_policy_receipts SET receipt_hash="
            "md5(id::text)||md5(id::text||'b7')"
        )
        conn.execute(
            "INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) "
            "VALUES(%s,%s,%s,'user',%s)",
            (conversation_id, USER, ORG, USER),
        )
        conn.commit()
    ids = _seed_specialist_action(database, conversation_id)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_actions SET tool_name='manage_scheduled_task',"
            "policy_snapshot=%s WHERE id=%s",
            (Jsonb({"provider": "scheduler", "provider_revision": "scheduler-v1",
                    "capability": "runtime.scheduler.control",
                    "capability_revision": "control-v1"}), ids["action"]),
        )
        conn.execute(
            "UPDATE agent_policy_receipts SET executor_type="
            "'runtime_scheduled_task:manage_scheduled_task',effective_scope=%s "
            "WHERE id=%s", (Jsonb({"org_id": ORG}), ids["policy"]),
        )
        conn.execute(
            "UPDATE agent_action_dispatch_intents SET executor_type="
            "'runtime_scheduled_task:manage_scheduled_task',executor_revision=1,"
            "recovery_mode='reconcile_only' WHERE attempt_id=%s", (ids["attempt"],),
        )
        conn.execute(
            "INSERT INTO agent_runtime_owner_fences(owner_kind,owner_id,org_id,"
            "execution_token,tenant_kill_epoch,provider_kill_epoch,capability_kill_epoch,"
            "provider_revision,capability_revision,state_version,status) "
            "VALUES('attempt',%s,%s,%s,0,0,0,'scheduler-v1','control-v1',0,'active')",
            (ids["attempt"], ORG, ids["token"]),
        )
        row = conn.execute(
            "SELECT id FROM agent_action_dispatch_intents WHERE attempt_id=%s",
            (ids["attempt"],),
        ).fetchone()
        ids["dispatch"] = str(row[0])
        ids["attempt_version"] = "0"
        conn.commit()
    return ids


def _rpc(database: str, name: str, params: tuple[object, ...], *, org: str = ORG):
    url = database.replace("postgres@", "everydayai_agent_runtime_worker@")
    with psycopg.connect(url) as conn:
        conn.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
        conn.execute("SELECT set_config('app.request_id',%s,false)", (str(uuid4()),))
        conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (USER,))
        conn.execute("SELECT set_config('app.org_id',%s,false)", (org,))
        values = tuple(Jsonb(value) if isinstance(value, (dict, list)) else value for value in params)
        result = conn.execute(
            f"SELECT {name}({','.join(['%s'] * len(values))})", values,
        ).fetchone()[0]
        conn.commit()
        return result


def _mutate(database: str, ids: dict[str, str], task_id: str, operation: str,
            expected: int, key: str, payload: dict[str, object]):
    return _rpc(database, "mutate_agent_runtime_scheduled_task_control_v1", (
        ids["attempt"], ids["action"], ids["run"], ORG, USER, "user", USER,
        task_id, operation, expected, int(ids["attempt_version"]),
        ids["request_hash"], ids["token"], key, ids["dispatch"], payload,
    ))


def _create_payload(name: str = "Runtime schedule") -> dict[str, object]:
    return {
        "name": name, "prompt": "Summarize inventory", "cron_expr": "0 9 * * *",
        "timezone": "Asia/Shanghai", "push_target": {"type": "web", "user_id": USER},
        "schedule_type": "cron", "next_run_at": "2030-01-01T01:00:00+00:00",
        "max_credits": 10, "retry_count": 1, "timeout_sec": 180,
    }


def _cancel(database: str, ids: dict[str, str], key: str, *, token: str | None = None,
            state_version: int = 0):
    return _rpc(database, "cancel_agent_runtime_scheduled_task_control_v1", (
        ids["attempt"], ids["request_hash"], token or ids["token"],
        state_version, key, "runtime_cancel",
    ))


def test_b7_five_operations_readback_acl_and_rollback(database: str) -> None:
    _prepare(database)
    task_id = str(uuid4())
    create = _mutate(database, _seed(database), task_id, "create", 0, "b7-create", _create_payload())
    assert create["outcome"] == "committed" and create["state_version"] == 1
    with pytest.raises(Exception, match="RUNTIME_SCHEDULER_REQUEST_INVALID"):
        _mutate(
            database, _seed(database), task_id, "update", 1,
            "b7-invalid-update", {"max_credits": -1},
        )
    with pytest.raises(Exception, match="RUNTIME_SCHEDULER_REQUEST_INVALID"):
        _mutate(
            database, _seed(database), task_id, "update", 1,
            "b7-invalid-timezone", {
                "schedule_type": "cron", "cron_expr": "0 8 * * *",
                "timezone": "Not/AZone",
            },
        )
    with pytest.raises(Exception, match="RUNTIME_SCHEDULER_REQUEST_INVALID"):
        _mutate(
            database, _seed(database), task_id, "update", 1,
            "b7-stale-schedule", {
                "schedule_type": "cron", "cron_expr": "0 8 * * *",
                "timezone": "America/New_York",
            },
        )
    update = _mutate(database, _seed(database), task_id, "update", 1, "b7-update", {
        "name": "Updated", "schedule_type": "cron", "cron_expr": "0 8 * * *",
        "timezone": "America/New_York",
        "next_run_at": "2030-01-01T08:00:00-05:00",
    })
    with psycopg.connect(database) as conn:
        persisted = conn.execute(
            "SELECT timezone,next_run_at AT TIME ZONE 'UTC' FROM scheduled_tasks WHERE id=%s",
            (task_id,),
        ).fetchone()
        assert persisted[0] == "America/New_York"
        assert persisted[1].isoformat() == "2030-01-01T13:00:00"
    pause = _mutate(database, _seed(database), task_id, "pause", 2, "b7-pause", {})
    resume = _mutate(database, _seed(database), task_id, "resume", 3, "b7-resume", {})
    delete_ids = _seed(database)
    deleted = _mutate(database, delete_ids, task_id, "delete", 4, "b7-delete", {})
    assert [update["state_version"], pause["state_version"], resume["state_version"], deleted["state_version"]] == [2, 3, 4, 5]
    readback = _rpc(database, "read_agent_runtime_scheduled_task_control_v1", (
        delete_ids["attempt"], delete_ids["request_hash"], delete_ids["token"], 0,
        "b7-delete",
    ))
    assert readback["outcome"] == "readback" and readback["task"]["deleted"] is True
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM scheduled_tasks WHERE id=%s", (task_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM agent_runtime_events WHERE correlation_id=%s AND event_type='scheduler.task.committed'", (delete_ids["action"],)).fetchone()[0] == 1
        for table in (
            "agent_runtime_scheduler_operation_intents",
            "agent_runtime_scheduler_operation_receipts",
            "agent_runtime_scheduler_cancel_gates",
        ):
            assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", (table,)).fetchone() == (True, True)
            assert conn.execute("SELECT has_table_privilege('everydayai_agent_runtime_worker',%s,'SELECT')", (table,)).fetchone()[0] is False
        assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker','mutate_agent_runtime_scheduled_task_control_v1(uuid,uuid,uuid,uuid,uuid,text,text,uuid,text,bigint,bigint,text,uuid,text,uuid,jsonb)','EXECUTE')").fetchone()[0] is True
        assert conn.execute("SELECT has_function_privilege('everydayai_worker','mutate_agent_runtime_scheduled_task_control_v1(uuid,uuid,uuid,uuid,uuid,text,text,uuid,text,bigint,bigint,text,uuid,text,uuid,jsonb)','EXECUTE')").fetchone()[0] is False
        assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker','runtime_mutate_scheduled_task(uuid,uuid,uuid,bigint,text,text,jsonb,uuid)','EXECUTE')").fetchone()[0] is False
    with pytest.raises(Exception, match="AR_18_B7_ROLLBACK_BLOCKED_SCHEDULER_INTENTS"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("TRUNCATE agent_runtime_scheduler_operation_receipts,agent_runtime_scheduler_operation_intents")
        conn.commit()
    _apply(database, ROLLBACK)
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)


def test_b7_concurrency_idempotency_tenant_fences_and_transaction_boundary(database: str) -> None:
    _prepare(database)
    task_id = str(uuid4())
    left, right = _seed(database), _seed(database)
    barrier = Barrier(2)

    def create(ids: dict[str, str], key: str):
        barrier.wait()
        return _mutate(database, ids, task_id, "create", 0, key, _create_payload(key))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result() for future in (
            pool.submit(create, left, "b7-race-a"),
            pool.submit(create, right, "b7-race-b"),
        )]
    assert sorted(item["outcome"] for item in outcomes) == ["cas_conflict", "committed"]
    winner_ids, winner_key = (left, "b7-race-a") if outcomes[0]["outcome"] == "committed" else (right, "b7-race-b")
    loser_ids, loser_key = (right, "b7-race-b") if outcomes[0]["outcome"] == "committed" else (left, "b7-race-a")
    conflict_readback = _rpc(
        database, "read_agent_runtime_scheduled_task_control_v1", (
            loser_ids["attempt"], loser_ids["request_hash"],
            loser_ids["token"], 0, loser_key,
        ),
    )
    assert conflict_readback["outcome"] == "readback"
    assert conflict_readback["receipt_outcome"] == "cas_conflict"
    replay = _mutate(database, winner_ids, task_id, "create", 0, winner_key, _create_payload(winner_key))
    assert replay["outcome"] == "readback"
    with pytest.raises(Exception, match="RUNTIME_SCHEDULER_IDEMPOTENCY_CONFLICT"):
        _mutate(database, winner_ids, str(uuid4()), "create", 0, winner_key, _create_payload("conflict"))

    foreign_id = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO organizations(id) VALUES(%s)", (foreign_id,))
        conn.execute("UPDATE scheduled_tasks SET org_id=%s WHERE id=%s", (foreign_id, task_id))
        conn.commit()
    with pytest.raises(Exception, match="RUNTIME_SCHEDULER_TENANT_SCOPE_MISMATCH"):
        _mutate(database, _seed(database), task_id, "pause", 1, "b7-cross", {})

    blocked = _seed(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runs SET status='cancelled',completed_at=clock_timestamp(),"
            "execution_token=NULL,lease_expires_at=NULL,blocking_action_count=0 WHERE id=%s",
            (blocked["run"],),
        )
        conn.commit()
    before = str(uuid4())
    with pytest.raises(Exception, match="RUNTIME_SCHEDULER_CONTEXT_MISMATCH"):
        _mutate(database, blocked, before, "create", 0, "b7-cancel-before", _create_payload())
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM scheduled_tasks WHERE id=%s", (before,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM agent_runtime_scheduler_operation_intents WHERE idempotency_key='b7-cancel-before'").fetchone()[0] == 0

    crash = _seed(database)
    crash_task = str(uuid4())
    worker_url = database.replace("postgres@", "everydayai_agent_runtime_worker@")
    with psycopg.connect(worker_url) as conn:
        conn.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
        conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (USER,))
        conn.execute("SELECT set_config('app.org_id',%s,false)", (ORG,))
        with pytest.raises(RuntimeError):
            with conn.transaction():
                conn.execute(
                    "SELECT mutate_agent_runtime_scheduled_task_control_v1(%s,%s,%s,%s,%s,'user',%s,%s,'create',0,0,%s,%s,'b7-crash',%s,%s)",
                    (crash["attempt"], crash["action"], crash["run"], ORG, USER, USER,
                     crash_task, crash["request_hash"], crash["token"], crash["dispatch"],
                     Jsonb(_create_payload())),
                )
                raise RuntimeError("simulate crash before commit")
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM scheduled_tasks WHERE id=%s", (crash_task,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM agent_runtime_scheduler_operation_intents WHERE idempotency_key='b7-crash'").fetchone()[0] == 0


def test_b7_cancel_gate_postcommit_readback_and_stale_token_fence(database: str) -> None:
    _prepare(database)
    before = _seed(database)
    before_task = str(uuid4())
    cancelled = _cancel(database, before, "b7-cancel-gate")
    assert cancelled["outcome"] == "cancelled" and cancelled["cancel_confirmed"] is True
    blocked = _mutate(
        database, before, before_task, "create", 0, "b7-cancel-gate", _create_payload(),
    )
    assert blocked["outcome"] == "cancelled"
    gate_readback = _rpc(database, "read_agent_runtime_scheduled_task_control_v1", (
        before["attempt"], before["request_hash"], before["token"], 0,
        "b7-cancel-gate",
    ))
    assert gate_readback["outcome"] == "cancelled"

    committed_ids = _seed(database)
    committed_task = str(uuid4())
    assert _mutate(
        database, committed_ids, committed_task, "create", 0,
        "b7-committed", _create_payload(),
    )["outcome"] == "committed"
    assert _cancel(database, committed_ids, "b7-committed")["outcome"] == "committed_readback"

    readback_ids = _seed(database)
    readback_task = str(uuid4())
    assert _mutate(
        database, readback_ids, readback_task, "create", 0,
        "b7-readback-token", _create_payload(),
    )["outcome"] == "committed"
    readback_reconciliation_token = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_action_attempts SET status='accepted',dispatch_phase='accepted',"
            "external_receipt='{\"state\":\"accepted\"}',accepted_at=clock_timestamp(),state_version=1,"
            "reconciliation_token=%s,reconciliation_lease_expires_at="
            "clock_timestamp()+interval '10 minutes' WHERE id=%s",
            (readback_reconciliation_token, readback_ids["attempt"]),
        )
        conn.execute(
            "UPDATE agent_actions SET status='accepted',accepted_at=clock_timestamp() WHERE id=%s",
            (readback_ids["action"],),
        )
        conn.commit()
    stale_readback = _rpc(
        database, "read_agent_runtime_scheduled_task_control_v1", (
            readback_ids["attempt"], readback_ids["request_hash"],
            readback_ids["token"], 1, "b7-readback-token",
        ),
    )
    assert stale_readback["outcome"] == "not_found"
    owned_readback = _rpc(
        database, "reconcile_agent_runtime_scheduled_task_control_v1", (
            readback_ids["attempt"], readback_ids["request_hash"],
            readback_reconciliation_token, 1, "b7-readback-token",
        ),
    )
    assert owned_readback["outcome"] == "readback"

    stale = _seed(database)
    reconciliation_token = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_action_attempts SET status='accepted',dispatch_phase='accepted',"
            "external_receipt='{\"state\":\"accepted\"}',accepted_at=clock_timestamp(),state_version=1,"
            "reconciliation_token=%s,reconciliation_lease_expires_at="
            "clock_timestamp()+interval '10 minutes' WHERE id=%s",
            (reconciliation_token, stale["attempt"]),
        )
        conn.execute("UPDATE agent_actions SET status='accepted',accepted_at=clock_timestamp() WHERE id=%s", (stale["action"],))
        conn.commit()
    with pytest.raises(Exception, match="RUNTIME_SCHEDULER_CANCEL_FENCED"):
        _cancel(database, stale, "b7-stale", state_version=1)
    current = _cancel(
        database, stale, "b7-stale", token=reconciliation_token, state_version=1,
    )
    assert current["outcome"] == "cancelled"


def test_b7_push_target_membership_and_management_permission(database: str) -> None:
    _prepare(database)
    colleague = str(uuid4())
    member_position = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("INSERT INTO users(id) VALUES(%s)", (colleague,))
        conn.execute(
            "INSERT INTO org_members(org_id,user_id,status) VALUES(%s,%s,'active')",
            (ORG, colleague),
        )
        conn.execute(
            "INSERT INTO org_positions(id,org_id,code,name,level) "
            "VALUES(%s,%s,'member','Runtime Member',5)",
            (member_position, ORG),
        )
        conn.commit()
    manager_payload = _create_payload("Manager multi target")
    manager_payload["push_target"] = {
        "type": "multi", "targets": [
            {"type": "web", "user_id": USER},
            {"type": "web", "user_id": colleague},
        ],
    }
    assert _mutate(
        database, _seed(database), str(uuid4()), "create", 0,
        "b7-manager-multi", manager_payload,
    )["outcome"] == "committed"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE org_member_assignments SET position_id=%s WHERE org_id=%s AND user_id=%s",
            (member_position, ORG, USER),
        )
        conn.commit()
    other_payload = _create_payload("Member other target")
    other_payload["push_target"] = {"type": "web", "user_id": colleague}
    with pytest.raises(Exception, match="RUNTIME_SCHEDULER_PUSH_TARGET_DENIED"):
        _mutate(
            database, _seed(database), str(uuid4()), "create", 0,
            "b7-member-other", other_payload,
        )
    self_payload = _create_payload("Member self target")
    assert _mutate(
        database, _seed(database), str(uuid4()), "create", 0,
        "b7-member-self", self_payload,
    )["outcome"] == "committed"
    other_department = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO org_departments(id,org_id,name,type,path) "
            "VALUES(%s,%s,'Runtime Other','other','root.runtime_other')",
            (other_department, ORG),
        )
        conn.execute(
            "UPDATE org_member_assignments SET department_id=%s WHERE org_id=%s AND user_id=%s",
            (other_department, ORG, USER),
        )
        conn.commit()
    with pytest.raises(Exception, match="RUNTIME_SCHEDULER_OPERATION_PERMISSION_DENIED"):
        _mutate(
            database, _seed(database), str(uuid4()), "create", 0,
            "b7-other-department", _create_payload("Other department denied"),
        )
