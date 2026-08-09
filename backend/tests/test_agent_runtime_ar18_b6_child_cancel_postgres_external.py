from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import (
    _apply, _seed_specialist_action, _worker_rpc,
)
from tests.test_agent_runtime_ar18_b5_sandbox_cancel_postgres_external import (
    _prepare as _prepare_b5,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "227_27_agent_runtime_child_run_recursive_cancel.sql"
ROLLBACK = "rollback/227_27_agent_runtime_child_run_recursive_cancel_rollback.sql"
ORG = "22222222-2222-2222-2222-222222222222"
USER = "44444444-4444-4444-4444-444444444444"


def _prepare(database: str) -> None:
    _prepare_b5(database)
    with psycopg.connect(database) as conn:
        conn.execute("ALTER ROLE everydayai_agent_model_gateway NOINHERIT")
        conn.commit()
    for name in (
        "227_18_agent_runtime_model_gateway.sql",
        "227_19_agent_runtime_model_gateway_predispatch_failure.sql",
        "227_20_agent_runtime_model_gateway_dispatch_binding.sql",
        "227_25_agent_runtime_model_gateway_cancel_fence.sql",
    ):
        _apply(database, name)
    _apply(database, MIGRATION)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("""
        CREATE FUNCTION test_b6_cancel_agent_run(UUID,BIGINT,TEXT) RETURNS JSONB
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public
        AS 'SELECT cancel_agent_run($1,$2,$3)';
        REVOKE ALL ON FUNCTION test_b6_cancel_agent_run(UUID,BIGINT,TEXT) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION test_b6_cancel_agent_run(UUID,BIGINT,TEXT)
          TO everydayai_runtime;
        """)
        conn.commit()


def _seed(database: str) -> dict[str, str]:
    conversation = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) "
            "VALUES(%s,%s,%s,'user',%s)",
            (conversation, USER, ORG, USER),
        )
        conn.commit()
    ids = _seed_specialist_action(database, conversation)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_actions SET tool_name='image_agent',arguments=%s,"
            "policy_snapshot=%s WHERE id=%s",
            (Jsonb({"child_ordinal": 0, "capability": "runtime.child"}),
             Jsonb({"capability": "runtime.child_run.create"}), ids["action"]),
        )
        conn.execute(
            "UPDATE agent_policy_receipts SET executor_type="
            "'runtime_child_run:image_agent' WHERE id=%s", (ids["policy"],),
        )
        conn.execute(
            "UPDATE agent_action_dispatch_intents SET executor_type="
            "'runtime_child_run:image_agent',recovery_mode='reconcile_only' "
            "WHERE attempt_id=%s", (ids["attempt"],),
        )
        conn.execute(
            "INSERT INTO agent_runtime_owner_fences(owner_kind,owner_id,org_id,"
            "execution_token,tenant_kill_epoch,state_version,status) "
            "VALUES('attempt',%s,%s,%s,0,1,'active')",
            (ids["attempt"], ORG, ids["token"]),
        )
        conn.commit()
    return ids


def _create(database: str, ids: dict[str, str]):
    return _worker_rpc(database, "create_agent_child_run_strict_v2", (
        ids["run"], ids["action"], ids["request_hash"], ids["token"],
        0, "runtime.child", {
            "policy_receipt_id": ids["policy"], "capability": "runtime.child",
            "budget_remaining": 1, "scope": {"org_id": ORG, "user_id": USER},
        },
    ))


def _cancel(database: str, ids: dict[str, str]):
    url = database.replace("postgres@", "everydayai_runtime@")
    with psycopg.connect(url) as conn:
        conn.execute("SELECT set_config('app.access_kind','runtime',false)")
        conn.execute("SELECT set_config('app.request_id','b6-cancel',false)")
        conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (USER,))
        conn.execute("SELECT set_config('app.org_id',%s,false)", (ORG,))
        result = conn.execute(
            "SELECT test_b6_cancel_agent_run(%s,0,'parent_cancelled')",
            (ids["run"],),
        ).fetchone()[0]
        conn.commit()
        return result


