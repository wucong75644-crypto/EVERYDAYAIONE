"""AR-18-A1.2-B1 behavior on the disposable AR-17 PostgreSQL fixture."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import (
    ORG,
    USER,
    _connect,
    database as ar17_database,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = tuple(
    ROOT / "migrations" / name for name in (
        "227_22_01_agent_runtime_task_cancel_intent.sql",
        "227_22_02_agent_runtime_task_cancel_create_run_fence.sql",
        "227_22_03_agent_runtime_task_cancel_claim_fence.sql",
    )
)
ROLLBACKS = tuple(
    ROOT / "migrations/rollback" / f"{path.stem}_rollback.sql"
    for path in reversed(MIGRATIONS)
)


@dataclass(frozen=True)
class Case:
    task_id: UUID
    message_id: UUID
    session_id: UUID
    command_id: UUID
    idempotency_key: str
    envelope: dict
    scope_kind: str
    scope_user_id: UUID | None
    requested_by_user_id: UUID


@pytest.fixture
def database(ar17_database: str) -> str:
    with psycopg.connect(ar17_database) as conn:
        conn.execute("""
        DO $$ BEGIN
          IF to_regrole('everydayai_agent_model_gateway') IS NULL THEN
            CREATE ROLE everydayai_agent_model_gateway LOGIN;
          END IF;
        END $$;
        """)
        conn.commit()
    _apply(ar17_database)
    return ar17_database


def _apply(url: str) -> None:
    for path in MIGRATIONS:
        with psycopg.connect(url) as conn:
            conn.execute(path.read_text(encoding="utf-8"))
            conn.commit()

def _scope(conn: psycopg.Connection, role: str, *, user: UUID = USER) -> None:
    worker = role == "everydayai_agent_runtime_worker"
    conn.execute("SELECT set_config('app.request_id',%s,false)", (uuid4().hex,))
    conn.execute(
        "SELECT set_config('app.access_kind',%s,false)",
        ("agent_runtime" if worker else "runtime",),
    )
    conn.execute(
        "SELECT set_config('app.actor_user_id',%s,false)",
        ("" if worker else str(user),),
    )
    conn.execute(
        "SELECT set_config('app.org_id',%s,false)",
        ("" if worker else str(ORG),),
    )

def _seed(
    url: str, *, task_status: str = "pending", scope_kind: str = "user",
) -> Case:
    conversation_id, input_id, output_id = uuid4(), uuid4(), uuid4()
    task_id, session_id, command_id = uuid4(), uuid4(), uuid4()
    scope_user_id = USER if scope_kind == "user" else None
    scope_id = str(USER) if scope_kind == "user" else f"wecom:group:{uuid4()}"
    key = f"cancel-b1:{uuid4()}"
    envelope = {
        "schema_revision": 3,
        "run_kind": "user",
        "context_receipt": {"revision": "v1", "session_id": str(session_id)},
        "config_snapshot": {"revision": "v1"},
        "capability_snapshot": {"revision": "v1"},
        "request_identity": {
            "session_id": str(session_id), "idempotency_key": key,
        },
    }
    payload = {
        "task_id": str(task_id), "input_message_id": str(input_id),
        "output_message_id": str(output_id), "run_envelope": envelope,
    }
    with psycopg.connect(url) as conn:
        conn.execute("SET LOCAL ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) "
            "VALUES(%s,%s,%s,%s,%s)",
            (conversation_id, scope_user_id, ORG, scope_kind, scope_id),
        )
        conn.execute(
            "INSERT INTO messages(id,conversation_id,org_id,role,content,status) "
            "VALUES(%s,%s,%s,'user','input','completed'),"
            "(%s,%s,%s,'assistant','output','pending')",
            (input_id, conversation_id, ORG, output_id, conversation_id, ORG),
        )
        conn.execute(
            "INSERT INTO agent_runtime_sessions(id,conversation_id,org_id,user_id,"
            "scope_kind,scope_id,created_by_user_id,agent_definition_id,"
            "agent_definition_revision) VALUES(%s,%s,%s,%s,%s,%s,%s,'default','v1')",
            (session_id, conversation_id, ORG, scope_user_id,
             scope_kind, scope_id, USER),
        )
        conn.execute(
            "INSERT INTO agent_session_commands(id,session_id,org_id,user_id,"
            "command_type,idempotency_key,payload,request_hash) VALUES("
            "%s,%s,%s,%s,'submit_input',%s,%s,md5(jsonb_build_object("
            "'command_type','submit_input','payload',%s::jsonb)::text))",
            (command_id, session_id, ORG, scope_user_id,
             key, Jsonb(payload), Jsonb(payload)),
        )
        conn.execute(
            "INSERT INTO tasks(id,user_id,org_id,conversation_id,type,status,"
            "assistant_message_id,input_message_id,delivery_context) VALUES("
            "%s,%s,%s,%s,'chat',%s,%s,%s,%s)",
            (task_id, USER, ORG, conversation_id, task_status, output_id, input_id,
             Jsonb({"actor": False, "runtime": True,
                    "runtime_session_id": str(session_id),
                    "runtime_command_id": str(command_id)})),
        )
        conn.commit()
    return Case(
        task_id, output_id, session_id, command_id, key, envelope,
        scope_kind, scope_user_id, USER,
    )

def _hash(url: str, case: Case, *, key: str | None = None) -> str:
    with psycopg.connect(url) as conn:
        conn.execute("SET LOCAL ROLE everydayai_owner")
        return conn.execute(
            "SELECT _agent_runtime_task_cancel_request_hash("
            "%s,%s,%s,%s,%s,%s,%s,%s)",
            (case.task_id, case.message_id, ORG, case.scope_user_id,
             case.requested_by_user_id, case.session_id, case.command_id,
             key or case.idempotency_key),
        ).fetchone()[0]


def _facade(
    url: str, case: Case, *, request_hash: str | None = None,
    role: str = "everydayai_runtime", actor_user: UUID | None = None,
    overrides: dict | None = None, barrier: Barrier | None = None,
) -> dict:
    values = {
        "task": case.task_id, "message": case.message_id, "org": ORG,
        "user": case.requested_by_user_id, "session": case.session_id,
        "command": case.command_id,
        "key": case.idempotency_key,
        "hash": request_hash or _hash(url, case),
    }
    values.update(overrides or {})
    with _connect(url, role) as conn:
        _scope(conn, role, user=actor_user or case.requested_by_user_id)
        if barrier is not None:
            barrier.wait(timeout=5)
        return conn.execute(
            "SELECT request_agent_runtime_task_cancel_v1("
            "%s,%s,%s,%s,%s,%s,%s,%s)", tuple(values.values()),
        ).fetchone()[0]

def _worker(
    url: str, query: str, params: tuple = (), *, barrier: Barrier | None = None,
) -> dict:
    with _connect(url, "everydayai_agent_runtime_worker") as conn:
        _scope(conn, "everydayai_agent_runtime_worker")
        if barrier is not None:
            barrier.wait(timeout=5)
        return conn.execute(query, params).fetchone()[0]


def _direct_create(
    url: str, case: Case, *, barrier: Barrier | None = None,
) -> dict:
    return _worker(
        url,
        "SELECT create_agent_run(%s,%s,%s,'user',%s,%s,%s)",
        (case.session_id, case.command_id, str(case.command_id),
         Jsonb(case.envelope["context_receipt"]),
         Jsonb(case.envelope["config_snapshot"]),
         Jsonb(case.envelope["capability_snapshot"])),
        barrier=barrier,
    )


def _scanner(
    url: str, worker_id: str, *, barrier: Barrier | None = None,
) -> dict:
    return _worker(
        url, "SELECT claim_pending_agent_command_and_ensure_run(%s,90,3)",
        (worker_id,), barrier=barrier,
    )


def _run_count(url: str, command_id: UUID) -> int:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT count(*) FROM agent_runs WHERE command_id=%s", (command_id,),
        ).fetchone()[0]

def _intent_count(url: str, command_id: UUID) -> int:
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT count(*) FROM agent_runtime_task_cancel_intents "
            "WHERE submit_command_id=%s", (command_id,),
        ).fetchone()[0]

def _race(left, right) -> list[dict]:
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(left, barrier), pool.submit(right, barrier))
        return [future.result() for future in futures]


def _verify_cancel_first(url: str) -> None:
    case = _seed(url)
    first = _facade(url, case)
    assert set(first) == {"outcome", "intent_id", "run_id"}
    assert first["outcome"] == "cancelled_before_claim"
    assert _facade(url, case) == first
    assert _facade(url, case, request_hash="0" * 64) == {
        "outcome": "idempotency_conflict"
    }
    with psycopg.connect(url) as conn:
        assert conn.execute(
            "SELECT scope_user_id,requested_by_user_id "
            "FROM agent_runtime_task_cancel_intents WHERE id=%s",
            (first["intent_id"],),
        ).fetchone() == (USER, USER)
        conn.execute("SET LOCAL ROLE everydayai_owner")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState,
                           match="TASK_CANCEL_INTENT_IMMUTABLE"):
            conn.execute(
                "UPDATE agent_runtime_task_cancel_intents SET scope_user_id=NULL,"
                "requested_by_user_id=%s,request_hash=%s WHERE id=%s",
                (uuid4(), "1" * 64, first["intent_id"]),
            )
        conn.rollback()
    assert _scanner(url, "cancel-first") == {"outcome": "not_found"}
    assert _direct_create(url, case)["outcome"] == "already_exists"
    assert _run_count(url, case.command_id) == 1


def _verify_wecom_channel_scope(url: str) -> None:
    case = _seed(url, scope_kind="channel")
    result = _facade(url, case, role="everydayai_wecom_runtime")
    assert result["outcome"] == "cancelled_before_claim"
    with psycopg.connect(url) as conn:
        fact = conn.execute(
            "SELECT scope_user_id,requested_by_user_id "
            "FROM agent_runtime_task_cancel_intents WHERE id=%s",
            (result["intent_id"],),
        ).fetchone()
    assert fact == (None, USER)
    assert _intent_count(url, case.command_id) == 1
    assert _run_count(url, case.command_id) == 1
    blocked = _seed(url, scope_kind="channel")
    with psycopg.connect(url) as conn:
        conn.execute("SET LOCAL ROLE everydayai_owner")
        conn.execute("UPDATE org_members SET status='disabled' WHERE org_id=%s AND user_id=%s", (ORG, USER))
        conn.commit()
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="TASK_CANCEL_BINDING_MISMATCH"):
        _facade(url, blocked, role="everydayai_wecom_runtime")
    with psycopg.connect(url) as conn:
        conn.execute("SET LOCAL ROLE everydayai_owner")
        conn.execute("UPDATE org_members SET status='active' WHERE org_id=%s AND user_id=%s", (ORG, USER))
        conn.commit()
    assert _facade(url, blocked, role="everydayai_wecom_runtime")["outcome"] == "cancelled_before_claim"

def _verify_claim_first_states(url: str) -> None:
    for claim_state in ("active", "expired", "terminal"):
        case = _seed(url)
        claimed = _scanner(url, f"claim-{claim_state}")
        assert claimed["outcome"] == "claimed"
        with psycopg.connect(url) as conn:
            conn.execute("SET LOCAL ROLE everydayai_owner")
            if claim_state == "expired":
                conn.execute(
                    "UPDATE agent_command_claims SET lease_expires_at=clock_timestamp()-interval '1 second' "
                    "WHERE command_id=%s", (case.command_id,),
                )
            elif claim_state == "terminal":
                conn.execute(
                    "UPDATE agent_command_claims SET status='completed',outcome='completed',"
                    "finished_at=clock_timestamp() WHERE command_id=%s", (case.command_id,),
                )
            conn.commit()
        assert _facade(url, case)["outcome"] == "cancelled"
        assert _run_count(url, case.command_id) == 1

    running = _seed(url)
    run_id = UUID(_scanner(url, "running-create")["run_id"])
    assert _worker(
        url, "SELECT claim_agent_run(%s,'running-owner',90,3)", (run_id,),
    )["outcome"] == "claimed"
    assert _facade(url, running)["outcome"] == "cancelled"

    terminal = _seed(url, task_status="completed")
    terminal_run = UUID(_scanner(url, "terminal-create")["run_id"])
    with psycopg.connect(url) as conn:
        conn.execute("SET LOCAL ROLE everydayai_owner")
        before = conn.execute(
            "SELECT task.status,task.delivery_context,message.status,message.content "
            "FROM tasks task JOIN messages message ON message.id=task.assistant_message_id "
            "WHERE task.id=%s", (terminal.task_id,),
        ).fetchone()
        conn.execute(
            "UPDATE agent_runs SET status='completed',completed_at=clock_timestamp(),"
            "terminal_reason='completed',state_version=state_version+1 WHERE id=%s",
            (terminal_run,),
        )
        conn.commit()
    assert _facade(url, terminal)["outcome"] == "terminal_conflict"
    with psycopg.connect(url) as conn:
        after = conn.execute(
            "SELECT task.status,task.delivery_context,message.status,message.content "
            "FROM tasks task JOIN messages message ON message.id=task.assistant_message_id "
            "WHERE task.id=%s", (terminal.task_id,),
        ).fetchone()
    assert after == before


def _verify_root_run_races(url: str) -> None:
    facade_scanner = _seed(url)
    outcomes = {item["outcome"] for item in _race(
        lambda barrier: _facade(url, facade_scanner, barrier=barrier),
        lambda barrier: _scanner(url, "facade-scanner", barrier=barrier),
    )}
    assert outcomes in (
        {"cancelled_before_claim", "not_found"}, {"cancelled", "claimed"},
    )
    assert _intent_count(url, facade_scanner.command_id) == 1
    assert _run_count(url, facade_scanner.command_id) == 1

    facade_direct = _seed(url)
    outcomes = {item["outcome"] for item in _race(
        lambda barrier: _facade(url, facade_direct, barrier=barrier),
        lambda barrier: _direct_create(url, facade_direct, barrier=barrier),
    )}
    assert outcomes in (
        {"cancelled_before_claim", "already_exists"},
        {"cancelled", "created"},
    )
    assert _intent_count(url, facade_direct.command_id) == 1
    assert _run_count(url, facade_direct.command_id) == 1

    scanner_direct = _seed(url)
    outcomes = {item["outcome"] for item in _race(
        lambda barrier: _scanner(url, "scanner-direct", barrier=barrier),
        lambda barrier: _direct_create(url, scanner_direct, barrier=barrier),
    )}
    assert outcomes in ({"claimed", "created"}, {"claimed", "already_exists"})
    assert _intent_count(url, scanner_direct.command_id) == 0
    assert _run_count(url, scanner_direct.command_id) == 1


def _verify_binding_permissions_and_flags(url: str) -> None:
    mismatch_fields = {
        "task": uuid4(), "message": uuid4(), "org": uuid4(),
        "user": uuid4(), "session": uuid4(), "command": uuid4(),
    }
    for field, value in mismatch_fields.items():
        case = _seed(url)
        with pytest.raises(psycopg.errors.InsufficientPrivilege,
                           match="TASK_CANCEL_BINDING_MISMATCH"):
            _facade(url, case, overrides={field: value})
        assert _run_count(url, case.command_id) == 0
    marker = _seed(url)
    with psycopg.connect(url) as conn:
        conn.execute("SET LOCAL ROLE everydayai_owner")
        conn.execute(
            "UPDATE tasks SET delivery_context=delivery_context||'{\"runtime\":false}' "
            "WHERE id=%s", (marker.task_id,),
        )
        conn.commit()
    with pytest.raises(psycopg.errors.InsufficientPrivilege,
                       match="TASK_CANCEL_BINDING_MISMATCH"):
        _facade(url, marker)
    tampered = _seed(url)
    with psycopg.connect(url) as conn:
        conn.execute("SET LOCAL ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_session_commands SET payload=payload||'{\"tampered\":true}' "
            "WHERE id=%s", (tampered.command_id,),
        )
        conn.commit()
    with pytest.raises(psycopg.errors.InsufficientPrivilege,
                       match="TASK_CANCEL_BINDING_MISMATCH"):
        _facade(url, tampered)
    scoped = _seed(url)
    with pytest.raises(psycopg.errors.InsufficientPrivilege,
                       match="TASK_CANCEL_BINDING_MISMATCH"):
        _facade(url, scoped, actor_user=uuid4())

    with psycopg.connect(url) as conn:
        signature = "request_agent_runtime_task_cancel_v1(uuid,uuid,uuid,uuid,uuid,uuid,text,text)"
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_runtime',%s,'execute'),"
            "has_function_privilege('everydayai_wecom_runtime',%s,'execute'),"
            "has_function_privilege('everydayai_worker',%s,'execute'),"
            "has_function_privilege('public',%s,'execute')", (signature,) * 4,
        ).fetchone() == (True, True, False, False)
        assert conn.execute(
            "SELECT has_table_privilege('everydayai_runtime',"
            "'agent_runtime_task_cancel_intents','select'),"
            "has_table_privilege('everydayai_worker',"
            "'agent_runtime_task_cancel_intents','select')"
        ).fetchone() == (False, False)
        create_signature = "create_agent_run(uuid,uuid,text,text,jsonb,jsonb,jsonb)"
        claim_signature = "claim_pending_agent_command_and_ensure_run(text,integer,integer)"
        for root_signature in (create_signature, claim_signature):
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_agent_runtime_worker',%s,'execute'),"
                "has_function_privilege('everydayai_worker',%s,'execute'),"
                "has_function_privilege('everydayai_runtime',%s,'execute')",
                (root_signature,) * 3,
            ).fetchone() == (True, False, False)
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE oid='agent_runtime_task_cancel_intents'::regclass"
        ).fetchone() == (True, True)
        assert conn.execute(
            "SELECT prosecdef,proconfig FROM pg_proc WHERE oid=%s::regprocedure",
            (signature,),
        ).fetchone() == (True, ["search_path=pg_catalog, public"])
        assert conn.execute(
            "SELECT ingress_enabled,command_claim_enabled,action_dispatch_enabled "
            "FROM agent_runtime_control WHERE singleton"
        ).fetchone() == (False, False, False)


def _verify_rollback_reapply(url: str) -> None:
    with psycopg.connect(url) as conn:
        for rollback in ROLLBACKS:
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState,
                               match="TASK_CANCEL_ROLLBACK_FACTS_EXIST"):
                conn.execute(rollback.read_text(encoding="utf-8"))
            conn.rollback()
        conn.execute("SET LOCAL ROLE everydayai_owner")
        conn.execute("TRUNCATE agent_runtime_task_cancel_intents")
        conn.commit()
    for path in ROLLBACKS:
        with psycopg.connect(url) as conn:
            conn.execute(path.read_text(encoding="utf-8"))
            conn.commit()
    with psycopg.connect(url) as conn:
        assert conn.execute(
            "SELECT to_regclass('agent_runtime_task_cancel_intents')"
        ).fetchone()[0] is None
        assert "task_cancel_intent" not in conn.execute(
            "SELECT pg_get_functiondef('create_agent_run(uuid,uuid,text,text,jsonb,jsonb,jsonb)'::regprocedure)"
        ).fetchone()[0]
        for signature in (
            "create_agent_run(uuid,uuid,text,text,jsonb,jsonb,jsonb)",
            "claim_pending_agent_command_and_ensure_run(text,integer,integer)",
        ):
            assert conn.execute(
                "SELECT has_function_privilege('everydayai_agent_runtime_worker',%s,'execute'),"
                "has_function_privilege('everydayai_worker',%s,'execute'),"
                "has_function_privilege('everydayai_runtime',%s,'execute')",
                (signature,) * 3,
            ).fetchone() == (True, False, False)
    _apply(url)
    reapplied = _seed(url)
    assert _facade(url, reapplied)["outcome"] == "cancelled_before_claim"
    assert _run_count(url, reapplied.command_id) == 1


def test_task_cancel_intent_full_contract(database: str) -> None:
    _verify_cancel_first(database)
    _verify_wecom_channel_scope(database)
    _verify_claim_first_states(database)
    _verify_root_run_races(database)
    _verify_binding_permissions_and_flags(database)
    _verify_rollback_reapply(database)
