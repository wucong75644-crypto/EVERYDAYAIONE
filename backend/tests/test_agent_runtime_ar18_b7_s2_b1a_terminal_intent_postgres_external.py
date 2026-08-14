from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import _rpc
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import (
    ORG, USER, _create_runtime_task, _scheduled_command_run, _scheduled_run, _select,
)
from tests.test_agent_runtime_ar18_b7_s2_a2_submission_postgres_external import (
    _enable, _prepare_a2, _prepare_runtime_due, _worker_rpc,
)


pytestmark = pytest.mark.external
MIGRATION = "227_31_agent_runtime_scheduled_terminal_intents.sql"
ROLLBACK = "rollback/227_31_agent_runtime_scheduled_terminal_intents_rollback.sql"


def _prepare(url: str) -> None:
    _prepare_a2(url)
    _apply(url, MIGRATION)
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "CREATE FUNCTION test_b1a_cancel_agent_run(UUID,BIGINT,TEXT) RETURNS JSONB "
            "LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public "
            "AS 'SELECT cancel_agent_run($1,$2,$3)'"
        )
        conn.execute("REVOKE ALL ON FUNCTION test_b1a_cancel_agent_run(UUID,BIGINT,TEXT) FROM PUBLIC")
        conn.execute("GRANT EXECUTE ON FUNCTION test_b1a_cancel_agent_run(UUID,BIGINT,TEXT) "
                     "TO everydayai_runtime")
        conn.commit()


def _request_rpc(url: str, name: str, args: tuple):
    with psycopg.connect(url.replace("postgres@", "everydayai_runtime@")) as conn:
        conn.execute("SELECT set_config('app.access_kind','runtime',false)")
        conn.execute("SELECT set_config('app.request_id',%s,false)", (str(uuid4()),))
        conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (USER,))
        conn.execute("SELECT set_config('app.org_id',%s,false)", (ORG,))
        return conn.execute(
            f"SELECT {name}({','.join(['%s'] * len(args))})", args,
        ).fetchone()[0]


def _bound_run(url: str) -> dict[str, str | int]:
    _enable(url)
    task_id = _prepare_runtime_due(url)
    [submission] = _worker_rpc(
        url, "worker_claim_due_scheduled_executions_v1",
        (datetime.now(timezone.utc), 5),
    )
    command_id = submission["command_id"]
    claimed_command = None
    for _ in range(20):
        candidate = _rpc(
            url, "claim_pending_agent_command_and_ensure_run", ("b1a-command", 90, 3),
        )
        if candidate.get("command_id") == command_id:
            claimed_command = candidate
            break
    assert claimed_command is not None
    run_claim = _rpc(url, "claim_agent_run", (
        claimed_command["run_id"], "b1a-runtime", 90, 3,
    ))
    assert run_claim["outcome"] == "claimed"
    return {
        "task_id": task_id,
        "scheduled_run_id": submission["binding"]["scheduled_run_id"],
        "command_id": command_id,
        "run_id": claimed_command["run_id"],
        "token": run_claim["execution_token"],
        "version": run_claim["state_version"],
    }


def _install_final_result(url: str, facts: dict[str, str | int]) -> str:
    step_id, result_id = str(uuid4()), str(uuid4())
    text = "scheduled result"
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        row = conn.execute(
            "SELECT session_id,org_id,user_id FROM agent_runs WHERE id=%s",
            (facts["run_id"],),
        ).fetchone()
        result_hash = conn.execute(
            "SELECT encode(digest(convert_to(%s,'UTF8'),'sha256'),'hex')", (text,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,step_number,"
            "status,model_id,provider,model_revision,prompt_revision,tool_catalog_revision,"
            "response_receipt,stop_reason,input_tokens,output_tokens,reasoning_tokens,"
            "state_version,completed_at) VALUES(%s,%s,%s,%s,%s,1,'completed','test-model',"
            "'test-provider','v1','v1','v1','{}','final',3,5,2,1,clock_timestamp())",
            (step_id, facts["run_id"], row[0], row[1], row[2]),
        )
        conn.execute(
            "INSERT INTO agent_model_results(id,model_step_id,run_id,session_id,org_id,user_id,"
            "output_kind,text_content,content_hash) VALUES(%s,%s,%s,%s,%s,%s,'text',%s,%s)",
            (result_id, step_id, facts["run_id"], row[0], row[1], row[2], text, result_hash),
        )
        conn.commit()
    return result_hash


def _intent_count(url: str, run_id: str) -> int:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_finalization_intents "
            "WHERE runtime_run_id=%s", (run_id,),
        ).fetchone()[0]