def _aggregate(database: str, ids: dict[str, str], child_id: str):
    return _worker_rpc(database, "aggregate_agent_child_run_strict_v2", (
        child_id, ids["run"], ids["action"], ids["request_hash"],
        ids["attempt"], ids["token"], 0, 1, {"items": []},
    ))


def _race(barrier: Barrier, call):
    barrier.wait()
    return call()


def _seed_child_action(database: str, *, child_run_id: str,
                       parent: dict[str, str], ordinal: int = 0) -> dict[str, str]:
    ids = {name: str(uuid4()) for name in (
        "step", "action", "attempt", "token", "policy",
    )}
    ids["run"] = child_run_id
    ids["request_hash"] = "e" * 64
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        run = conn.execute(
            "SELECT session_id,org_id,user_id FROM agent_runs WHERE id=%s",
            (child_run_id,),
        ).fetchone()
        conn.execute(
            "UPDATE agent_runs SET status='running',execution_token=%s,"
            "lease_expires_at=clock_timestamp()+interval '10 minutes',"
            "blocking_action_count=1 WHERE id=%s", (ids["token"], child_run_id),
        )
        conn.execute(
            "INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,"
            "step_number,model_id,provider,model_revision,prompt_revision,tool_catalog_revision) "
            "VALUES(%s,%s,%s,%s,%s,1,'fixture','fixture','v1','v1','v1')",
            (ids["step"], child_run_id, *run),
        )
        conn.execute(
            "INSERT INTO agent_actions(id,session_id,run_id,model_step_id,org_id,user_id,"
            "action_index,stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,"
            "batch_hash,policy_decision,policy_snapshot,policy_revision,retry_disposition,status) "
            "VALUES(%s,%s,%s,%s,%s,%s,0,%s,'image_agent',%s,%s,%s,%s,'preauthorized',"
            "%s,'v1','retry_after_reconcile','running')",
            (ids["action"], run[0], child_run_id, ids["step"], run[1], run[2],
             ids["action"], Jsonb({"child_ordinal": ordinal, "capability": "runtime.child"}),
             "f" * 64, ids["request_hash"], "1" * 64,
             Jsonb({"capability": "runtime.child_run.create"})),
        )
        conn.execute(
            "INSERT INTO agent_action_attempts(id,action_id,session_id,run_id,org_id,user_id,"
            "attempt_number,status,dispatch_phase,worker_id,execution_token,lease_expires_at,"
            "idempotency_key,request_hash,retry_disposition) VALUES(%s,%s,%s,%s,%s,%s,1,"
            "'dispatching','request_started','fixture-worker',%s,clock_timestamp()+interval "
            "'10 minutes',%s,%s,'retry_after_reconcile')",
            (ids["attempt"], ids["action"], run[0], child_run_id, run[1], run[2],
             ids["token"], ids["attempt"], ids["request_hash"]),
        )
        conn.execute(
            "INSERT INTO agent_policy_receipts(id,action_id,session_id,run_id,org_id,user_id,"
            "decision,arguments_hash,executor_type,executor_revision,policy_revision,"
            "effective_scope,reason_codes,receipt_hash,expires_at) VALUES(%s,%s,%s,%s,%s,%s,"
            "'allow',%s,'runtime_child_run:image_agent',1,'v1','{}',ARRAY['fixture'],%s,"
            "clock_timestamp()+interval '10 minutes')",
            (ids["policy"], ids["action"], run[0], child_run_id, run[1], run[2],
             "f" * 64, "2" * 64),
        )
        conn.execute(
            "INSERT INTO agent_action_dispatch_intents(attempt_id,action_id,policy_receipt_id,"
            "execution_token,request_hash,executor_type,executor_revision,policy_revision,"
            "external_idempotency_key,recovery_mode) VALUES(%s,%s,%s,%s,%s,"
            "'runtime_child_run:image_agent',1,'v1',%s,'reconcile_only')",
            (ids["attempt"], ids["action"], ids["policy"], ids["token"],
             ids["request_hash"], ids["attempt"]),
        )
        conn.execute(
            "INSERT INTO agent_runtime_owner_fences(owner_kind,owner_id,org_id,execution_token,"
            "tenant_kill_epoch,state_version,status) VALUES('attempt',%s,%s,%s,0,1,'active')",
            (ids["attempt"], run[1], ids["token"]),
        )
        conn.commit()
    ids["parent_action"] = parent["action"]
    return ids


