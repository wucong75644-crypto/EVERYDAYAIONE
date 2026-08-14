"""Disposable PostgreSQL contract for Runtime raw Action hash canonicalization."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import (
    ORG,
    USER,
    _connect,
    _settings,
    database,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_01_agent_runtime_action_hash_canonicalization.sql"
ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_01_agent_runtime_action_hash_canonicalization_rollback.sql"
)
FUNCTION_SIGNATURE = (
    "complete_model_attempt_with_raw_actions("
    "uuid,uuid,bigint,bigint,text,jsonb,text,text,jsonb,integer,jsonb)"
)


def _apply(database_url: str, path: Path) -> None:
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            connection.execute(path.read_text(encoding="utf-8"))


def _seed_running_step(database_url: str) -> dict[str, UUID]:
    ids = {
        name: uuid4() for name in (
            "conversation", "session", "command", "run", "run_attempt",
            "step", "attempt", "token",
        )
    }
    with psycopg.connect(
        database_url, cursor_factory=psycopg.ClientCursor,
    ) as connection:
        connection.execute("SET ROLE everydayai_owner")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id)
                VALUES(%(conversation)s,%(user)s,%(org)s,'user',%(user_text)s);
                INSERT INTO agent_runtime_sessions(
                  id,conversation_id,org_id,user_id,scope_kind,scope_id,
                  created_by_user_id,agent_definition_id,agent_definition_revision
                ) VALUES(
                  %(session)s,%(conversation)s,%(org)s,%(user)s,'user',
                  %(user_text)s,%(user)s,'everydayai-default','v1'
                );
                INSERT INTO agent_session_commands(
                  id,session_id,org_id,user_id,command_type,idempotency_key,
                  payload,request_hash
                ) VALUES(
                  %(command)s,%(session)s,%(org)s,%(user)s,'submit_input',
                  %(command_key)s,'{}','11111111111111111111111111111111'
                );
                INSERT INTO agent_runs(
                  id,session_id,command_id,org_id,user_id,run_kind,status,
                  idempotency_key,request_hash,execution_token,lease_expires_at,
                  attempt_count,started_at
                ) VALUES(
                  %(run)s,%(session)s,%(command)s,%(org)s,%(user)s,'user','running',
                  %(run_key)s,'22222222222222222222222222222222',%(token)s,
                  clock_timestamp()+interval '10 minutes',1,clock_timestamp()
                );
                INSERT INTO agent_run_attempts(
                  id,run_id,org_id,user_id,attempt_number,execution_token,worker_id,
                  lease_expires_at
                ) VALUES(
                  %(run_attempt)s,%(run)s,%(org)s,%(user)s,1,%(token)s,
                  'hash-worker',clock_timestamp()+interval '10 minutes'
                );
                INSERT INTO agent_model_steps(
                  id,run_id,session_id,org_id,user_id,step_number,status,model_id,
                  provider,model_revision,prompt_revision,tool_catalog_revision,
                  request_receipt
                ) VALUES(
                  %(step)s,%(run)s,%(session)s,%(org)s,%(user)s,1,'running',
                  'model','provider','v1','v1','catalog-v1','{}'
                );
                INSERT INTO agent_model_attempts(
                  id,model_step_id,run_id,session_id,org_id,user_id,attempt_number,
                  request_hash,idempotency_key,provider,status,dispatch_phase,
                  request_receipt,worker_id,execution_token,lease_expires_at,
                  dispatched_at
                ) VALUES(
                  %(attempt)s,%(step)s,%(run)s,%(session)s,%(org)s,%(user)s,1,
                  %(model_hash)s,%(attempt_key)s,'provider','dispatching',
                  'request_started','{}','hash-worker',%(token)s,
                  clock_timestamp()+interval '10 minutes',clock_timestamp()
                );
                INSERT INTO agent_model_credit_settlements(
                  model_step_id,reservation_attempt_id,billing_user_id,org_id,
                  reservation_key,status,reserved_credits
                ) VALUES(
                  %(step)s,%(attempt)s,%(user)s,%(org)s,%(reservation_key)s,
                  'reserved',0
                );
                """,
                {
                    **ids,
                    "user": USER,
                    "org": ORG,
                    "user_text": str(USER),
                    "command_key": f"command:{ids['command']}",
                    "run_key": f"run:{ids['run']}",
                    "attempt_key": f"attempt:{ids['attempt']}",
                    "reservation_key": f"reserve:{ids['step']}",
                    "model_hash": "a" * 64,
                },
            )
    return ids


