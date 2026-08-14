from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import _rpc
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import USER
from tests.test_agent_runtime_ar18_b7_s2_b1a_terminal_intent_postgres_external import (
    _bound_run, _install_final_result, _prepare, _request_rpc,
)


pytestmark = pytest.mark.external
MIGRATION = "227_32_agent_runtime_scheduled_finalization_apply.sql"
ROLLBACK = "rollback/227_32_agent_runtime_scheduled_finalization_apply_rollback.sql"


def _prepare_b1b(url: str) -> None:
    _prepare(url)
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS content_hash TEXT")
        conn.execute("ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS storage_ref JSONB")
        conn.execute("ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS inline_content JSONB")
        conn.execute("ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS sensitivity TEXT")
        conn.commit()
    _apply(url, MIGRATION)


def _claim(url: str) -> dict:
    claim = _rpc(url, "claim_next_agent_runtime_scheduled_finalization_v1", ("b1b", 90))
    assert claim["outcome"] == "claimed"
    return claim


def _apply_final(url: str, claim: dict, next_run_at=None, request_id=None) -> dict:
    return _rpc(url, "apply_agent_runtime_scheduled_finalization_v1", (
        claim["intent"]["scheduled_run_id"], claim["intent"]["claim_token"],
        claim["intent"]["state_version"], claim["task_schedule"]["state_version"],
        claim["task_schedule"]["schedule_hash"], request_id or str(uuid4()),
        "runtime_finalizer", next_run_at,
    ))


def _link_artifact(url: str, facts: dict, invalid: str | None = None) -> dict[str, str]:
    action_id, attempt_id, artifact_id = str(uuid4()), str(uuid4()), str(uuid4())
    link_hash, artifact_hash = "d" * 64, "d" * 64
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        session_id, step_id, org_id, user_id, conversation_id = conn.execute(
            "SELECT r.session_id,ms.id,r.org_id,r.user_id,s.conversation_id FROM agent_runs r "
            "JOIN agent_runtime_sessions s ON s.id=r.session_id JOIN agent_model_steps ms ON ms.run_id=r.id "
            "WHERE r.id=%s ORDER BY ms.step_number DESC LIMIT 1", (facts["run_id"],),
        ).fetchone()
        conn.execute("INSERT INTO agent_actions(id,session_id,run_id,model_step_id,org_id,user_id,"
                     "action_index,stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,"
                     "batch_hash,policy_decision,policy_snapshot,policy_revision,retry_disposition,status) "
                     "VALUES(%s,%s,%s,%s,%s,%s,0,%s,'artifact_test','{}',%s,%s,%s,'preauthorized',"
                     "'{}','v1','retry_safe','running')",
                     (action_id, session_id, facts["run_id"], step_id, org_id, user_id, action_id,
                      "a" * 64, "b" * 64, "c" * 64))
        conn.execute("INSERT INTO agent_action_attempts(id,action_id,session_id,run_id,org_id,user_id,"
                     "attempt_number,status,dispatch_phase,worker_id,execution_token,lease_expires_at,"
                     "idempotency_key,request_hash,retry_disposition) VALUES(%s,%s,%s,%s,%s,%s,1,"
                     "'dispatching','request_started','artifact-worker',%s,clock_timestamp()+interval '10 minutes',"
                     "%s,%s,'retry_safe')",
                     (attempt_id, action_id, session_id, facts["run_id"], org_id, user_id,
                      str(uuid4()), attempt_id, "b" * 64))
        link_attempt = attempt_id
        artifact_org, artifact_conversation = org_id, conversation_id
        if invalid == "hash_mismatch":
            artifact_hash = "e" * 64
        elif invalid == "wrong_attempt":
            other_action, link_attempt = str(uuid4()), str(uuid4())
            conn.execute("INSERT INTO agent_actions(id,session_id,run_id,model_step_id,org_id,user_id,"
                         "action_index,stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,"
                         "batch_hash,policy_decision,policy_snapshot,policy_revision,retry_disposition,status) "
                         "VALUES(%s,%s,%s,%s,%s,%s,1,%s,'artifact_other','{}',%s,%s,%s,'preauthorized',"
                         "'{}','v1','retry_safe','running')",
                         (other_action, session_id, facts["run_id"], step_id, org_id, user_id, other_action,
                          "a" * 64, "b" * 64, "c" * 64))
            conn.execute("INSERT INTO agent_action_attempts(id,action_id,session_id,run_id,org_id,user_id,"
                         "attempt_number,status,dispatch_phase,worker_id,execution_token,lease_expires_at,"
                         "idempotency_key,request_hash,retry_disposition) VALUES(%s,%s,%s,%s,%s,%s,1,"
                         "'dispatching','request_started','other-worker',%s,clock_timestamp()+interval '10 minutes',"
                         "%s,%s,'retry_safe')",
                         (link_attempt, other_action, session_id, facts["run_id"], org_id, user_id,
                          str(uuid4()), link_attempt, "b" * 64))
        elif invalid == "cross_tenant":
            artifact_org = str(uuid4())
            conn.execute("INSERT INTO organizations(id) VALUES(%s)", (artifact_org,))
        elif invalid == "wrong_conversation":
            artifact_conversation = str(uuid4())
            conn.execute("INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) "
                         "VALUES(%s,%s,%s,'user',%s)",
                         (artifact_conversation, user_id, org_id, str(user_id)))
        conn.execute("INSERT INTO conversation_artifacts(id,conversation_id,org_id,content_hash,storage_ref,"
                     "inline_content,sensitivity) VALUES(%s,%s,%s,%s,%s,%s,'restricted')",
                     (artifact_id, artifact_conversation, artifact_org, artifact_hash,
                      Jsonb({"path": "/private/secret", "url": "https://u:p@example.test"}),
                      Jsonb({"secret": "must-not-leak"})))
        conn.execute("INSERT INTO agent_action_artifact_links(action_id,attempt_id,artifact_id,role,"
                     "content_hash,materialize_status,sensitivity) VALUES(%s,%s,%s,'output',%s,"
                     "'materialized','restricted')", (action_id, link_attempt, artifact_id, link_hash))
        conn.commit()
    return {"artifact_id": artifact_id, "content_hash": link_hash}