def _claim_and_apply(database: str, worker: str) -> dict[str, object]:
    claim = _worker_rpc(
        database, "claim_next_agent_child_run_cancel_intent_v1", (worker, 120),
    )
    assert claim["outcome"] == "claimed"
    return _worker_rpc(database, "apply_agent_child_run_cancel_intent_v1", (
        claim["intent"]["id"], claim["intent"]["claim_token"],
        claim["intent"]["state_version"], "parent_cancelled",
    ))


def _finalize_child_action(database: str, ids: dict[str, str], worker: str) -> None:
    claim = _worker_rpc(database, "claim_next_agent_action_reconciliation", (
        worker, 120, 0,
    ))
    assert claim["operation"] == "cancel"
    assert claim["attempt_id"] == ids["attempt"]
    proof = _worker_rpc(database, "read_agent_child_run_cancel_intent_v1", (
        ids["action"], ids["attempt"], claim["execution_token"],
        claim["state_version"], ids["request_hash"],
    ))
    assert proof["outcome"] == "confirmed"
    with pytest.raises(Exception, match="CHILD_CANCEL_FINALIZE_FENCED"):
        _worker_rpc(database, "finalize_agent_action_child_cancel_v1", (
            ids["attempt"], claim["execution_token"], claim["state_version"],
            ids["request_hash"], proof["intent_id"], "0" * 64,
        ))
    finalized = _worker_rpc(database, "finalize_agent_action_child_cancel_v1", (
        ids["attempt"], claim["execution_token"], claim["state_version"],
        ids["request_hash"], proof["intent_id"], proof["proof_hash"],
    ))
    assert finalized["outcome"] == "cancelled"


def test_b6_cancel_create_race_converges_and_finalizes_parent(database: str) -> None:
    _prepare(database)
    ids = _seed(database)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (
            pool.submit(_race, barrier, lambda: _create(database, ids)),
            pool.submit(_race, barrier, lambda: _cancel(database, ids)),
        )
        created, cancelled = (future.result() for future in futures)
    assert created["outcome"] in {"created", "cancel_fenced"}
    assert cancelled["outcome"] == "cancelled"

    with psycopg.connect(database) as conn:
        intent_status = conn.execute(
            "SELECT status FROM agent_runtime_child_run_cancel_intents "
            "WHERE parent_action_id=%s", (ids["action"],),
        ).fetchone()[0]
    if intent_status != "confirmed":
        first = _worker_rpc(
            database, "claim_next_agent_child_run_cancel_intent_v1",
            ("child-cancel-a", 120),
        )
        assert first["outcome"] == "claimed"
        applied = _worker_rpc(database, "apply_agent_child_run_cancel_intent_v1", (
            first["intent"]["id"], first["intent"]["claim_token"],
            first["intent"]["state_version"], "parent_cancelled",
        ))
        assert applied["outcome"] == "confirmed"
        assert applied["terminal_kind"] == "cancelled"

    claim = _worker_rpc(database, "claim_next_agent_action_reconciliation", (
        "parent-action-cancel", 120, 0,
    ))
    assert claim["operation"] == "cancel"
    proof = _worker_rpc(database, "read_agent_child_run_cancel_intent_v1", (
        ids["action"], ids["attempt"], claim["execution_token"],
        claim["state_version"], ids["request_hash"],
    ))
    assert proof["outcome"] == "confirmed"
    finalized = _worker_rpc(database, "finalize_agent_action_child_cancel_v1", (
        ids["attempt"], claim["execution_token"], claim["state_version"],
        ids["request_hash"], proof["intent_id"], proof["proof_hash"],
    ))
    assert finalized["outcome"] == "cancelled"
    assert finalized["blocking_action_count"] == 0
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT status FROM agent_runs WHERE id=%s", (ids["run"],),
        ).fetchone()[0] == "cancelled"
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_child_run_cancel_intents "
            "WHERE parent_action_id=%s", (ids["action"],),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM agent_action_cost_settlements WHERE action_id=%s",
            (ids["action"],),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_events WHERE correlation_id=%s "
            "AND event_type='action.cancelled'", (ids["action"],),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE "
            "oid='agent_runtime_child_run_cancel_intents'::regclass",
        ).fetchone() == (True, True)
        assert conn.execute(
            "SELECT has_table_privilege('everydayai_agent_runtime_worker',"
            "'agent_runtime_child_run_cancel_intents','SELECT')",
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_agent_runtime_worker',"
            "'create_agent_child_run_strict(uuid,uuid,text,uuid,integer,text,jsonb)',"
            "'EXECUTE')",
        ).fetchone()[0] is False
        for signature in (
            "create_agent_child_run_strict_v2(uuid,uuid,text,uuid,integer,text,jsonb)",
            "claim_next_agent_child_run_cancel_intent_v1(text,integer)",
            "apply_agent_child_run_cancel_intent_v1(uuid,uuid,bigint,text)",
            "finalize_agent_action_child_cancel_v1(uuid,uuid,bigint,text,uuid,text)",
        ):
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_agent_runtime_worker',%s,'EXECUTE')",
                (signature,),
            ).fetchone()[0] is True
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_worker',%s,'EXECUTE')",
                (signature,),
            ).fetchone()[0] is False
            assert conn.execute(
                "SELECT prosecdef AND proconfig@>ARRAY['search_path=pg_catalog, public'] "
                "FROM pg_proc WHERE oid=to_regprocedure(%s)", (signature,),
            ).fetchone()[0] is True
    with pytest.raises(Exception, match="ROLLBACK_PENDING_FACTS"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("DELETE FROM agent_runtime_child_run_cancel_intents")
        conn.commit()
    _apply(database, ROLLBACK)
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)