def _raw_actions(count: int) -> list[dict[str, object]]:
    return [
        {
            "action_id": str(uuid4()),
            "index": index,
            "stable_tool_call_id": f"call-{index}",
            "provider_call_id": None,
            "tool_name": "generate_image",
            "arguments": {"prompt": f"variant {index}"},
            "wave": 0,
            "dependencies": [],
            "blocking": True,
            "policy_decision": "preauthorized",
            "policy_snapshot": {"source": "hash-canonicalization-test"},
            "policy_revision": "v1",
            "retry_disposition": "retry_safe",
        }
        for index in range(count)
    ]


def _complete(
    database_url: str, ids: dict[str, UUID], actions: list[dict[str, object]],
) -> dict[str, object]:
    with _connect(database_url, "everydayai_agent_runtime_worker") as connection:
        _settings(connection, "everydayai_agent_runtime_worker")
        [row] = connection.execute(
            """
            SELECT complete_model_attempt_with_raw_actions(
              %s,%s,0,0,%s,'{}'::jsonb,%s,'tool_calls',%s::jsonb,0,%s::jsonb
            )
            """,
            (
                ids["attempt"], ids["token"], "a" * 64, "d" * 64,
                json.dumps({
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "reasoning_tokens": 0,
                }),
                json.dumps(actions),
            ),
        ).fetchone()
        return row


def _stored_facts(
    database_url: str, ids: dict[str, UUID],
) -> tuple[str, str, str, int]:
    with psycopg.connect(database_url) as connection:
        return connection.execute(
            """
            SELECT step.status,attempt.status,settlement.status,
                   count(action.id)::integer
            FROM agent_model_steps step
            JOIN agent_model_attempts attempt ON attempt.model_step_id=step.id
            JOIN agent_model_credit_settlements settlement
              ON settlement.model_step_id=step.id
            LEFT JOIN agent_actions action ON action.model_step_id=step.id
            WHERE step.id=%s
            GROUP BY step.status,attempt.status,settlement.status
            """,
            (ids["step"],),
        ).fetchone()


def _assert_no_terminal_writes(
    database_url: str, ids: dict[str, UUID],
) -> None:
    assert _stored_facts(database_url, ids) == (
        "running", "dispatching", "reserved", 0,
    )


def _assert_apply_rollback_reapply(database_url: str) -> None:
    regression_ids = _seed_running_step(database_url)
    regression_actions = _raw_actions(1)
    assert _complete(
        database_url, regression_ids, regression_actions,
    )["outcome"] == (
        "request_hash_conflict"
    )
    _assert_no_terminal_writes(database_url, regression_ids)

    _apply(database_url, MIGRATION)
    first = _complete(database_url, regression_ids, regression_actions)
    assert first["outcome"] == "completed"
    assert first["blocking_action_count"] == 1
    assert _stored_facts(database_url, regression_ids) == (
        "completed", "completed", "settled", 1,
    )
    assert _complete(
        database_url, regression_ids, regression_actions,
    )["outcome"] == (
        "already_completed"
    )

    with psycopg.connect(database_url) as connection:
        function_body = connection.execute(
            "SELECT pg_get_functiondef(%s::regprocedure)",
            (FUNCTION_SIGNATURE,),
        ).fetchone()[0]
        assert "canonical_actions" in function_body
        privileges = {
            role: connection.execute(
                "SELECT has_function_privilege(%s,%s,'execute')",
                (role, FUNCTION_SIGNATURE),
            ).fetchone()[0]
            for role in (
                "public", "everydayai_runtime", "everydayai_wecom_runtime",
                "everydayai_worker", "everydayai_agent_runtime_worker",
                "everydayai_projection_worker",
                "everydayai_authorization_worker",
            )
        }
        assert privileges == {
            "public": False,
            "everydayai_runtime": False,
            "everydayai_wecom_runtime": False,
            "everydayai_worker": False,
            "everydayai_agent_runtime_worker": True,
            "everydayai_projection_worker": False,
            "everydayai_authorization_worker": False,
        }
        rls = connection.execute(
            """
            SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class
            WHERE relname IN(
              'agent_actions','agent_model_attempts','agent_model_steps'
            ) ORDER BY relname
            """
        ).fetchall()
        assert rls == [
            ("agent_actions", True, True),
            ("agent_model_attempts", True, True),
            ("agent_model_steps", True, True),
        ]

    _apply(database_url, ROLLBACK)
    with psycopg.connect(database_url) as connection:
        restored = connection.execute(
            "SELECT pg_get_functiondef(%s::regprocedure)",
            (FUNCTION_SIGNATURE,),
        ).fetchone()[0]
        assert "canonical_actions" not in restored
    _apply(database_url, MIGRATION)