@pytest.mark.parametrize("terminal", ("completed", "failed", "cancelled"))
def test_real_run_terminal_rpcs_capture_one_intent(database: str, terminal: str) -> None:
    _prepare(database)
    facts = _bound_run(database)
    if terminal == "completed":
        result_hash = _install_final_result(database, facts)
        result = _rpc(database, "complete_agent_run", (
            facts["run_id"], facts["token"], facts["version"], result_hash,
        ))
    elif terminal == "failed":
        result = _rpc(database, "fail_agent_run", (
            facts["run_id"], facts["token"], facts["version"],
            "SAFE_FAILURE_CODE token=private-value",
        ))
    else:
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute(
                "UPDATE agent_runtime_sessions SET scope_kind='user',scope_id=%s WHERE id=("
                "SELECT session_id FROM agent_runs WHERE id=%s)", (USER, facts["run_id"]),
            )
            conn.commit()
        result = _request_rpc(database, "test_b1a_cancel_agent_run", (
            facts["run_id"], facts["version"], "runtime_cancel",
        ))
    assert result["outcome"] == terminal
    assert _intent_count(database, str(facts["run_id"])) == 1
    with psycopg.connect(database) as conn:
        intent = conn.execute(
            "SELECT terminal_status,terminal_reason,runtime_run_state_version FROM "
            "agent_runtime_scheduled_finalization_intents WHERE runtime_run_id=%s",
            (facts["run_id"],),
        ).fetchone()
        binding = conn.execute(
            "SELECT owner_status FROM agent_runtime_scheduled_run_bindings "
            "WHERE scheduled_run_id=%s", (facts["scheduled_run_id"],),
        ).fetchone()[0]
    assert intent[0] == terminal and intent[2] == result["state_version"]
    assert "private-value" not in intent[1]
    assert binding == "reconcile_required"


def test_terminal_reason_allowlist_blocks_structured_and_free_text_secrets(database: str) -> None:
    _prepare(database)
    cases = (
        ("Authorization: Bearer sk-live-authorization", "redacted_terminal_reason"),
        ('{"api_key":"sk-live-json"}', "redacted_terminal_reason"),
        ("Cookie: session=private-cookie", "redacted_terminal_reason"),
        ("https://alice:private-password@example.com/private", "redacted_terminal_reason"),
        ("ordinary free text failure detail", "redacted_terminal_reason"),
        ("provider_error", "provider_error"),
    )
    for index, (source_reason, expected_reason) in enumerate(cases):
        facts = _bound_run(database)
        result = _rpc(database, "fail_agent_run", (
            facts["run_id"], facts["token"], facts["version"], source_reason,
        ))
        assert result["outcome"] == "failed"
        claimed = _rpc(
            database, "claim_next_agent_runtime_scheduled_finalization_v1",
            (f"reason-worker-{index}", 90),
        )
        assert claimed["outcome"] == "claimed"
        assert claimed["intent"]["scheduled_run_id"] == facts["scheduled_run_id"]
        assert claimed["intent"]["terminal_reason"] == expected_reason
        readback = _rpc(database, "read_agent_runtime_scheduled_finalization_v1", (
            facts["scheduled_run_id"], claimed["intent"]["claim_token"],
        ))
        assert readback["outcome"] == "found"
        assert readback["intent"]["terminal_reason"] == expected_reason
        if expected_reason == "redacted_terminal_reason":
            assert source_reason not in str(claimed)
            assert source_reason not in str(readback)