def test_b6_expired_scanner_claim_has_one_takeover_and_fences_old_token(database: str) -> None:
    _prepare(database)
    ids = _seed(database)
    assert _create(database, ids)["outcome"] == "created"
    assert _cancel(database, ids)["outcome"] == "cancelled"
    original = _worker_rpc(
        database, "claim_next_agent_child_run_cancel_intent_v1", ("crashed", 120),
    )
    assert original["outcome"] == "claimed"
    replay = _worker_rpc(
        database, "claim_next_agent_child_run_cancel_intent_v1", ("crashed", 120),
    )
    assert replay["intent"]["id"] == original["intent"]["id"]
    assert replay["intent"]["claim_token"] == original["intent"]["claim_token"]
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runtime_child_run_cancel_intents SET "
            "claim_lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (original["intent"]["id"],),
        )
        conn.commit()
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (
            pool.submit(_race, barrier, lambda: _worker_rpc(
                database, "claim_next_agent_child_run_cancel_intent_v1", ("takeover-a", 120),
            )),
            pool.submit(_race, barrier, lambda: _worker_rpc(
                database, "claim_next_agent_child_run_cancel_intent_v1", ("takeover-b", 120),
            )),
        )]
    assert sorted(item["outcome"] for item in results) == ["claimed", "not_found"]
    winner = next(item for item in results if item["outcome"] == "claimed")
    stale = _worker_rpc(database, "apply_agent_child_run_cancel_intent_v1", (
        original["intent"]["id"], original["intent"]["claim_token"],
        original["intent"]["state_version"], "stale-worker",
    ))
    assert stale["outcome"] == "ownership_lost"
    confirmed = _worker_rpc(database, "apply_agent_child_run_cancel_intent_v1", (
        winner["intent"]["id"], winner["intent"]["claim_token"],
        winner["intent"]["state_version"], "takeover",
    ))
    assert confirmed["outcome"] == "confirmed"
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT status FROM agent_runs WHERE parent_action_id=%s", (ids["action"],),
        ).fetchone()[0] == "cancelled"