def _assert_ten_actions_and_hash_conflicts(database_url: str) -> None:
    ten_ids = _seed_running_step(database_url)
    ten_actions = _raw_actions(10)
    ten_result = _complete(database_url, ten_ids, ten_actions)
    assert ten_result["outcome"] == "completed"
    assert ten_result["blocking_action_count"] == 10
    assert len(ten_result["action_ids"]) == 10
    with psycopg.connect(database_url) as connection:
        stored = connection.execute(
            """
            SELECT action_index,arguments_hash,request_hash,batch_hash
            FROM agent_actions WHERE model_step_id=%s ORDER BY action_index
            """,
            (ten_ids["step"],),
        ).fetchall()
    assert [row[0] for row in stored] == list(range(10))
    assert all(len(value) == 64 for row in stored for value in row[1:])
    assert len({row[3] for row in stored}) == 1

    for hash_name in ("arguments_hash", "request_hash", "batch_hash"):
        tampered_ids = _seed_running_step(database_url)
        tampered = _raw_actions(1)
        tampered[0][hash_name] = "f" * 64
        expected = (
            "batch_hash_conflict"
            if hash_name == "batch_hash"
            else "request_hash_conflict"
        )
        assert _complete(
            database_url, tampered_ids, tampered,
        )["outcome"] == expected
        _assert_no_terminal_writes(database_url, tampered_ids)


def _assert_invalid_batches_and_scope(database_url: str) -> None:
    duplicate_ids = _seed_running_step(database_url)
    duplicate = _raw_actions(2)
    duplicate[1]["action_id"] = duplicate[0]["action_id"]
    with pytest.raises(psycopg.Error, match="AGENT_ACTION_BATCH_INVALID"):
        _complete(database_url, duplicate_ids, duplicate)
    _assert_no_terminal_writes(database_url, duplicate_ids)

    invalid_ids = _seed_running_step(database_url)
    invalid = _raw_actions(10)
    invalid[-1]["policy_snapshot"] = "invalid"
    with pytest.raises(psycopg.Error, match="AGENT_ACTION_BATCH_INVALID"):
        _complete(database_url, invalid_ids, invalid)
    _assert_no_terminal_writes(database_url, invalid_ids)

    wrong_access_ids = _seed_running_step(database_url)
    with _connect(
        database_url, "everydayai_agent_runtime_worker",
    ) as connection:
        connection.execute("SELECT set_config('app.access_kind','worker',false)")
        with pytest.raises(
            psycopg.Error, match="AGENT_RUNTIME_WORKER_SCOPE_REQUIRED",
        ):
            connection.execute(
                """
                SELECT complete_model_attempt_with_raw_actions(
                  %s,%s,0,0,%s,'{}'::jsonb,%s,'tool_calls','{}'::jsonb,0,%s::jsonb
                )
                """,
                (
                    wrong_access_ids["attempt"], wrong_access_ids["token"],
                    "a" * 64, "d" * 64, json.dumps(_raw_actions(1)),
                ),
            )
    _assert_no_terminal_writes(database_url, wrong_access_ids)


def test_raw_action_hashes_apply_rollback_reapply_and_atomic_contracts(
    database: str,
) -> None:
    _assert_apply_rollback_reapply(database)
    _assert_ten_actions_and_hash_conflicts(database)
    _assert_invalid_batches_and_scope(database)
