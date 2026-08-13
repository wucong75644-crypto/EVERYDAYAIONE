from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests import (
    test_agent_runtime_action_hash_canonicalization_postgres_external
    as canonicalization,
)
pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION_228_01 = ROOT / "migrations/228_01_agent_runtime_action_hash_canonicalization.sql"
MIGRATION = ROOT / "migrations/228_03_agent_runtime_media_authorization_group.sql"
ROLLBACK = ROOT / (
    "migrations/rollback/228_03_agent_runtime_media_authorization_group_rollback.sql"
)
ORG = UUID("22222222-2222-2222-2222-222222222222")
USER = UUID("44444444-4444-4444-4444-444444444444")


def _apply(database_url: str, path: Path) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(path.read_text(encoding="utf-8"))


def _connect(database_url: str, role: str, access_kind: str, *,
             user: UUID = USER, org: UUID = ORG) -> psycopg.Connection:
    connection = psycopg.connect(database_url.replace("postgres@", f"{role}@"))
    connection.execute(
        "SELECT set_config('app.actor_user_id',%s,false)", (str(user),),
    )
    connection.execute(
        "SELECT set_config('app.org_id',%s,false)", (str(org),),
    )
    connection.execute(
        "SELECT set_config('app.access_kind',%s,false)", (access_kind,),
    )
    connection.execute(
        "SELECT set_config('app.request_id','batch-auth-test',false)",
    )
    return connection


def _value(connection: psycopg.Connection, sql: str, params=()):
    return connection.execute(sql, params).fetchone()[0]


def _seed_batch(database_url: str, count: int) -> dict[str, object]:
    ids = {name: uuid4() for name in (
        "conversation", "message", "task", "session", "command", "run", "step",
    )}
    ids["actions"] = [uuid4() for _ in range(count)]
    batch_hash = hashlib.sha256(f"batch:{ids['run']}".encode()).hexdigest()
    ids["batch_hash"] = batch_hash
    members = []
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) "
            "VALUES(%s,%s,%s,'user',%s)",
            (ids["conversation"], USER, ORG, str(USER)),
        )
        connection.execute(
            "INSERT INTO messages(id,conversation_id,org_id,role,content) "
            "VALUES(%s,%s,%s,'user','batch')",
            (ids["message"], ids["conversation"], ORG),
        )
        connection.execute(
            "INSERT INTO tasks(id,user_id,org_id,conversation_id,type,status,"
            "input_message_id,model_id,delivery_context) "
            "VALUES(%s,%s,%s,%s,'chat','pending',%s,'qwen','{}')",
            (ids["task"], USER, ORG, ids["conversation"], ids["message"]),
        )
        connection.execute(
            "INSERT INTO agent_runtime_sessions(id,conversation_id,org_id,user_id,"
            "scope_kind,scope_id,created_by_user_id,agent_definition_id,"
            "agent_definition_revision) VALUES(%s,%s,%s,%s,'user',%s,%s,"
            "'everydayai-default','v7')",
            (ids["session"], ids["conversation"], ORG, USER, str(USER), USER),
        )
        connection.execute(
            "INSERT INTO agent_session_commands(id,session_id,org_id,user_id,"
            "command_type,idempotency_key,payload,request_hash) "
            "VALUES(%s,%s,%s,%s,'submit_input',%s,%s,%s)",
            (ids["command"], ids["session"], ORG, USER,
             f"command:{ids['command']}", Jsonb({"task_id": str(ids["task"])}),
             "1" * 32),
        )
        connection.execute(
            "INSERT INTO agent_runs(id,session_id,command_id,org_id,user_id,"
            "run_kind,status,idempotency_key,request_hash,blocking_action_count) "
            "VALUES(%s,%s,%s,%s,%s,'user','waiting_actions',%s,%s,%s)",
            (ids["run"], ids["session"], ids["command"], ORG, USER,
             f"run:{ids['run']}", "2" * 32, count),
        )
        connection.execute(
            "INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,"
            "step_number,status,model_id,provider,model_revision,prompt_revision,"
            "tool_catalog_revision,stop_reason,completed_at) "
            "VALUES(%s,%s,%s,%s,%s,1,'completed','model','provider','v1',"
            "'batch-media-v1','catalog-v7','tool_calls',clock_timestamp())",
            (ids["step"], ids["run"], ids["session"], ORG, USER),
        )
        for index, action_id in enumerate(ids["actions"]):
            arguments = {"prompt": f"variant {index}"}
            arguments_hash = hashlib.sha256(
                json.dumps(arguments, separators=(",", ":")).encode(),
            ).hexdigest()
            request_hash = hashlib.sha256(f"request:{action_id}".encode()).hexdigest()
            connection.execute(
                "INSERT INTO agent_actions(id,session_id,run_id,model_step_id,"
                "org_id,user_id,action_index,stable_tool_call_id,tool_name,"
                "arguments,arguments_hash,request_hash,batch_hash,blocking,"
                "policy_decision,policy_snapshot,policy_revision,retry_disposition,"
                "status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'generate_image',%s,%s,"
                "%s,%s,true,'requires_authorization',%s,'agent-runtime-v1',"
                "'retry_after_reconcile','awaiting_authorization')",
                (action_id, ids["session"], ids["run"], ids["step"], ORG, USER,
                 index, f"call-{index}", Jsonb(arguments), arguments_hash,
                 request_hash, batch_hash, Jsonb({
                     "safety_level": "confirm",
                     "authorization_requirement": "persisted_interaction",
                     "executor_revision": 1,
                 })),
            )
            members.append({
                "action_id": str(action_id), "expected_action_version": 0,
                "action_index": index, "arguments_hash": arguments_hash,
            })
    ids["members"] = members
    return ids