def test_completed_projects_atomically_without_wallet_mutation(database: str) -> None:
    _prepare_b1b(database)
    facts = _bound_run(database)
    _install_final_result(database, facts)
    artifact = _link_artifact(database, facts)
    expected_summary = ("  real\n\tRuntime   summary " + "x" * 600)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        result_hash = conn.execute("SELECT encode(digest(convert_to(%s,'UTF8'),'sha256'),'hex')",
                                   (expected_summary,)).fetchone()[0]
        conn.execute("UPDATE agent_model_results SET text_content=%s,content_hash=%s WHERE run_id=%s",
                     (expected_summary, result_hash, facts["run_id"]))
        balance = conn.execute("SELECT credits FROM users WHERE id=(SELECT user_id FROM scheduled_tasks WHERE id=%s)",
                               (facts["task_id"],)).fetchone()[0]
        conn.commit()
    assert _rpc(database, "complete_agent_run", (
        facts["run_id"], facts["token"], facts["version"], result_hash,
    ))["outcome"] == "completed"
    claim = _claim(database)
    request_id = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        old_terminal = datetime.now(timezone.utc) - timedelta(minutes=10)
        conn.execute("UPDATE agent_runs SET completed_at=%s WHERE id=%s", (old_terminal, facts["run_id"]))
        conn.execute("ALTER TABLE agent_runtime_scheduled_finalization_intents DISABLE TRIGGER "
                     "runtime_scheduled_finalization_immutable")
        conn.execute("UPDATE agent_runtime_scheduled_finalization_intents SET created_at=%s WHERE scheduled_run_id=%s",
                     (old_terminal, facts["scheduled_run_id"]))
        conn.execute("ALTER TABLE agent_runtime_scheduled_finalization_intents ENABLE TRIGGER "
                     "runtime_scheduled_finalization_immutable")
        conn.commit()
    next_run = datetime.now(timezone.utc) - timedelta(minutes=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(
            lambda _: _apply_final(database, claim, next_run, request_id), range(2),
        ))
    assert sorted(item["outcome"] for item in outcomes) == ["already_applied", "applied"]
    applied = next(item for item in outcomes if item["outcome"] == "applied")
    assert applied["scheduled_run_status"] == "success"
    with psycopg.connect(database) as conn:
        run = conn.execute("SELECT status,credits_used,tokens_used,result_summary FROM scheduled_task_runs WHERE id=%s",
                           (facts["scheduled_run_id"],)).fetchone()
        task = conn.execute("SELECT status,run_count,consecutive_failures,last_result,runtime_state_version FROM scheduled_tasks WHERE id=%s",
                            (facts["task_id"],)).fetchone()
        assert conn.execute("SELECT credits FROM users WHERE id=(SELECT user_id FROM scheduled_tasks WHERE id=%s)",
                            (facts["task_id"],)).fetchone()[0] == balance
    assert run[:3] == ("success", 0, 10)
    assert run[3] == ("real Runtime summary " + "x" * 600)[:500]
    assert task[0:3] == ("active", 1, 0)
    assert task[3]["content_hash"] == result_hash
    assert task[3]["artifacts"] == [{
        "artifact_id": artifact["artifact_id"], "content_hash": artifact["content_hash"],
        "role": "output", "materialize_status": "materialized",
    }]
    assert set(task[3]["artifacts"][0]) == {"artifact_id", "content_hash", "role", "materialize_status"}
    with pytest.raises(Exception, match="IDEMPOTENCY_CONFLICT"):
        _apply_final(database, claim, next_run + timedelta(minutes=1), request_id)
    with pytest.raises(Exception, match="APPLICATION_FACTS_EXIST"):
        _apply(database, ROLLBACK)


