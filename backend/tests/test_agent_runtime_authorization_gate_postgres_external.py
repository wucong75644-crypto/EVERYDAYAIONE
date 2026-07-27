"""Real PostgreSQL authorization recovery, gate, fencing, and revoke contract."""

from __future__ import annotations

import json
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest


pytestmark = pytest.mark.external
DATABASE_URL = os.getenv("AR16_TEST_DATABASE_URL", "")


@pytest.fixture(scope="module", autouse=True)
def dedicated_database() -> None:
    if os.getenv("RUN_AR16_DB_TEST") != "1" or not DATABASE_URL:
        pytest.skip("RUN_AR16_DB_TEST=1 and AR16_TEST_DATABASE_URL required")
    if "ar16" not in DATABASE_URL.lower() and "ar1416" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR16 database name required")


def _execute(
    sql: str, params: tuple[object, ...] = (), *, role: str | None = None,
) -> list[dict[str, object]]:
    with psycopg.connect(
        DATABASE_URL, row_factory=dict_row,
        cursor_factory=psycopg.ClientCursor,
    ) as connection:
        with connection.cursor() as cursor:
            if role:
                cursor.execute(f"SET SESSION AUTHORIZATION {role}")
                cursor.execute(
                    "SELECT set_config('app.access_kind',%s,false)",
                    ("worker" if role == "everydayai_worker" else "runtime",),
                )
                cursor.execute(
                    "SELECT set_config('app.request_id','ar16-gate-test',false)",
                )
            cursor.execute(sql, params)
            return list(cursor.fetchall()) if cursor.description else []