def test_b6_cancel_and_terminal_aggregate_have_one_atomic_winner(database: str) -> None:
    _prepare(database)
    ids = _seed(database)
    child = _create(database, ids)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runs SET status='completed',completed_at=clock_timestamp(),"
            "child_terminal_result='{}',result_hash=%s,aggregation_revision=1 WHERE id=%s",
            ("9" * 64, child["child_run_id"]),
        )
        conn.commit()
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        aggregate, cancel = [future.result() for future in (
            pool.submit(_race, barrier, lambda: _aggregate(
                database, ids, child["child_run_id"],
            )),
            pool.submit(_race, barrier, lambda: _cancel(database, ids)),
        )]
    assert (aggregate["outcome"], cancel["outcome"]) in {
        ("completed", "terminal_conflict"),
        ("cancel_pending", "cancelled"),
        ("fenced", "cancelled"),
    }
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_child_run_cancel_intents "
            "WHERE parent_action_id=%s", (ids["action"],),
        ).fetchone()[0] in {0, 1}


def test_b6_terminal_child_with_unsettled_action_cannot_confirm(database: str) -> None:
    _prepare(database)
    root = _seed(database)
    child = _create(database, root)
    _seed_child_action(database, child_run_id=child["child_run_id"], parent=root)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runs SET status='completed',completed_at=clock_timestamp(),"
            "execution_token=NULL,lease_expires_at=NULL,blocking_action_count=0,"
            "child_terminal_result='{}',result_hash=%s,aggregation_revision=1 WHERE id=%s",
            ("8" * 64, child["child_run_id"]),
        )
        conn.commit()
    assert _cancel(database, root)["outcome"] == "cancelled"
    result = _claim_and_apply(database, "terminal-child-pending")
    assert result["outcome"] == "applied"
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT status,terminal_kind,proof_hash FROM "
            "agent_runtime_child_run_cancel_intents WHERE parent_action_id=%s",
            (root["action"],),
        ).fetchone() == ("applied", None, None)


def test_b6_three_level_tree_converges_bottom_up_without_forced_unknown(database: str) -> None:
    _prepare(database)
    root = _seed(database)
    child = _create(database, root)
    assert child["outcome"] == "created"
    child_action = _seed_child_action(
        database, child_run_id=child["child_run_id"], parent=root,
    )
    grandchild = _create(database, child_action)
    assert grandchild["outcome"] == "created"
    assert _cancel(database, root)["outcome"] == "cancelled"

    root_pending = _claim_and_apply(database, "root-intent-first")
    assert root_pending["outcome"] == "applied"
    descendant = _claim_and_apply(database, "grandchild-intent")
    assert descendant["outcome"] == "confirmed"
    assert descendant["terminal_kind"] == "cancelled"
    with psycopg.connect(database) as conn:
        root_fact = conn.execute(
            "SELECT status,terminal_kind FROM agent_runtime_child_run_cancel_intents "
            "WHERE parent_action_id=%s", (root["action"],),
        ).fetchone()
        assert root_fact == ("applied", None)
        assert conn.execute(
            "SELECT status FROM agent_action_attempts WHERE id=%s",
            (child_action["attempt"],),
        ).fetchone()[0] == "unknown"
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_action_attempts SET updated_at=clock_timestamp()+interval "
            "'10 minutes' WHERE id=%s", (root["attempt"],),
        )
        conn.commit()

    _finalize_child_action(database, child_action, "child-action-finalizer")
    root_confirmed = _claim_and_apply(database, "root-intent-final")
    assert root_confirmed["outcome"] == "confirmed"
    assert root_confirmed["terminal_kind"] == "cancelled"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_action_attempts SET updated_at=clock_timestamp() "
            "WHERE id=%s", (root["attempt"],),
        )
        conn.commit()
    _finalize_child_action(database, root, "root-action-finalizer")
    with psycopg.connect(database) as conn:
        rows = conn.execute(
            "SELECT status,terminal_kind FROM agent_runtime_child_run_cancel_intents "
            "ORDER BY parent_run_id",
        ).fetchall()
        assert rows == [("confirmed", "cancelled"), ("confirmed", "cancelled")]
        assert conn.execute(
            "SELECT count(*) FROM agent_runs WHERE status<>'cancelled' AND id IN(%s,%s,%s)",
            (root["run"], child["child_run_id"], grandchild["child_run_id"]),
        ).fetchone()[0] == 0