def test_attempts_exhausted_and_nonterminal_action_do_not_lose_or_invent_facts(database: str) -> None:
    _prepare(database)
    facts = _bound_run(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        action_id, attempt_id = conn.execute(
            "SELECT action.id,attempt.id FROM agent_actions action JOIN agent_action_attempts attempt "
            "ON attempt.action_id=action.id ORDER BY action.created_at LIMIT 1"
        ).fetchone()
        conn.execute(
            "UPDATE agent_actions SET status='accepted',completed_at=NULL,accepted_at=clock_timestamp(),"
            "state_version=state_version+1 WHERE id=%s", (action_id,),
        )
        conn.execute(
            "UPDATE agent_action_attempts SET status='accepted',dispatch_phase='accepted',"
            "external_receipt='{\"receipt\":\"accepted\"}',accepted_at=clock_timestamp(),ended_at=NULL,"
            "state_version=state_version+1 WHERE id=%s", (attempt_id,),
        )
        conn.commit()
    assert _intent_count(database, str(facts["run_id"])) == 0
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_actions SET status='unknown',state_version=state_version+1 WHERE id=%s",
            (action_id,),
        )
        conn.execute(
            "UPDATE agent_action_attempts SET status='unknown',ambiguity_evidence="
            "'{\"reason\":\"response_lost\"}',state_version=state_version+1 WHERE id=%s",
            (attempt_id,),
        )
        conn.commit()
    assert _intent_count(database, str(facts["run_id"])) == 0
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runs SET status='queued',execution_token=NULL,lease_expires_at=NULL,"
            "state_version=state_version+1 WHERE id=%s", (facts["run_id"],),
        )
        conn.execute(
            "UPDATE agent_command_claims SET lease_expires_at=clock_timestamp()-interval '1 second',"
            "attempt_number=3 WHERE command_id=%s", (facts["command_id"],),
        )
        conn.commit()
    assert _intent_count(database, str(facts["run_id"])) == 0
    exhausted = _rpc(
        database, "claim_pending_agent_command_and_ensure_run", ("exhausted", 90, 3),
    )
    assert exhausted["outcome"] == "attempts_exhausted"
    assert _intent_count(database, str(facts["run_id"])) == 1
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT terminal_reason FROM agent_runtime_scheduled_finalization_intents "
            "WHERE runtime_run_id=%s", (facts["run_id"],),
        ).fetchone()[0] == "command_attempts_exhausted"
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_scheduled_finalization_intents i JOIN "
            "agent_runs r ON r.id=i.runtime_run_id WHERE r.status NOT IN"
            "('completed','failed','cancelled')"
        ).fetchone()[0] == 0


def test_claim_single_winner_expiry_readback_and_actor_fences(database: str) -> None:
    _prepare(database)
    facts = _bound_run(database)
    _rpc(database, "fail_agent_run", (
        facts["run_id"], facts["token"], facts["version"], "SAFE_FAILURE",
    ))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda worker: _rpc(
                database, "claim_next_agent_runtime_scheduled_finalization_v1", (worker, 15),
            ), ("finalizer-a", "finalizer-b"),
        ))
    claimed = [item for item in results if item["outcome"] == "claimed"]
    assert len(claimed) == 1
    winner = claimed[0]
    assert winner["intent"]["attempt_count"] == 1
    before = winner["intent"]["state_version"]
    fenced = _rpc(database, "read_agent_runtime_scheduled_finalization_v1", (
        facts["scheduled_run_id"], str(uuid4()),
    ))
    assert fenced == {"outcome": "fenced"}
    readback = _rpc(database, "read_agent_runtime_scheduled_finalization_v1", (
        facts["scheduled_run_id"], winner["intent"]["claim_token"],
    ))
    assert readback["outcome"] == "found"
    assert readback["intent"]["state_version"] == before
    assert readback["usage_projection_input"]["total_tokens"] == 0
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("ALTER TABLE agent_runtime_scheduled_finalization_intents DISABLE TRIGGER "
                     "runtime_scheduled_finalization_immutable")
        conn.execute(
            "UPDATE agent_runtime_scheduled_finalization_intents SET "
            "claim_lease_expires_at=clock_timestamp()-interval '1 second' WHERE scheduled_run_id=%s",
            (facts["scheduled_run_id"],),
        )
        conn.execute("ALTER TABLE agent_runtime_scheduled_finalization_intents ENABLE TRIGGER "
                     "runtime_scheduled_finalization_immutable")
        conn.commit()
    reclaimed = _rpc(
        database, "claim_next_agent_runtime_scheduled_finalization_v1", ("finalizer-c", 15),
    )
    assert reclaimed["outcome"] == "claimed"
    assert reclaimed["intent"]["attempt_count"] == 2
    assert reclaimed["intent"]["claim_token"] != winner["intent"]["claim_token"]
    with psycopg.connect(database.replace("postgres@", "everydayai_worker@")) as conn:
        conn.execute("SELECT set_config('app.access_kind','worker',false)")
        conn.execute("SELECT set_config('app.request_id',%s,false)", (str(uuid4()),))
        with pytest.raises(Exception, match="permission denied"):
            conn.execute("SELECT claim_next_agent_runtime_scheduled_finalization_v1('legacy',15)")


