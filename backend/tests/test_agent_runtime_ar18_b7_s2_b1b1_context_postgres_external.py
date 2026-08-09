from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from tests.test_agent_runtime_ar173_postgres_external import _seed_specialist_action
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import _rpc
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG, USER
from tests.test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_postgres_external import (
    _bound_run,
    _install_final_result,
)
from tests.test_agent_runtime_ar18_b7_s2_b1b_finalizer_postgres_external import (
    _apply_final,
    _claim,
    _prepare_b1b,
)
from tests.test_agent_runtime_tenant_kill_control_postgres_external import _set_gate


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


def _apply_v2(url: str, claim: dict, *, request_id: str | None = None,
              token: str | None = None, intent_version: int | None = None,
              task_version: int | None = None, schedule_hash: str | None = None,
              next_run_at: datetime | None = None) -> dict:
    return _rpc(url, "apply_agent_runtime_scheduled_finalization_v2", (
        claim["intent"]["scheduled_run_id"], token or claim["intent"]["claim_token"],
        claim["intent"]["state_version"] if intent_version is None else intent_version,
        claim["task_schedule"]["state_version"] if task_version is None else task_version,
        schedule_hash or claim["task_schedule"]["schedule_hash"], request_id or str(uuid4()),
        "runtime_finalizer", next_run_at or datetime.now(timezone.utc) + timedelta(hours=1),
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
    "binding", "tenant", "schedule", "revision",
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
        else:
            conn.execute("ALTER TABLE agent_runtime_scheduled_execution_profiles DISABLE TRIGGER "
                         "runtime_scheduled_profile_immutable")
            conn.execute("UPDATE agent_runtime_scheduled_execution_profiles SET provider_revision='fenced' "
                         "WHERE scheduled_task_id=%s", (facts["task_id"],))
            conn.execute("ALTER TABLE agent_runtime_scheduled_execution_profiles ENABLE TRIGGER "
                         "runtime_scheduled_profile_immutable")
        conn.commit()
    assert _read(database, run_id, token)["outcome"] == "fenced"


@pytest.mark.parametrize("scope", ("tenant", "provider", "capability"))
def test_terminal_finalization_converges_after_kill(database: str, scope: str) -> None:
    facts, claim = _prepare_context(database)
    run_id = claim["intent"]["scheduled_run_id"]
    token = claim["intent"]["claim_token"]
    with psycopg.connect(database) as conn:
        profile = conn.execute(
            "SELECT e.provider_key,e.capability_key,s.conversation_id "
            "FROM agent_runtime_scheduled_execution_profiles e "
            "JOIN agent_runtime_scheduled_run_bindings b ON b.scheduled_task_id=e.scheduled_task_id "
            "JOIN agent_runs r ON r.id=b.runtime_run_id "
            "JOIN agent_runtime_sessions s ON s.id=r.session_id "
            "WHERE e.scheduled_task_id=%s", (facts["task_id"],),
        ).fetchone()
    key = "tenant" if scope == "tenant" else profile[0 if scope == "provider" else 1]

    dispatch_ids = None
    if scope == "tenant":
        conversation_id = str(uuid4())
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("UPDATE agent_policy_receipts SET receipt_hash=%s WHERE receipt_hash=%s",
                         ("1" * 64, "d" * 64))
            conn.execute("INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) "
                         "VALUES(%s,%s,%s,'user',%s)",
                         (conversation_id, USER, ORG, USER))
            conn.commit()
        dispatch_ids = _seed_specialist_action(database, conversation_id)
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("INSERT INTO agent_runtime_owner_fences(owner_kind,owner_id,org_id,"
                         "execution_token,tenant_kill_epoch,status) VALUES('attempt',%s,"
                         "'22222222-2222-2222-2222-222222222222',%s,0,'active')",
                         (dispatch_ids["attempt"], dispatch_ids["token"]))
            conn.commit()

    assert _set_gate(database, str(uuid4()), scope, key, True, 0)["outcome"] == "applied"
    assert _read(database, run_id, token)["outcome"] == "found"
    with pytest.raises(Exception, match="EPOCH_FENCED"):
        _apply_final(database, claim, datetime.now(timezone.utc) + timedelta(hours=1))

    if dispatch_ids is not None:
        dispatch = _rpc(database, "gate_agent_action_dispatch_v2", (
            dispatch_ids["attempt"], dispatch_ids["token"], 0,
            dispatch_ids["request_hash"], dispatch_ids["policy"],
            "runtime_media_generation:generate_image", 1, "v1", "reconcile_only",
        ))
        assert dispatch["error_code"] == "RUNTIME_KILL_EPOCH_FENCED"

    applied = _apply_v2(database, claim)
    assert applied["outcome"] == "applied"
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT status FROM scheduled_task_runs WHERE id=%s", (run_id,)).fetchone()[0] == "success"
        assert conn.execute("SELECT status FROM agent_runtime_scheduled_finalization_intents "
                            "WHERE scheduled_run_id=%s", (run_id,)).fetchone()[0] == "applied"


def test_apply_v2_preserves_claim_version_schedule_and_idempotency_fences(database: str) -> None:
    _, claim = _prepare_context(database)
    with pytest.raises(Exception, match="CLAIM_FENCED"):
        _apply_v2(database, claim, token=str(uuid4()))
    with pytest.raises(Exception, match="STALE_VERSION"):
        _apply_v2(database, claim, intent_version=claim["intent"]["state_version"] + 1)
    with pytest.raises(Exception, match="SCOPE_FENCED"):
        _apply_v2(database, claim, task_version=claim["task_schedule"]["state_version"] + 1)
    with pytest.raises(Exception, match="SCOPE_FENCED"):
        _apply_v2(database, claim, schedule_hash="f" * 64)
    request_id = str(uuid4())
    next_run_at = datetime.now(timezone.utc) + timedelta(hours=1)
    assert _apply_v2(database, claim, request_id=request_id,
                     next_run_at=next_run_at)["outcome"] == "applied"
    assert _apply_v2(database, claim, request_id=request_id,
                     next_run_at=next_run_at)["outcome"] == "already_applied"
    with pytest.raises(Exception, match="IDEMPOTENCY_CONFLICT"):
        _apply_v2(database, claim, request_id=str(uuid4()))
