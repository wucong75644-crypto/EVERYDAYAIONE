from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from typing import NamedTuple
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import (
    CONVERSATION, ORG, USER, _connect, _settings, database,
)


pytestmark = pytest.mark.external
MIGRATION = Path(__file__).resolve().parents[1] / "migrations/228_04_agent_runtime_media_action_bindings.sql"
ROLLBACK = MIGRATION.parent / "rollback/228_04_agent_runtime_media_action_bindings_rollback.sql"


class AttemptFact(NamedTuple):
    action_id: UUID
    attempt_id: UUID
    token: UUID
    request_hash: str


class BatchFact(NamedTuple):
    step_id: UUID
    output_id: UUID
    attempts: tuple[AttemptFact, ...]


def _prepare_legacy_schema(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
          ALTER TABLE tasks
            ADD COLUMN placeholder_created_at TIMESTAMPTZ,
            ADD COLUMN base_context_revision BIGINT,
            ADD COLUMN context_through_message_id UUID,
            ADD COLUMN image_index INTEGER,
            ADD COLUMN batch_id TEXT,
            ADD COLUMN credit_transaction_id UUID REFERENCES credit_transactions(id),
            ADD COLUMN last_polled_at TIMESTAMPTZ;
          CREATE TABLE agent_runtime_provider_submission_facts(
            action_id UUID NOT NULL REFERENCES agent_actions(id)
          );
        """)
        connection.execute(MIGRATION.read_text(encoding="utf-8"))


def _seed_batch(
    database_url: str, count: int, *, credits: int | None = None,
    model: str = "gpt-image-2-text-to-image", resolution: str = "1K",
) -> BatchFact:
    ids = {name: uuid4() for name in (
        "session", "command", "run", "step", "input", "output", "chat_task", "turn",
    )}
    batch_hash = hashlib.sha256(str(ids["step"]).encode()).hexdigest()
    attempts: list[AttemptFact] = []
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        if credits is not None:
            connection.execute(
                "UPDATE users SET credits=%s WHERE id=%s", (credits, USER),
            )
        existing_session = connection.execute(
            "SELECT id FROM agent_runtime_sessions WHERE conversation_id=%s",
            (CONVERSATION,),
        ).fetchone()
        if existing_session:
            ids["session"] = existing_session[0]
        else:
            connection.execute("""
              INSERT INTO agent_runtime_sessions(
                id,conversation_id,org_id,user_id,scope_kind,scope_id,
                created_by_user_id,agent_definition_id,agent_definition_revision
              ) VALUES(%s,%s,%s,%s,'user',%s,%s,'everydayai-default','v7')
            """, (
                ids["session"], CONVERSATION, ORG, USER, str(USER), USER,
            ))
        input_content = [{
            "type": "image", "url": "https://example.invalid/reference.png",
        }]
        connection.execute("""
          INSERT INTO messages(id,conversation_id,org_id,role,content,status,turn_id)
          VALUES(%s,%s,%s,'user',%s,'completed',%s),
                (%s,%s,%s,'assistant','[]','pending',%s)
        """, (
            ids["input"], CONVERSATION, ORG, json.dumps(input_content), ids["turn"],
            ids["output"], CONVERSATION, ORG, ids["turn"],
        ))
        connection.execute("""
          INSERT INTO tasks(
            id,user_id,org_id,conversation_id,type,status,model_id,
            placeholder_message_id,assistant_message_id,input_message_id,turn_id,
            execution_mode,delivery_context
          ) VALUES(%s,%s,%s,%s,'chat','running','qwen3.5-plus',%s,%s,%s,%s,
                   'serial','{"actor":false,"runtime":true}')
        """, (
            ids["chat_task"], USER, ORG, CONVERSATION, str(ids["output"]),
            ids["output"], ids["input"], ids["turn"],
        ))
        connection.execute("""
          INSERT INTO agent_session_commands(
            id,session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash
          ) VALUES(%s,%s,%s,%s,'submit_input',%s,%s,%s)
        """, (
            ids["command"], ids["session"], ORG, USER, f"command:{ids['command']}",
            Jsonb({
                "task_id": str(ids["chat_task"]),
                "input_message_id": str(ids["input"]),
                "output_message_id": str(ids["output"]),
                "turn_id": str(ids["turn"]),
            }), hashlib.md5(str(ids["command"]).encode()).hexdigest(),
        ))
        connection.execute("""
          INSERT INTO agent_runs(
            id,session_id,command_id,org_id,user_id,run_kind,status,
            idempotency_key,request_hash,blocking_action_count,capability_snapshot
          ) VALUES(%s,%s,%s,%s,%s,'user','waiting_actions',%s,%s,%s,'{"channel":"web"}')
        """, (
            ids["run"], ids["session"], ids["command"], ORG, USER,
            f"run:{ids['run']}", hashlib.md5(str(ids["run"]).encode()).hexdigest(), count,
        ))
        connection.execute("""
          INSERT INTO agent_model_steps(
            id,run_id,session_id,org_id,user_id,step_number,status,model_id,
            provider,model_revision,prompt_revision,tool_catalog_revision,
            request_receipt,response_receipt,stop_reason,completed_at
          ) VALUES(%s,%s,%s,%s,%s,1,'completed','qwen3.5-plus','dashscope',
                   'v1','batch-media-v1','catalog-v7','{}','{}','tool_calls',clock_timestamp())
        """, (
            ids["step"], ids["run"], ids["session"], ORG, USER,
        ))
        for index in range(count):
            attempts.append(_seed_action(
                connection, ids, batch_hash, index=index, model=model,
                resolution=resolution,
            ))
    return BatchFact(ids["step"], ids["output"], tuple(attempts))


def _seed_action(
    connection: psycopg.Connection, ids: dict[str, UUID], batch_hash: str,
    *, index: int, model: str, resolution: str,
) -> AttemptFact:
    action_id, attempt_id, token, receipt_id = uuid4(), uuid4(), uuid4(), uuid4()
    arguments = {
        "prompt": f"variant {index}", "reference_image_indexes": [0],
        "model": model, "resolution": resolution, "aspect_ratio": "1:1",
    }
    arguments_hash = hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    request_hash = hashlib.sha256(str(action_id).encode()).hexdigest()
    connection.execute("""
      INSERT INTO agent_actions(
        id,session_id,run_id,model_step_id,org_id,user_id,action_index,
        stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,
        batch_hash,wave,dependency_ids,blocking,policy_decision,policy_snapshot,
        policy_revision,retry_disposition,status
      ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'generate_image',%s,%s,%s,%s,0,'{}',TRUE,
               'preauthorized','{}','v1','retry_after_reconcile','running')
    """, (
        action_id, ids["session"], ids["run"], ids["step"], ORG, USER, index,
        f"call-{ids['step']}-{index}", Jsonb(arguments), arguments_hash,
        request_hash, batch_hash,
    ))
    connection.execute("""
      INSERT INTO agent_action_attempts(
        id,action_id,session_id,run_id,org_id,user_id,attempt_number,status,
        dispatch_phase,worker_id,execution_token,lease_expires_at,idempotency_key,
        request_hash,retry_disposition,state_version
      ) VALUES(%s,%s,%s,%s,%s,%s,1,'claimed','claimed','media-worker',%s,
               clock_timestamp()+interval '10 minutes',%s,%s,'retry_after_reconcile',0)
    """, (
        attempt_id, action_id, ids["session"], ids["run"], ORG, USER, token,
        f"attempt:{attempt_id}", request_hash,
    ))
    connection.execute("""
      INSERT INTO agent_policy_receipts(
        id,action_id,session_id,run_id,org_id,user_id,decision,arguments_hash,
        executor_type,executor_revision,policy_revision,effective_scope,
        reason_codes,receipt_hash,expires_at
      ) VALUES(%s,%s,%s,%s,%s,%s,'allow',%s,'runtime_media_generation:generate_image',
               1,'v1','{}',ARRAY['test'],%s,clock_timestamp()+interval '10 minutes')
    """, (
        receipt_id, action_id, ids["session"], ids["run"], ORG, USER,
        arguments_hash, hashlib.sha256(str(receipt_id).encode()).hexdigest(),
    ))
    connection.execute("""
      INSERT INTO agent_action_dispatch_intents(
        attempt_id,action_id,policy_receipt_id,execution_token,request_hash,
        executor_type,executor_revision,policy_revision,external_idempotency_key,
        recovery_mode
      ) VALUES(%s,%s,%s,%s,%s,'runtime_media_generation:generate_image',1,'v1',%s,
               'idempotent_replay')
    """, (
        attempt_id, action_id, receipt_id, token, request_hash,
        f"media:{action_id}",
    ))
    return AttemptFact(action_id, attempt_id, token, request_hash)


def _worker_call(
    database_url: str, name: str, fact: AttemptFact,
    *, manifest_hash: str | None = None,
) -> dict[str, object]:
    with _connect(database_url, "everydayai_agent_runtime_worker") as connection:
        _settings(connection, "everydayai_agent_runtime_worker")
        params: list[object] = [
            fact.action_id, fact.attempt_id, "media-worker", fact.token, 0,
            fact.request_hash,
        ]
        signature = "%s,%s,%s,%s,%s,%s"
        if manifest_hash is not None:
            params.append(manifest_hash)
            signature += ",%s"
        [result] = connection.execute(
            f"SELECT {name}({signature})", tuple(params),
        ).fetchone()
        return result


def _prepare(database_url: str, fact: AttemptFact) -> dict[str, object]:
    manifest = _worker_call(
        database_url, "read_agent_runtime_media_manifest_v1", fact,
    )
    return _worker_call(
        database_url, "prepare_agent_runtime_media_batch_v1", fact,
        manifest_hash=str(manifest["reference_manifest_hash"]),
    )


def _step_counts(database_url: str, step_id: UUID) -> tuple[int, int, int]:
    with psycopg.connect(database_url) as connection:
        return connection.execute("""
          SELECT count(DISTINCT binding.action_id)::integer,
                 count(DISTINCT task.id)::integer,
                 count(DISTINCT transaction.id)::integer
          FROM agent_actions action
          LEFT JOIN agent_runtime_media_action_bindings binding
            ON binding.action_id=action.id
          LEFT JOIN tasks task ON task.id=binding.task_id
          LEFT JOIN credit_transactions transaction
            ON transaction.id=binding.credit_transaction_id
          WHERE action.model_step_id=%s
        """, (step_id,)).fetchone()


def _assert_atomic_failure(database_url: str, batch: BatchFact) -> None:
    assert _step_counts(database_url, batch.step_id) == (0, 0, 0)


def _assert_prepared_shape(database_url: str, batch: BatchFact, count: int, history_count: int) -> None:
    with psycopg.connect(database_url) as connection:
        slot_count, metadata_count = connection.execute("""
          SELECT jsonb_array_length(content::jsonb),
                 (generation_params->'runtime_media_batch'->>'slot_count')::integer
          FROM messages WHERE id=%s
        """, (batch.output_id,)).fetchone()
        transactions = connection.execute("""
          SELECT count(*),bool_and(tx.status='pending'),sum(tx.amount),
                 (SELECT count(*) FROM credits_history
                   WHERE description='Agent Runtime media reservation')
          FROM agent_runtime_media_action_bindings binding
          JOIN credit_transactions tx ON tx.id=binding.credit_transaction_id
          WHERE binding.model_step_id=%s
        """, (batch.step_id,)).fetchone()
    assert (slot_count, metadata_count) == (count, count)
    assert transactions == (count, True, count * 6, history_count)


def _assert_security_and_worker_fence(database_url: str, batch: BatchFact) -> None:
    fact = batch.attempts[0]
    with _connect(database_url, "everydayai_runtime") as connection:
        _settings(connection, "everydayai_runtime")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "SELECT read_agent_runtime_media_binding_v1(%s,%s,%s,%s,0,%s)",
                (fact.action_id, fact.attempt_id, "media-worker", fact.token, fact.request_hash),
            )
    with _connect(database_url, "everydayai_agent_runtime_worker") as connection:
        _settings(connection, "everydayai_agent_runtime_worker")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM agent_runtime_media_action_bindings")
    with _connect(database_url, "everydayai_agent_runtime_worker") as connection:
        _settings(connection, "everydayai_agent_runtime_worker")
        connection.execute("SELECT set_config('app.access_kind','runtime',false)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "SELECT read_agent_runtime_media_binding_v1(%s,%s,%s,%s,0,%s)",
                (fact.action_id, fact.attempt_id, "media-worker", fact.token, fact.request_hash),
            )
    with psycopg.connect(database_url) as connection:
        assert not connection.execute(
            "SELECT has_table_privilege('everydayai_agent_runtime_worker',"
            "'agent_runtime_media_action_bindings','SELECT')",
        ).fetchone()[0]
        rows = connection.execute("""
          SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class
          WHERE relname IN('agent_runtime_media_action_bindings',
                           'agent_runtime_media_pricing_facts') ORDER BY relname
        """).fetchall()
        assert rows == [
            ("agent_runtime_media_action_bindings", True, True),
            ("agent_runtime_media_pricing_facts", True, True),
        ]
        definition = connection.execute("""
          SELECT pg_get_functiondef(
            'prepare_agent_runtime_media_batch_v1(uuid,uuid,text,uuid,bigint,text,text)'::regprocedure
          )
        """).fetchone()[0]
        assert "SET search_path TO 'pg_catalog', 'public'" in definition


def _assert_worker_discovery_fence(database_url: str, batch: BatchFact) -> None:
    legacy_task = uuid4()
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE tasks SET status='pending',delivery_context='{}' WHERE id=%s",
            (batch.attempts[0].action_id,),
        )
        connection.execute("""
          INSERT INTO tasks(id,user_id,org_id,conversation_id,type,status,model_id,
                            delivery_context)
          VALUES(%s,%s,%s,%s,'image','pending','legacy-image','{}')
        """, (legacy_task, USER, ORG, CONVERSATION))
    with _connect(database_url, "everydayai_worker") as connection:
        result = connection.execute(
            "SELECT worker_discover_media_tasks(100)",
        ).fetchone()[0]
    discovered = {UUID(item["id"]) for item in result}
    assert legacy_task in discovered
    assert batch.attempts[0].action_id not in discovered


def _assert_settlement(database_url: str, batch: BatchFact) -> None:
    settled, refunded = batch.attempts[:2]
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_actions SET status='completed',completed_at=clock_timestamp() WHERE id=%s",
            (settled.action_id,),
        )
        connection.execute(
            "UPDATE agent_actions SET status='failed',completed_at=clock_timestamp() WHERE id=%s",
            (refunded.action_id,),
        )
    with _connect(database_url, "everydayai_projection_worker") as connection:
        _settings(connection, "everydayai_projection_worker")
        connection.execute("SELECT set_config('app.access_kind','projection',false)")
        assert connection.execute(
            "SELECT settle_agent_runtime_media_credit_v1(%s,1)",
            (settled.action_id,),
        ).fetchone()[0]["outcome"] == "settled"
        assert connection.execute(
            "SELECT settle_agent_runtime_media_credit_v1(%s,1)",
            (settled.action_id,),
        ).fetchone()[0]["outcome"] == "already_settled"
        assert connection.execute(
            "SELECT refund_agent_runtime_media_credit_v1(%s,1)",
            (refunded.action_id,),
        ).fetchone()[0]["outcome"] == "refunded"
        assert connection.execute(
            "SELECT refund_agent_runtime_media_credit_v1(%s,1)",
            (refunded.action_id,),
        ).fetchone()[0]["outcome"] == "already_refunded"


def _cleanup_and_rollback(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(ROLLBACK.read_text(encoding="utf-8"))
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        transaction_ids = connection.execute(
            "SELECT credit_transaction_id FROM agent_runtime_media_action_bindings",
        ).fetchall()
        task_ids = connection.execute(
            "SELECT task_id FROM agent_runtime_media_action_bindings",
        ).fetchall()
        connection.execute("DELETE FROM agent_runtime_media_action_bindings")
        if task_ids:
            connection.execute("DELETE FROM tasks WHERE id=ANY(%s)", ([row[0] for row in task_ids],))
        if transaction_ids:
            connection.execute(
                "DELETE FROM credit_transactions WHERE id=ANY(%s)",
                ([row[0] for row in transaction_ids],),
            )
        connection.execute(ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regclass('agent_runtime_media_action_bindings')",
        ).fetchone()[0] is None
        restored = connection.execute(
            "SELECT pg_get_functiondef('worker_discover_media_tasks(integer)'::regprocedure)",
        ).fetchone()[0]
        assert "agent_runtime_media_action_bindings" not in restored
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT count(*) FROM agent_runtime_media_pricing_facts",
        ).fetchone()[0] == 11


def test_runtime_media_prepare_full_database_contract(database: str) -> None:
    _prepare_legacy_schema(database)
    one = _seed_batch(database, 1, credits=1000)
    first_balance = _balance(database)
    assert _prepare(database, one.attempts[0])["outcome"] == "prepared"
    assert _prepare(database, one.attempts[0])["outcome"] == "already_prepared"
    assert _worker_call(database, "read_agent_runtime_media_binding_v1", one.attempts[0])["outcome"] == "found"
    assert _step_counts(database, one.step_id) == (1, 1, 1)
    assert _balance(database) == first_balance - 6
    _assert_prepared_shape(database, one, 1, 1)

    ten = _seed_batch(database, 10)
    before_ten = _balance(database)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(
            lambda fact: _prepare(database, fact), ten.attempts[:2],
        ))
    assert {item["outcome"] for item in outcomes} == {"prepared", "already_prepared"}
    assert _step_counts(database, ten.step_id) == (10, 10, 10)
    assert _balance(database) == before_ten - 60
    _assert_prepared_shape(database, ten, 10, 11)
    _assert_settlement(database, ten)

    eleven = _seed_batch(database, 11)
    before_failure = _balance(database)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _prepare(database, eleven.attempts[0])
    _assert_atomic_failure(database, eleven)
    assert _balance(database) == before_failure

    insufficient = _seed_batch(database, 1, credits=5)
    with pytest.raises(psycopg.errors.RaiseException):
        _prepare(database, insufficient.attempts[0])
    _assert_atomic_failure(database, insufficient)
    assert _balance(database) == 5

    illegal = _seed_batch(database, 2, credits=100)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_actions SET arguments=arguments||'{\"reserved_credits\":1}' "
            "WHERE id=%s", (illegal.attempts[1].action_id,),
        )
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _prepare(database, illegal.attempts[0])
    _assert_atomic_failure(database, illegal)
    assert _balance(database) == 100

    cross_tenant = _seed_batch(database, 2, credits=100)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        foreign_org = uuid4()
        connection.execute("INSERT INTO organizations(id) VALUES(%s)", (foreign_org,))
        connection.execute(
            "UPDATE agent_actions SET org_id=%s WHERE id=%s",
            (foreign_org, cross_tenant.attempts[1].action_id),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _prepare(database, cross_tenant.attempts[0])
    _assert_atomic_failure(database, cross_tenant)

    changed = _seed_batch(database, 1, credits=100, resolution="4K")
    prepared = _prepare(database, changed.attempts[0])
    assert (prepared["total_credits"], prepared["binding"]["pricing_resolution"]) == (10, "2K")
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_actions SET arguments=arguments||'{\"prompt\":\"changed\"}',"
            "arguments_hash=%s WHERE id=%s", ("f" * 64, changed.attempts[0].action_id),
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        _prepare(database, changed.attempts[0])
    assert _step_counts(database, changed.step_id) == (1, 1, 1)

    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE agent_runtime_media_pricing_facts SET user_credits=1 "
                "WHERE model_id='gpt-image-2-text-to-image' AND resolution_key='1K'",
            )
    _assert_security_and_worker_fence(database, one)
    _assert_worker_discovery_fence(database, one)
    _cleanup_and_rollback(database)


def _balance(database_url: str) -> int:
    with psycopg.connect(database_url) as connection:
        return connection.execute("SELECT credits FROM users WHERE id=%s", (USER,)).fetchone()[0]
