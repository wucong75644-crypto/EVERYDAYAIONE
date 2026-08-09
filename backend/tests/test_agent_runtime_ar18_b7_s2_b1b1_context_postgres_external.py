from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import _rpc
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import USER
from tests.test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_postgres_external import (
    _bound_run,
    _install_final_result,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b_finalizer_postgres_external import (
    _apply_final,
    _claim,
    _prepare_b1b,
)


pytestmark = pytest.mark.external
MIGRATION = "227_33_agent_runtime_scheduled_finalization_context.sql"
ROLLBACK = "rollback/227_33_agent_runtime_scheduled_finalization_context_rollback.sql"


def _prepare_context(url: str) -> tuple[dict, dict]:
    _prepare_b1b(url)
    _apply(url, MIGRATION)
    facts = _bound_run(url)
    result_hash = _install_final_result(url, facts)
    assert _rpc(url, "complete_agent_run", (
        facts["run_id"], facts["token"], facts["version"], result_hash,
    ))["outcome"] == "completed"
    return facts, _claim(url)


def _read(url: str, scheduled_run_id: str, token: str) -> dict:
    return _rpc(url, "read_agent_runtime_scheduled_finalization_context_v1", (
        scheduled_run_id, token,
    ))


def _fact_snapshot(url: str, scheduled_run_id: str) -> tuple:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT to_jsonb(i),to_jsonb(b),to_jsonb(q),to_jsonb(t),to_jsonb(e),to_jsonb(r) "
            "FROM agent_runtime_scheduled_finalization_intents i "
            "JOIN agent_runtime_scheduled_run_bindings b USING(scheduled_run_id) "
            "JOIN scheduled_task_runs q ON q.id=i.scheduled_run_id "
            "JOIN scheduled_tasks t ON t.id=i.scheduled_task_id "
            "JOIN agent_runtime_scheduled_execution_profiles e ON e.scheduled_task_id=t.id "
            "JOIN agent_runs r ON r.id=i.runtime_run_id WHERE i.scheduled_run_id=%s",
            (scheduled_run_id,),
        ).fetchone()


def test_claimed_context_is_whitelisted_and_side_effect_free(database: str) -> None:
    _, claim = _prepare_context(database)
    run_id = claim["intent"]["scheduled_run_id"]
    before = _fact_snapshot(database, run_id)
    result = _read(database, run_id, claim["intent"]["claim_token"])
    after = _fact_snapshot(database, run_id)
    assert result["outcome"] == "found"
    assert before == after
    context = result["context"]
    assert set(context) == {
        "scheduled_run_id", "runtime_run_id", "scheduled_task_id", "terminal_status",
        "terminal_baseline", "intent_state_version", "task_state_version", "schedule_hash",
        "schedule_type", "cron_expr", "timezone", "run_at", "weekdays", "day_of_month",
        "retry_count", "consecutive_failures",
    }
    assert context["terminal_status"] == "completed"
    assert context["retry_count"] >= 0
    assert context["consecutive_failures"] >= 0
    assert len(context["schedule_hash"]) == 64
    payload = json.dumps(result, sort_keys=True)
    for forbidden in (
        "claim_token", "prompt", "push_target", "last_result", "secret", "password",
        "provider_payload", "storage_ref", "/private/",
    ):
        assert forbidden not in payload.lower()