@pytest.mark.parametrize("terminal,run_status", (("failed", "failed"), ("cancelled", "skipped")))
def test_failed_and_cancelled_projection_and_acl(database: str, terminal: str, run_status: str) -> None:
    _prepare_b1b(database)
    facts = _bound_run(database)
    rpc = "fail_agent_run" if terminal == "failed" else "test_b1a_cancel_agent_run"
    args = (facts["run_id"], facts["token"], facts["version"], "SAFE_FAILURE") if terminal == "failed" \
        else (facts["run_id"], facts["version"], "runtime_cancel")
    if terminal == "cancelled":
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("UPDATE agent_runtime_sessions SET scope_kind='user',scope_id=%s WHERE id=("
                         "SELECT session_id FROM agent_runs WHERE id=%s)", (USER, facts["run_id"]))
            conn.commit()
    terminal_result = _rpc(database, rpc, args) if terminal == "failed" else _request_rpc(database, rpc, args)
    assert terminal_result["outcome"] in ("failed", "cancelled")
    claim = _claim(database)
    with pytest.raises(Exception, match="CLAIM_FENCED"):
        _rpc(database, "apply_agent_runtime_scheduled_finalization_v1", (
            claim["intent"]["scheduled_run_id"], str(uuid4()), claim["intent"]["state_version"],
            claim["task_schedule"]["state_version"], claim["task_schedule"]["schedule_hash"],
            str(uuid4()), "runtime_finalizer", datetime.now(timezone.utc) + timedelta(minutes=5),
        ))
    with pytest.raises(Exception, match="STALE_VERSION"):
        _rpc(database, "apply_agent_runtime_scheduled_finalization_v1", (
            claim["intent"]["scheduled_run_id"], claim["intent"]["claim_token"],
            claim["intent"]["state_version"] + 1, claim["task_schedule"]["state_version"],
            claim["task_schedule"]["schedule_hash"], str(uuid4()), "runtime_finalizer",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        ))
    with pytest.raises(Exception, match="SCOPE_FENCED"):
        _rpc(database, "apply_agent_runtime_scheduled_finalization_v1", (
            claim["intent"]["scheduled_run_id"], claim["intent"]["claim_token"],
            claim["intent"]["state_version"], claim["task_schedule"]["state_version"],
            "f" * 64, str(uuid4()), "runtime_finalizer",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        ))
    with pytest.raises(Exception, match="SCOPE_FENCED"):
        _rpc(database, "apply_agent_runtime_scheduled_finalization_v1", (
            claim["intent"]["scheduled_run_id"], claim["intent"]["claim_token"],
            claim["intent"]["state_version"], claim["task_schedule"]["state_version"] + 1,
            claim["task_schedule"]["schedule_hash"], str(uuid4()), "runtime_finalizer",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        ))
    if terminal == "failed":
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("ALTER TABLE agent_runtime_scheduled_finalization_intents DISABLE TRIGGER "
                         "runtime_scheduled_finalization_immutable")
            conn.execute("UPDATE agent_runtime_scheduled_finalization_intents SET "
                         "claim_lease_expires_at=clock_timestamp()-interval '1 second' WHERE scheduled_run_id=%s",
                         (facts["scheduled_run_id"],))
            conn.execute("ALTER TABLE agent_runtime_scheduled_finalization_intents ENABLE TRIGGER "
                         "runtime_scheduled_finalization_immutable")
            conn.commit()
        with pytest.raises(Exception, match="CLAIM_FENCED"):
            _apply_final(database, claim, datetime.now(timezone.utc) + timedelta(minutes=5))
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("ALTER TABLE agent_runtime_scheduled_finalization_intents DISABLE TRIGGER "
                         "runtime_scheduled_finalization_immutable")
            conn.execute("UPDATE agent_runtime_scheduled_finalization_intents SET "
                         "claim_lease_expires_at=clock_timestamp()+interval '90 seconds' WHERE scheduled_run_id=%s",
                         (facts["scheduled_run_id"],))
            conn.execute("ALTER TABLE agent_runtime_scheduled_finalization_intents ENABLE TRIGGER "
                         "runtime_scheduled_finalization_immutable")
            conn.commit()
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        with pytest.raises(Exception, match="APPLY_RPC_REQUIRED"):
            conn.execute("UPDATE agent_runtime_scheduled_finalization_intents SET "
                         "application_request_id=%s,state_version=state_version+1 WHERE scheduled_run_id=%s",
                         (str(uuid4()), facts["scheduled_run_id"]))
        conn.rollback()
    outcome = _apply_final(database, claim, datetime.now(timezone.utc) + timedelta(minutes=5))
    assert outcome["scheduled_run_status"] == run_status
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT status FROM scheduled_task_runs WHERE id=%s",
                            (facts["scheduled_run_id"],)).fetchone()[0] == run_status
        assert conn.execute("SELECT has_table_privilege('everydayai_agent_runtime_worker',"
                            "'agent_runtime_scheduled_finalization_intents','UPDATE')").fetchone()[0] is False
        assert conn.execute("SELECT proconfig FROM pg_proc WHERE oid="
                            "'apply_agent_runtime_scheduled_finalization_v1(uuid,uuid,bigint,bigint,text,uuid,text,timestamptz)'::regprocedure").fetchone()[0] == ["search_path=pg_catalog, public"]