def _decoded(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else json.loads(str(value))


def _seed_awaiting_action() -> dict[str, object]:
    ids = {name: uuid4() for name in (
        "user", "conversation", "session", "command", "run", "step", "action",
    )}
    _execute(
        """
        SET ROLE everydayai_owner;
        INSERT INTO users(id,credits) VALUES (%(user)s,100);
        INSERT INTO conversations(id,user_id,scope_type,scope_id)
        VALUES (%(conversation)s,%(user)s,'user',%(user_text)s);
        INSERT INTO agent_runtime_sessions(
          id,conversation_id,user_id,scope_kind,scope_id,created_by_user_id,
          agent_definition_id,agent_definition_revision
        ) VALUES (
          %(session)s,%(conversation)s,%(user)s,'user',%(user_text)s,
          %(user)s,'default','v1'
        );
        INSERT INTO agent_session_commands(
          id,session_id,user_id,command_type,idempotency_key,payload,request_hash
        ) VALUES (
          %(command)s,%(session)s,%(user)s,'submit_input',%(command_key)s,
          '{}','11111111111111111111111111111111'
        );
        INSERT INTO agent_runs(
          id,session_id,command_id,user_id,run_kind,status,idempotency_key,
          request_hash,blocking_action_count
        ) VALUES (
          %(run)s,%(session)s,%(command)s,%(user)s,'user','waiting_actions',
          %(run_key)s,'22222222222222222222222222222222',1
        );
        INSERT INTO agent_model_steps(
          id,run_id,session_id,user_id,step_number,status,model_id,provider,
          model_revision,prompt_revision,tool_catalog_revision,stop_reason,
          completed_at
        ) VALUES (
          %(step)s,%(run)s,%(session)s,%(user)s,1,'completed','model',
          'provider','v1','v1','v1','tool_calls',clock_timestamp()
        );
        INSERT INTO agent_actions(
          id,session_id,run_id,model_step_id,user_id,action_index,
          stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,
          batch_hash,blocking,policy_decision,policy_snapshot,policy_revision,
          retry_disposition,status
        ) VALUES (
          %(action)s,%(session)s,%(run)s,%(step)s,%(user)s,0,
          %(tool_call)s,'resource.read','{"key":"one"}',%(arguments_hash)s,
          %(request_hash)s,%(batch_hash)s,true,'requires_authorization',
          '{"permission_mode":"ask"}','policy-v1','retry_safe',
          'awaiting_authorization'
        );
        RESET ROLE;
        """,
        {
            **ids,
            "user_text": str(ids["user"]),
            "command_key": f"command-{ids['command']}",
            "run_key": f"run-{ids['run']}",
            "tool_call": f"call-{ids['action']}",
            "arguments_hash": "a" * 64,
            "request_hash": "b" * 64,
            "batch_hash": "c" * 64,
        },
    )
    return ids


def _open_and_approve(ids: dict[str, object]) -> dict[str, object]:
    opened = _decoded(_execute(
        """
        SELECT open_agent_authorization_interaction(
            %s,0,'{"prompt":"approve"}',%s,900
        ) AS value
        """,
        (ids["action"], "d" * 64),
        role="everydayai_worker",
    )[0]["value"])
    interaction = opened["interaction"]
    resolved = _decoded(_execute(
        """
        WITH settings AS (
          SELECT set_config('app.actor_user_id',%s,false),
                 set_config('app.org_id','',false)
        )
        SELECT resolve_agent_authorization_interaction(
          %s,%s,'approve',%s,'{"resource":"one"}','action',NULL,900
        ) AS value FROM settings
        """,
        (
            str(ids["user"]), interaction["id"],
            interaction["state_version"], "e" * 64,
        ),
        role="everydayai_runtime",
    )[-1]["value"])
    return resolved["grant"]


def _record_activate_claim_gate(
    ids: dict[str, object], grant: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    recovery = _decoded(_execute(
        "SELECT claim_next_agent_authorization_recovery('auth-worker',120) value",
        role="everydayai_worker",
    )[0]["value"])
    receipt = _decoded(_execute(
        """
        SELECT record_agent_policy_receipt(
          %s,%s,'resource_read',1,'policy-v1','allow',%s,
          '{"resource":"one"}',ARRAY['ACTION_GRANT_VALID'],ARRAY['audit'],%s,300
        ) value
        """,
        (
            ids["action"], "a" * 64, grant["id"],
            hashlib.sha256(str(ids["action"]).encode()).hexdigest(),
        ),
        role="everydayai_worker",
    )[0]["value"])["receipt"]
    activated = _decoded(_execute(
        """
        SELECT activate_agent_authorized_action(
          %s,0,%s,%s,%s,%s
        ) value
        """,
        (
            ids["action"], recovery["interaction_id"],
            recovery["recovery_token"], recovery["state_version"],
            receipt["id"],
        ),
        role="everydayai_worker",
    )[0]["value"])
    assert activated["outcome"] == "activated"
    claimed = _decoded(_execute(
        "SELECT claim_ready_agent_action_snapshots(%s,%s,1,120) value",
        (
            f"worker-{ids['action']}",
            f"claim-{ids['action']}",
        ),
        role="everydayai_worker",
    )[0]["value"])["snapshots"][0]
    gate = _decoded(_execute(
        """
        SELECT gate_agent_action_dispatch(
          %s,%s,%s,%s,%s,'resource_read',1,'policy-v1','idempotent_replay'
        ) value
        """,
        (
            claimed["id"], claimed["execution_token"],
            claimed["state_version"], claimed["request_hash"], receipt["id"],
        ),
        role="everydayai_worker",
    )[0]["value"])
    return claimed, gate


def test_approve_receipt_activate_claim_gate_is_atomic_and_idempotent() -> None:
    ids = _seed_awaiting_action()
    grant = _open_and_approve(ids)
    claimed, gate = _record_activate_claim_gate(ids, grant)

    assert gate["outcome"] == "dispatch_authorized"
    replay = _decoded(_execute(
        """
        SELECT gate_agent_action_dispatch(
          %s,%s,%s,%s,%s,'resource_read',1,'policy-v1','idempotent_replay'
        ) value
        """,
        (
            claimed["id"], claimed["execution_token"],
            gate["state_version"], claimed["request_hash"],
            _execute(
                "SELECT policy_receipt_id FROM agent_action_dispatch_intents "
                "WHERE attempt_id=%s", (claimed["id"],),
            )[0]["policy_receipt_id"],
        ),
        role="everydayai_worker",
    )[0]["value"])
    assert replay["outcome"] == "already_authorized"
    counts = _execute(
        """
        SELECT
          (SELECT count(*) FROM agent_action_dispatch_intents
            WHERE action_id=%s) AS intents,
          (SELECT count(*) FROM agent_authorization_grant_uses
            WHERE action_id=%s) AS uses
        """,
        (ids["action"], ids["action"]),
    )[0]
    assert counts == {"intents": 1, "uses": 1}


def test_claim_then_revoke_closes_attempt_and_fences_old_token() -> None:
    ids = _seed_awaiting_action()
    grant = _open_and_approve(ids)
    recovery = _decoded(_execute(
        "SELECT claim_next_agent_authorization_recovery('auth-worker-2',120) value",
        role="everydayai_worker",
    )[0]["value"])
    receipt = _decoded(_execute(
        """
        SELECT record_agent_policy_receipt(
          %s,%s,'resource_read',1,'policy-v1','allow',%s,
          '{}',ARRAY['ACTION_GRANT_VALID'],ARRAY['audit'],%s,300
        ) value
        """,
        (ids["action"], "a" * 64, grant["id"], "1" * 64),
        role="everydayai_worker",
    )[0]["value"])["receipt"]
    _execute(
        "SELECT activate_agent_authorized_action(%s,0,%s,%s,%s,%s)",
        (
            ids["action"], recovery["interaction_id"],
            recovery["recovery_token"], recovery["state_version"],
            receipt["id"],
        ),
        role="everydayai_worker",
    )
    claimed = _decoded(_execute(
        "SELECT claim_ready_agent_action_snapshots('worker-2','claim-2',1,120) value",
        role="everydayai_worker",
    )[0]["value"])["snapshots"][0]
    _execute(
        """
        WITH settings AS (
          SELECT set_config('app.actor_user_id',%s,false),
                 set_config('app.org_id','',false)
        )
        SELECT revoke_agent_authorization_grant(%s) FROM settings
        """,
        (str(ids["user"]), grant["id"]),
        role="everydayai_runtime",
    )
    state = _execute(
        """
        SELECT action.status action_status, action.terminal_reason,
               attempt.status attempt_status, attempt.execution_token,
               attempt.lease_expires_at, run.blocking_action_count
          FROM agent_actions action
          JOIN agent_action_attempts attempt ON attempt.action_id=action.id
          JOIN agent_runs run ON run.id=action.run_id
         WHERE action.id=%s
        """,
        (ids["action"],),
    )[0]
    assert state == {
        "action_status": "rejected",
        "terminal_reason": "authorization_revoked",
        "attempt_status": "cancelled",
        "execution_token": None,
        "lease_expires_at": None,
        "blocking_action_count": 0,
    }
    lost = _decoded(_execute(
        """
        SELECT gate_agent_action_dispatch(
          %s,%s,%s,%s,%s,'resource_read',1,'policy-v1','idempotent_replay'
        ) value
        """,
        (
            claimed["id"], claimed["execution_token"],
            claimed["state_version"], claimed["request_hash"], receipt["id"],
        ),
        role="everydayai_worker",
    )[0]["value"])
    assert lost["outcome"] == "ownership_lost"


def test_gate_and_revoke_have_one_linearized_owner() -> None:
    ids = _seed_awaiting_action()
    grant = _open_and_approve(ids)
    claimed, gate = _record_activate_claim_gate(ids, grant)
    with ThreadPoolExecutor(max_workers=2) as pool:
        revoke = pool.submit(
            _execute,
            """
            WITH settings AS (
              SELECT set_config('app.actor_user_id',%s,false),
                     set_config('app.org_id','',false)
            )
            SELECT revoke_agent_authorization_grant(%s) FROM settings
            """,
            (str(ids["user"]), grant["id"]),
            role="everydayai_runtime",
        )
        readback = pool.submit(
            _execute,
            "SELECT get_agent_action_dispatch_intent(%s,%s) value",
            (claimed["id"], f"worker-{ids['action']}"),
            role="everydayai_worker",
        )
        revoke.result()
        intent = _decoded(readback.result()[0]["value"])
    assert gate["outcome"] == "dispatch_authorized"
    assert intent["outcome"] == "found"
    assert _execute(
        "SELECT status FROM agent_actions WHERE id=%s", (ids["action"],),
    )[0]["status"] == "running"