def test_context_token_lifecycle_and_acl(database: str) -> None:
    _, claim = _prepare_context(database)
    run_id = claim["intent"]["scheduled_run_id"]
    token = claim["intent"]["claim_token"]
    assert _read(database, str(uuid4()), token)["outcome"] == "not_found"
    assert _read(database, run_id, str(uuid4()))["outcome"] == "fenced"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("ALTER TABLE agent_runtime_scheduled_finalization_intents DISABLE TRIGGER "
                     "runtime_scheduled_finalization_immutable")
        conn.execute("UPDATE agent_runtime_scheduled_finalization_intents "
                     "SET claim_lease_expires_at=clock_timestamp()-interval '1 second' "
                     "WHERE scheduled_run_id=%s", (run_id,))
        conn.execute("ALTER TABLE agent_runtime_scheduled_finalization_intents ENABLE TRIGGER "
                     "runtime_scheduled_finalization_immutable")
        conn.commit()
    assert _read(database, run_id, token)["outcome"] == "fenced"

    legacy_url = database.replace("postgres@", "everydayai_worker@")
    with psycopg.connect(legacy_url) as conn:
        conn.execute("SELECT set_config('app.access_kind','agent_runtime',false)")
        with pytest.raises(Exception, match="permission denied"):
            conn.execute("SELECT read_agent_runtime_scheduled_finalization_context_v1(%s,%s)",
                         (run_id, token)).fetchone()
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT has_table_privilege('everydayai_agent_runtime_worker',"
            "'scheduled_tasks','SELECT')"
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT proconfig FROM pg_proc WHERE oid="
            "'read_agent_runtime_scheduled_finalization_context_v1(uuid,uuid)'::regprocedure"
        ).fetchone()[0] == ["search_path=pg_catalog, public"]


def test_applied_context_and_exact_rollback_reapply(database: str) -> None:
    _, claim = _prepare_context(database)
    run_id = claim["intent"]["scheduled_run_id"]
    token = claim["intent"]["claim_token"]
    assert _apply_final(
        database, claim, datetime.now(timezone.utc) + timedelta(hours=1),
    )["outcome"] == "applied"
    assert _read(database, run_id, token)["outcome"] == "applied"
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT to_regprocedure('read_agent_runtime_scheduled_finalization_context_v1(uuid,uuid)')"
        ).fetchone()[0] is None
    _apply(database, MIGRATION)
    assert _read(database, run_id, token)["outcome"] == "applied"
    _apply(database, ROLLBACK)


@pytest.mark.parametrize("conflict", (
    "binding", "tenant", "schedule", "epoch", "revision",
))
def test_context_fails_closed_on_fact_conflict(database: str, conflict: str) -> None:
    facts, claim = _prepare_context(database)
    run_id = claim["intent"]["scheduled_run_id"]
    token = claim["intent"]["claim_token"]
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        if conflict == "binding":
            conn.execute("UPDATE agent_runtime_scheduled_run_bindings SET owner_status='running',"
                         "state_version=state_version+1 WHERE scheduled_run_id=%s", (run_id,))
        elif conflict == "tenant":
            other_org = str(uuid4())
            conn.execute("INSERT INTO organizations(id) VALUES(%s)", (other_org,))
            conn.execute("UPDATE scheduled_task_runs SET org_id=%s WHERE id=%s", (other_org, run_id))
        elif conflict == "schedule":
            conn.execute("UPDATE scheduled_tasks SET cron_expr='17 4 * * *',"
                         "runtime_state_version=runtime_state_version+1 WHERE id=%s", (facts["task_id"],))
        elif conflict == "epoch":
            org_id = conn.execute("SELECT org_id FROM scheduled_tasks WHERE id=%s",
                                  (facts["task_id"],)).fetchone()[0]
            conn.execute("INSERT INTO agent_runtime_tenant_gate_controls(org_id,gate_scope,scope_key,"
                         "kill_epoch,state_version,reason,updated_by) VALUES(%s,'tenant','tenant',1,1,"
                         "'test fence',%s)", (org_id, USER))
        else:
            conn.execute("ALTER TABLE agent_runtime_scheduled_execution_profiles DISABLE TRIGGER "
                         "runtime_scheduled_profile_immutable")
            conn.execute("UPDATE agent_runtime_scheduled_execution_profiles SET provider_revision='fenced' "
                         "WHERE scheduled_task_id=%s", (facts["task_id"],))
            conn.execute("ALTER TABLE agent_runtime_scheduled_execution_profiles ENABLE TRIGGER "
                         "runtime_scheduled_profile_immutable")
        conn.commit()
    assert _read(database, run_id, token)["outcome"] == "fenced"