@pytest.mark.parametrize("invalid", ("hash_mismatch", "wrong_attempt", "cross_tenant", "wrong_conversation"))
def test_artifact_identity_conflicts_fail_closed(database: str, invalid: str) -> None:
    _prepare_b1b(database)
    facts = _bound_run(database)
    result_hash = _install_final_result(database, facts)
    _link_artifact(database, facts, invalid)
    assert _rpc(database, "complete_agent_run", (
        facts["run_id"], facts["token"], facts["version"], result_hash,
    ))["outcome"] == "completed"
    claim = _claim(database)
    with pytest.raises(Exception, match="ARTIFACT_FENCED"):
        _apply_final(database, claim, datetime.now(timezone.utc) + timedelta(minutes=5))


def test_text_and_structured_summary_redaction_contract(database: str) -> None:
    _prepare_b1b(database)
    cases = (
        ("text", "  销售额  120 万\n同比增长 8% ", None, "销售额 120 万 同比增长 8%"),
        ("text", "Authorization: Bearer live-secret", None, "Runtime scheduled task completed"),
        ("text", "结果 https://alice:password@example.test/report", None, "Runtime scheduled task completed"),
        ("text", "文件位于 /var/private/runtime/result.json", None, "Runtime scheduled task completed"),
        ("text", r"文件位于 C:\private\runtime\result.txt", None, "Runtime scheduled task completed"),
        ("structured", None, {"summary": "  库存 正常  "}, "库存 正常"),
        ("structured", None, {"summary": "Cookie: sid=private"}, "Runtime scheduled task completed"),
        ("structured", None, {"summary": {"nested": "not-a-string"}}, "Runtime scheduled task completed"),
    )
    for index, (kind, text, structured, expected) in enumerate(cases):
        facts = _bound_run(database)
        _install_final_result(database, facts)
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            source = text if kind == "text" else str(structured)
            result_hash = conn.execute(
                "SELECT encode(digest(convert_to(%s,'UTF8'),'sha256'),'hex')", (source,),
            ).fetchone()[0]
            if kind == "text":
                conn.execute("UPDATE agent_model_results SET output_kind='text',text_content=%s,"
                             "structured_content=NULL,schema_revision=NULL,content_hash=%s WHERE run_id=%s",
                             (text, result_hash, facts["run_id"]))
            else:
                conn.execute("UPDATE agent_model_results SET output_kind='structured',text_content=NULL,"
                             "structured_content=%s,schema_revision='v1',content_hash=%s WHERE run_id=%s",
                             (Jsonb(structured), result_hash, facts["run_id"]))
            conn.commit()
        assert _rpc(database, "complete_agent_run", (
            facts["run_id"], facts["token"], facts["version"], result_hash,
        ))["outcome"] == "completed"
        claim = _claim(database)
        _apply_final(database, claim, datetime.now(timezone.utc) + timedelta(minutes=5), str(uuid4()))
        with psycopg.connect(database) as conn:
            summary, last_summary = conn.execute(
                "SELECT q.result_summary,t.last_summary FROM scheduled_task_runs q "
                "JOIN scheduled_tasks t ON t.id=q.task_id WHERE q.id=%s",
                (facts["scheduled_run_id"],),
            ).fetchone()
        assert summary == expected, index
        assert last_summary == expected, index
        assert len(summary) <= 500
        assert "live-secret" not in summary and "private" not in summary.lower()


def test_rollback_guard_and_reapply(database: str) -> None:
    _prepare_b1b(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("TRUNCATE agent_runtime_scheduled_finalization_intents")
        conn.commit()
    _apply(database, ROLLBACK)
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)