def test_backfill_failure_closed_acl_identity_and_disposable_rollback(database: str) -> None:
    _prepare_a2(database)
    _enable(database)
    facts = _bound_run(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runs SET status='failed',execution_token=NULL,lease_expires_at=NULL,"
            "completed_at=clock_timestamp(),terminal_reason='preexisting',state_version=state_version+1 "
            "WHERE id=%s", (facts["run_id"],),
        )
        conn.commit()
    _apply(database, MIGRATION)
    assert _intent_count(database, str(facts["run_id"])) == 1
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE "
            "oid='agent_runtime_scheduled_finalization_intents'::regclass"
        ).fetchone() == (True, True)
        assert conn.execute(
            "SELECT NOT EXISTS(SELECT 1 FROM aclexplode(relacl) WHERE grantee=0 "
            "AND privilege_type='SELECT') FROM pg_class WHERE "
            "oid='agent_runtime_scheduled_finalization_intents'::regclass"
        ).fetchone()[0] is True
        for role in ("everydayai_worker", "everydayai_runtime",
                     "everydayai_agent_runtime_worker"):
            assert conn.execute(
                "SELECT has_table_privilege(%s,'agent_runtime_scheduled_finalization_intents','SELECT')",
                (role,),
            ).fetchone()[0] is False
        for signature in (
            "claim_next_agent_runtime_scheduled_finalization_v1(text,integer)",
            "read_agent_runtime_scheduled_finalization_v1(uuid,uuid)",
        ):
            assert conn.execute(
                "SELECT proconfig FROM pg_proc WHERE oid=%s::regprocedure", (signature,),
            ).fetchone()[0] == ["search_path=pg_catalog, public"]
        conn.execute("SET ROLE everydayai_owner")
        with pytest.raises(Exception, match="IDENTITY_IMMUTABLE"):
            conn.execute(
                "UPDATE agent_runtime_scheduled_finalization_intents SET terminal_status='cancelled',"
                "state_version=state_version+1 WHERE runtime_run_id=%s", (facts["run_id"],),
            )
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        with pytest.raises(Exception, match="CLAIM_RPC_REQUIRED"):
            conn.execute(
                "UPDATE agent_runtime_scheduled_finalization_intents SET status='claimed',"
                "claim_worker_id='direct-owner',claim_token=gen_random_uuid(),"
                "claim_lease_expires_at=clock_timestamp()+interval '1 minute',"
                "state_version=state_version+1 WHERE runtime_run_id=%s", (facts["run_id"],),
            )
    with pytest.raises(Exception, match="ROLLBACK_FACTS_EXIST"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("TRUNCATE agent_runtime_scheduled_finalization_intents")
        conn.execute("ALTER TABLE agent_runs DISABLE TRIGGER capture_runtime_scheduled_terminal_intent")
        conn.execute(
            "UPDATE agent_runs SET status='queued',completed_at=NULL,terminal_reason=NULL,"
            "state_version=state_version+1 WHERE id=%s", (facts["run_id"],),
        )
        conn.execute("ALTER TABLE agent_runs ENABLE TRIGGER capture_runtime_scheduled_terminal_intent")
        conn.commit()
    _apply(database, ROLLBACK)
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)


@pytest.mark.parametrize("binding_case", ("missing", "conflicting"))
def test_missing_terminal_binding_and_backfill_conflict_fail_closed(
    database: str, binding_case: str,
) -> None:
    _prepare_a2(database)
    task_id, _ = _create_runtime_task(database)
    scheduled_run = _scheduled_run(database, task_id)
    _select(database, task_id, scheduled_run)
    command_id, run_id = _scheduled_command_run(database, task_id, scheduled_run)
    other_user = str(uuid4())
    if binding_case == "conflicting":
        assert _rpc(database, "bind_agent_runtime_scheduled_run_runtime_v1", (
            scheduled_run, command_id, run_id, 0,
        ))["outcome"] == "bound"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        if binding_case == "conflicting":
            conn.execute("INSERT INTO users(id) VALUES(%s)", (other_user,))
            conn.execute("UPDATE agent_runs SET user_id=%s WHERE id=%s", (other_user, run_id))
        conn.execute(
            "UPDATE agent_runs SET status='failed',completed_at=clock_timestamp(),"
            "terminal_reason='unbound',state_version=state_version+1 WHERE id=%s", (run_id,),
        )
        conn.commit()
    with pytest.raises(Exception, match="BACKFILL_CONFLICT"):
        _apply(database, MIGRATION)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT to_regclass('agent_runtime_scheduled_finalization_intents')"
        ).fetchone()[0] is None
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runs SET status='queued',completed_at=NULL,terminal_reason=NULL,"
            "user_id=%s,state_version=state_version+1 WHERE id=%s", (USER, run_id),
        )
        conn.commit()
    _apply(database, MIGRATION)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        if binding_case == "conflicting":
            conn.execute("UPDATE agent_runs SET user_id=%s WHERE id=%s", (other_user, run_id))
        with pytest.raises(Exception, match="TERMINAL_BINDING_REQUIRED"):
            conn.execute(
                "UPDATE agent_runs SET status='failed',completed_at=clock_timestamp(),"
                "terminal_reason='still-unbound',state_version=state_version+1 WHERE id=%s",
                (run_id,),
            )