def _open(database_url: str, ids: dict[str, object]) -> dict[str, object]:
    with _connect(database_url, "everydayai_agent_runtime_worker",
                  "agent_runtime") as connection:
        return _value(
            connection,
            "SELECT open_agent_authorization_batch_v1(%s,%s,%s::jsonb,900)",
            (ids["step"], ids["batch_hash"], json.dumps(ids["members"])),
        )


def _claim(database_url: str, worker_id: str) -> dict[str, object]:
    with _connect(database_url, "everydayai_projection_worker",
                  "projection") as connection:
        return _value(
            connection,
            "SELECT claim_agent_tool_batch_confirmation_v1(%s,60)",
            (worker_id,),
        )


def _resolve(
    database_url: str, claim: dict[str, object], approved: bool,
    *, confirmation_id: str = "c" * 40, user: UUID = USER,
) -> dict[str, object]:
    with _connect(
        database_url, "everydayai_runtime", "runtime", user=user,
    ) as connection:
        return _value(connection, """
          SELECT resolve_agent_tool_batch_confirmation_v1(
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            confirmation_id, claim["interaction_id"], claim["action_id"],
            claim["interaction_version"], USER, ORG, claim["arguments_hash"],
            claim["confirmation_group_hash"],
            claim["authorization_expires_at"], approved,
        ))


def _enable_confirmation(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_control SET tool_confirmation_enabled=TRUE,"
            "action_dispatch_enabled=TRUE,non_safe_actions_enabled=TRUE",
        )
        connection.execute(
            "INSERT INTO agent_runtime_capabilities("
            "capability_name,reporter_role,ready,evidence) "
            "VALUES('tool_confirmation_v3_redis','runtime',TRUE,'{}') "
            "ON CONFLICT(capability_name) DO UPDATE SET ready=TRUE,"
            "observed_at=clock_timestamp()",
        )
        connection.execute(
            "INSERT INTO agent_runtime_org_rollout(org_id,enabled,updated_by,"
            "update_reason) VALUES(%s,TRUE,%s,'batch auth') "
            "ON CONFLICT(org_id) DO UPDATE SET enabled=TRUE",
            (ORG, USER),
        )


def test_group_migration_apply_readback_rollback_reapply(database: str) -> None:
    _apply(database, MIGRATION_228_01)
    _apply(database, MIGRATION)
    with psycopg.connect(database) as connection:
        columns = _value(connection, """
          SELECT count(*) FROM information_schema.columns
          WHERE table_name='agent_interactions'
            AND column_name IN (
              'confirmation_group_hash','confirmation_group_leader_id')
        """)
        security = connection.execute("""
          SELECT relrowsecurity,relforcerowsecurity FROM pg_class
          WHERE relname='agent_interactions'
        """).fetchone()
        search_paths = _value(connection, """
          SELECT bool_and('search_path=pg_catalog, public'=ANY(proconfig))
          FROM pg_proc WHERE proname IN (
            'open_agent_authorization_batch_v1',
            'claim_agent_tool_batch_confirmation_v1',
            'resolve_agent_tool_batch_confirmation_v1')
        """)
        assert columns == 2
        assert security == (True, True)
        assert search_paths is True
        assert _value(connection, "SELECT has_function_privilege("
            "'everydayai_agent_runtime_worker',"
            "'open_agent_authorization_batch_v1(uuid,text,jsonb,integer)',"
            "'EXECUTE')") is True
        assert _value(connection, "SELECT has_function_privilege("
            "'everydayai_worker',"
            "'open_agent_authorization_batch_v1(uuid,text,jsonb,integer)',"
            "'EXECUTE')") is False
        assert _value(connection, "SELECT has_function_privilege("
            "'everydayai_projection_worker',"
            "'claim_agent_tool_batch_confirmation_v1(text,integer)',"
            "'EXECUTE')") is True
        assert _value(connection, "SELECT has_table_privilege("
            "'everydayai_projection_worker','agent_interactions','SELECT')"
        ) is False
    with _connect(
        database, "everydayai_agent_runtime_worker", "worker",
    ) as connection, pytest.raises(psycopg.Error, match="SCOPE_REQUIRED"):
        connection.execute(
            "SELECT open_agent_authorization_batch_v1(%s,%s,'[]',900)",
            (uuid4(), "a" * 64),
        )
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        assert _value(connection, """
          SELECT count(*) FROM information_schema.columns
          WHERE table_name='agent_interactions'
            AND column_name='confirmation_group_hash'
        """) == 0
    _apply(database, MIGRATION)
    with psycopg.connect(database) as connection:
        assert _value(connection, "SELECT to_regprocedure("
            "'resolve_agent_tool_batch_confirmation_v1(text,uuid,uuid,bigint,"
            "uuid,uuid,text,text,timestamptz,boolean)') IS NOT NULL") is True


def test_raw_action_wrapper_opens_one_exact_group(database: str) -> None:
    _apply(database, MIGRATION_228_01)
    _apply(database, MIGRATION)
    ids = canonicalization._seed_running_step(database)
    actions = canonicalization._raw_actions(10)
    for action in actions:
        action["policy_decision"] = "requires_authorization"
        action["policy_snapshot"] = {
            "safety_level": "confirm",
            "authorization_requirement": "persisted_interaction",
        }
        action["retry_disposition"] = "retry_after_reconcile"

    result = canonicalization._complete(database, ids, actions)

    assert result["outcome"] == "completed"
    assert canonicalization._complete(
        database, ids, actions,
    )["outcome"] == "already_completed"
    with psycopg.connect(database) as connection:
        assert connection.execute("""
          SELECT run.open_interaction_count,
            (SELECT count(*) FROM agent_interactions interaction
              WHERE interaction.run_id=run.id),
            (SELECT count(DISTINCT confirmation_group_hash)
              FROM agent_interactions interaction WHERE interaction.run_id=run.id),
            (SELECT count(*) FROM agent_interactions interaction
              WHERE interaction.run_id=run.id
                AND interaction.id=interaction.confirmation_group_leader_id)
          FROM agent_runs run WHERE run.id=%s
        """, (ids["run"],)).fetchone() == (10, 10, 1, 1)


def test_ten_action_group_confirmation_and_terminal_paths(database: str) -> None:
    _apply(database, MIGRATION_228_01)
    _apply(database, MIGRATION)
    _enable_confirmation(database)
    _assert_approved_batch(database)
    _assert_denied_and_expired_batches(database)
    _assert_single_confirmation_compatibility(database)


def _assert_approved_batch(database: str) -> None:
    approved = _seed_batch(database, 10)
    opened = _open(database, approved)
    assert opened["outcome"] == "opened"
    assert opened["member_count"] == 10
    assert _open(database, approved)["outcome"] == "already_open"
    with psycopg.connect(database) as connection:
        row = connection.execute(
            "SELECT open_interaction_count,status FROM agent_runs WHERE id=%s",
            (approved["run"],),
        ).fetchone()
        assert row == (10, "waiting_interaction")
        assert _value(connection, "SELECT count(*) FROM agent_interactions "
            "WHERE confirmation_group_hash=%s AND id=confirmation_group_leader_id",
            (opened["confirmation_group_hash"],)) == 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda worker: _claim(database, worker), ("projection-a", "projection-b"),
        ))
    claim = next(result for result in results if result["outcome"] == "claimed")
    assert sum(result["outcome"] == "claimed" for result in results) == 1
    assert claim["confirmation_group_size"] == 10

    with _connect(database, "everydayai_runtime", "runtime") as connection:
        single = _value(connection, """
          SELECT resolve_agent_authorization_interaction(
            %s,%s,'approve',%s,'{}','action',NULL,900)
        """, (claim["interaction_id"], claim["interaction_version"], "d" * 64))
        legacy = _value(connection, """
          SELECT resolve_agent_tool_confirmation_v3(
            %s,%s,%s,%s,%s,%s,%s,%s,TRUE)
        """, ("z" * 40, claim["interaction_id"], claim["action_id"],
               claim["interaction_version"], USER, ORG,
               claim["arguments_hash"], claim["authorization_expires_at"]))
        assert single["outcome"] == "group_confirmation_required"
        assert legacy["outcome"] == "group_confirmation_required"

    foreign_user = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO users(id,credits) VALUES(%s,100)", (foreign_user,),
        )
    assert _resolve(database, claim, True, user=foreign_user)["outcome"] == (
        "binding_mismatch"
    )
    resolved = _resolve(database, claim, True)
    assert resolved == {"outcome": "resolved", "member_count": 10}
    assert _resolve(database, claim, True)["outcome"] == "already_resolved"
    assert _resolve(database, claim, False)["outcome"] == "confirmation_conflict"

    with psycopg.connect(database) as connection:
        assert connection.execute("""
          SELECT run.open_interaction_count,
            (SELECT count(*) FROM agent_interactions i
              WHERE i.run_id=run.id AND i.status='resolved'),
            (SELECT count(*) FROM agent_authorization_grants g
              WHERE g.run_id=run.id AND g.grant_kind='action'),
            (SELECT count(*) FROM agent_tool_confirmation_results r
              WHERE r.interaction_id IN (
                SELECT id FROM agent_interactions WHERE run_id=run.id))
          FROM agent_runs run WHERE run.id=%s
        """, (approved["run"],)).fetchone() == (0, 10, 10, 1)

    receipt_ids: dict[str, str] = {}
    for index in range(10):
        with _connect(
            database, "everydayai_authorization_worker", "authorization",
        ) as connection:
            recovery = _value(
                connection,
                "SELECT claim_next_agent_authorization_recovery(%s,120)",
                (f"authorization-{index}",),
            )
            action = recovery["action"]
            grant = recovery["grant"]
            receipt = _value(connection, """
              SELECT record_agent_policy_receipt(
                %s,%s,'runtime_media_generation:generate_image',1,
                'agent-runtime-v1','allow',%s,'{}',
                ARRAY['ACTION_GRANT_VALID'],ARRAY['audit'],%s,300)
            """, (action["id"], action["arguments_hash"], grant["id"],
                   hashlib.sha256(f"receipt:{action['id']}".encode()).hexdigest()))
            activated = _value(connection, """
              SELECT activate_agent_authorized_action(%s,%s,%s,%s,%s,%s)
            """, (action["id"], action["state_version"],
                   recovery["interaction_id"], recovery["recovery_token"],
                   recovery["state_version"], receipt["receipt"]["id"]))
            assert activated["outcome"] == "activated"
            receipt_ids[str(action["id"])] = str(receipt["receipt"]["id"])
    for index in range(10):
        with _connect(
            database, "everydayai_agent_runtime_worker", "agent_runtime",
        ) as connection:
            snapshot = _value(connection, """
              SELECT claim_ready_agent_action_snapshots(%s,%s,1,120)
            """, (f"action-{index}", f"claim-{index}"))["snapshots"][0]
            receipt_id = receipt_ids[str(snapshot["action_id"])]
            gate = _value(connection, """
              SELECT gate_agent_action_dispatch(
                %s,%s,%s,%s,%s,'runtime_media_generation:generate_image',
                1,'agent-runtime-v1','reconcile_only')
            """, (snapshot["id"], snapshot["execution_token"],
                   snapshot["state_version"], snapshot["request_hash"],
                   receipt_id))
            assert gate["outcome"] == "dispatch_authorized"
    with psycopg.connect(database) as connection:
        assert connection.execute("""
          SELECT
            (SELECT count(*) FROM agent_actions WHERE run_id=%s AND status='running'),
            (SELECT count(*) FROM agent_authorization_grant_uses grant_use
              JOIN agent_actions action ON action.id=grant_use.action_id
              WHERE action.run_id=%s),
            (SELECT count(*) FROM agent_policy_receipts WHERE run_id=%s),
            (SELECT count(*) FROM agent_action_dispatch_intents intent
              JOIN agent_actions action ON action.id=intent.action_id
              WHERE action.run_id=%s)
        """, (approved["run"], approved["run"], approved["run"],
               approved["run"])).fetchone() == (10, 10, 10, 10)


def _assert_denied_and_expired_batches(database: str) -> None:
    denied = _seed_batch(database, 3)
    _open(database, denied)
    denied_claim = _claim(database, "projection-deny")
    assert _resolve(
        database, denied_claim, False, confirmation_id="d" * 40,
    )["outcome"] == "resolved"
    assert _resolve(
        database, denied_claim, False, confirmation_id="d" * 40,
    )["outcome"] == "already_resolved"
    with psycopg.connect(database) as connection:
        assert connection.execute("""
          SELECT open_interaction_count,blocking_action_count,
            (SELECT count(*) FROM agent_actions
              WHERE run_id=agent_runs.id AND status='rejected')
          FROM agent_runs WHERE id=%s
        """, (denied["run"],)).fetchone() == (0, 0, 3)

    expired = _seed_batch(database, 2)
    expired_open = _open(database, expired)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_interactions SET expires_at=clock_timestamp()-"
            "interval '1 second' WHERE confirmation_group_hash=%s",
            (expired_open["confirmation_group_hash"],),
        )
    assert _claim(database, "projection-expire")["outcome"] == "not_found"
    with psycopg.connect(database) as connection:
        assert connection.execute("""
          SELECT open_interaction_count,blocking_action_count,
            (SELECT count(*) FROM agent_interactions
              WHERE run_id=agent_runs.id AND status='expired'),
            (SELECT count(*) FROM agent_actions
              WHERE run_id=agent_runs.id AND status='rejected')
          FROM agent_runs WHERE id=%s
        """, (expired["run"],)).fetchone() == (0, 0, 2, 2)


def _assert_single_confirmation_compatibility(database: str) -> None:
    single = _seed_batch(database, 1)
    action_id = single["actions"][0]
    arguments_hash = single["members"][0]["arguments_hash"]
    prompt = {
        "protocol_version": 3, "action_id": str(action_id),
        "tool_call_id": "call-0", "tool_name": "generate_image",
        "arguments": {"prompt": "variant 0"},
        "arguments_hash": arguments_hash,
    }
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
          INSERT INTO agent_interactions(
            action_id,session_id,run_id,org_id,user_id,prompt,prompt_hash,
            expires_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,clock_timestamp()+interval '15 minutes')
        """, (action_id, single["session"], single["run"], ORG, USER,
               Jsonb(prompt), "f" * 64))
        connection.execute("""
          UPDATE agent_runs SET open_interaction_count=1,
            status='waiting_interaction' WHERE id=%s
        """, (single["run"],))
    single_claim = _claim(database, "projection-single")
    assert single_claim["confirmation_group_hash"] == ""
    assert single_claim["confirmation_group_size"] == 1
    with _connect(database, "everydayai_runtime", "runtime") as connection:
        resolved_single = _value(connection, """
          SELECT resolve_agent_tool_confirmation_v3(
            %s,%s,%s,%s,%s,%s,%s,%s,TRUE)
        """, ("s" * 40, single_claim["interaction_id"],
               single_claim["action_id"], single_claim["interaction_version"],
               USER, ORG, single_claim["arguments_hash"],
               single_claim["authorization_expires_at"]))
        assert resolved_single["outcome"] == "resolved"
