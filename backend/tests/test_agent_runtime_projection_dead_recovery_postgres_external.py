"""Real PostgreSQL dead Projection inspection, recovery, ordering, and ACL."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
import os
import time
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest


pytestmark = pytest.mark.external
DATABASE_URL = os.getenv("AR16_TEST_DATABASE_URL", "")
ADMIN_ID = "44444444-4444-4444-4444-444444444444"
ORG_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module", autouse=True)
def dedicated_database() -> None:
    if os.getenv("RUN_AR16_DB_TEST") != "1" or not DATABASE_URL:
        pytest.skip("RUN_AR16_DB_TEST=1 and AR16_TEST_DATABASE_URL required")
    if "ar1416" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR14-16 database required")
    _execute("""
        SET ROLE everydayai_owner;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user';
        UPDATE users SET role='super_admin' WHERE id=%s;
        CREATE OR REPLACE FUNCTION tenant_platform_admin()
        RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path=pg_catalog,public AS $$
          SELECT session_user='everydayai_runtime'
             AND current_setting('app.access_kind',true)='runtime'
             AND tenant_actor_user_id() IS NOT NULL
             AND EXISTS (
               SELECT 1 FROM users
                WHERE id=tenant_actor_user_id()
                  AND role='super_admin' AND status='active')
        $$;
        REVOKE ALL ON FUNCTION tenant_platform_admin()
        FROM PUBLIC,everydayai_wecom_runtime,everydayai_worker,everydayai_sync;
        GRANT EXECUTE ON FUNCTION tenant_platform_admin()
        TO everydayai_runtime;
        RESET ROLE;
    """, (ADMIN_ID,))


def _execute(
    sql: str, params: tuple[object, ...] = (), *, role: str | None = None,
    actor: str = ADMIN_ID, org: str = ORG_ID,
) -> list[dict[str, object]]:
    with psycopg.connect(
        DATABASE_URL, row_factory=dict_row,
        cursor_factory=psycopg.ClientCursor,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout='3s'")
            if role:
                cursor.execute(f"SET SESSION AUTHORIZATION {role}")
                cursor.execute(
                    "SELECT set_config('app.access_kind',%s,false)",
                    ("worker" if role == "everydayai_worker" else "runtime",),
                )
                cursor.execute(
                    "SELECT set_config('app.actor_user_id',%s,false)",
                    (actor,),
                )
                cursor.execute(
                    "SELECT set_config('app.org_id',%s,false)", (org,),
                )
                cursor.execute(
                    "SELECT set_config('app.request_id',%s,false)",
                    (f"dead-recovery-{uuid4()}",),
                )
            cursor.execute(sql, params)
            return list(cursor.fetchall()) if cursor.description else []


def _decoded(value: object) -> dict[str, object] | list[object]:
    return value if isinstance(value, (dict, list)) else json.loads(str(value))


def _seed_stream(
    *, first_status: str = "dead", first_attempts: int = 8,
    projection_kind: str = "web_runtime",
) -> dict[str, str]:
    ids = {name: str(uuid4()) for name in (
        "conversation", "session", "event1", "event2", "outbox1", "outbox2",
    )}
    _execute("""
        SET ROLE everydayai_owner;
        INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id)
        VALUES (%(conversation)s,%(admin)s,%(org)s,'user',%(admin)s);
        INSERT INTO agent_runtime_sessions(
          id,conversation_id,org_id,user_id,scope_kind,scope_id,
          created_by_user_id,agent_definition_id,agent_definition_revision,
          next_event_sequence
        ) VALUES (
          %(session)s,%(conversation)s,%(org)s,%(admin)s,'user',%(admin)s,
          %(admin)s,'default','v1',3
        );
        INSERT INTO agent_runtime_events(
          id,session_id,sequence,org_id,user_id,scope_kind,scope_id,event_type,
          correlation_id,actor_type,payload,payload_hash
        ) VALUES
          (%(event1)s,%(session)s,1,%(org)s,%(admin)s,'user',%(admin)s,
           'session.created',%(event1)s,'system','{}','hash'),
          (%(event2)s,%(session)s,2,%(org)s,%(admin)s,'user',%(admin)s,
           'model_step.created',%(event2)s,'system','{}','hash');
        INSERT INTO agent_projection_outbox(
          id,event_id,session_id,org_id,user_id,projection_kind,status,
          attempt_count,last_error_code,next_attempt_at
        ) VALUES
          (%(outbox1)s,%(event1)s,%(session)s,%(org)s,%(admin)s,
           %(kind)s,%(status)s,%(attempts)s,'forced_failure',clock_timestamp()),
          (%(outbox2)s,%(event2)s,%(session)s,%(org)s,%(admin)s,
           %(kind)s,'pending',0,NULL,clock_timestamp());
        INSERT INTO agent_compat_projection_checkpoints(
          session_id,projection_kind
        ) SELECT %(session)s,%(kind)s
        WHERE %(kind)s IN ('web_runtime','wecom');
        RESET ROLE;
    """, {
        **ids, "admin": ADMIN_ID, "org": ORG_ID,
        "kind": projection_kind, "status": first_status,
        "attempts": first_attempts,
    })
    return ids


def _requeue(
    outbox_id: str, request_id: str, not_before: datetime,
    *, version: int = 0, attempts: int = 8,
    reason: str = "operator verified transient projection failure",
) -> dict[str, object]:
    value = _execute("""
        SELECT requeue_agent_projection_dead(
          %s,'dead',%s,%s,%s,%s,%s
        ) value
    """, (
        outbox_id, version, attempts, request_id, reason, not_before,
    ), role="everydayai_runtime")[0]["value"]
    return _decoded(value)  # type: ignore[return-value]


def test_dead_head_requeues_once_and_unblocks_ordered_stream() -> None:
    ids = _seed_stream()
    request_id = str(uuid4())
    not_before = datetime.now(UTC) + timedelta(milliseconds=100)

    first = _requeue(ids["outbox1"], request_id, not_before)
    replay = _requeue(ids["outbox1"], request_id, not_before)
    conflict = _requeue(
        ids["outbox1"], request_id, not_before, reason="different reason",
    )
    assert first["outcome"] == "requeued"
    assert replay["outcome"] == "already_requeued"
    assert replay["audit_id"] == first["audit_id"]
    assert conflict["outcome"] == "recovery_request_conflict"

    inspected = _decoded(_execute(
        "SELECT get_agent_projection_dead_item(%s) value",
        (ids["outbox1"],), role="everydayai_runtime",
    )[0]["value"])
    assert inspected["outcome"] == "not_found"
    assert "payload" not in json.dumps(inspected)

    time.sleep(0.15)
    generic = _decoded(_execute(
        "SELECT claim_agent_projection_outbox(100,60) value",
        role="everydayai_worker",
    )[0]["value"])
    assert all(row["projection_kind"] == "audit" for row in generic)
    compat_claims = _decoded(_execute(
        "SELECT claim_agent_compat_projection_outbox(100,60) value",
        role="everydayai_worker",
    )[0]["value"])
    [claimed] = [
        row for row in compat_claims if row["id"] == ids["outbox1"]
    ]
    assert claimed["id"] == ids["outbox1"]
    applied = _decoded(_execute(
        "SELECT apply_agent_compat_projection(%s,%s,'checkpoint_only') value",
        (claimed["id"], claimed["lease_token"]),
        role="everydayai_worker",
    )[0]["value"])
    assert applied["outcome"] == "applied"
    next_claims = _decoded(_execute(
        "SELECT claim_agent_compat_projection_outbox(100,60) value",
        role="everydayai_worker",
    )[0]["value"])
    [next_claim] = [
        row for row in next_claims if row["id"] == ids["outbox2"]
    ]
    _execute(
        "SELECT apply_agent_compat_projection(%s,%s,'checkpoint_only')",
        (next_claim["id"], next_claim["lease_token"]),
        role="everydayai_worker",
    )
    state = _execute("""
        SELECT checkpoint.through_sequence,
          (SELECT count(*) FROM agent_compat_projection_results
            WHERE session_id=%s) result_count
        FROM agent_compat_projection_checkpoints checkpoint
        WHERE session_id=%s AND projection_kind='web_runtime'
    """, (ids["session"], ids["session"]))[0]
    assert state == {"through_sequence": 2, "result_count": 2}
    time.sleep(1)
    late_replay = _requeue(ids["outbox1"], request_id, not_before)
    assert late_replay["outcome"] == "already_requeued"


def test_concurrent_requeue_and_second_failure_preserve_history() -> None:
    ids = _seed_stream()
    not_before = datetime.now(UTC) + timedelta(milliseconds=100)
    requests = (str(uuid4()), str(uuid4()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_requeue, ids["outbox1"], value, not_before)
            for value in requests
        ]
        outcomes = {future.result()["outcome"] for future in futures}
    assert outcomes == {"requeued", "not_dead"}
    assert _execute(
        "SELECT count(*) count FROM agent_projection_dead_recoveries "
        "WHERE outbox_id=%s", (ids["outbox1"],),
    )[0]["count"] == 1

    time.sleep(0.15)
    compat_claims = _decoded(_execute(
        "SELECT claim_agent_compat_projection_outbox(100,60) value",
        role="everydayai_worker",
    )[0]["value"])
    [claimed] = [
        row for row in compat_claims if row["id"] == ids["outbox1"]
    ]
    assert claimed["attempt_count"] == 9
    _execute(
        "SELECT fail_agent_projection_outbox(%s,%s,'failed_again')",
        (claimed["id"], claimed["lease_token"]),
        role="everydayai_worker",
    )
    facts = _execute("""
        SELECT outbox.status,outbox.attempt_count,outbox.last_error_code,
               recovery.previous_attempt_count,
               recovery.previous_last_error_code
        FROM agent_projection_outbox outbox
        JOIN agent_projection_dead_recoveries recovery
          ON recovery.outbox_id=outbox.id
        WHERE outbox.id=%s
    """, (ids["outbox1"],))[0]
    assert facts == {
        "status": "dead", "attempt_count": 9,
        "last_error_code": "failed_again",
        "previous_attempt_count": 8,
        "previous_last_error_code": "forced_failure",
    }
    duplicate = _seed_stream()
    duplicate_request = str(uuid4())
    duplicate_not_before = datetime.now(UTC) + timedelta(milliseconds=100)
    with ThreadPoolExecutor(max_workers=2) as pool:
        same_request = [
            pool.submit(
                _requeue, duplicate["outbox1"], duplicate_request,
                duplicate_not_before,
            )
            for _ in range(2)
        ]
        same_outcomes = {item.result()["outcome"] for item in same_request}
    assert same_outcomes == {"requeued", "already_requeued"}


def test_stale_non_dead_scope_acl_and_audit_claim_contract() -> None:
    dead = _seed_stream()
    pending = _seed_stream(first_status="pending", first_attempts=8)
    processing = _seed_stream(first_status="pending", first_attempts=8)
    audit = _seed_stream(
        first_status="pending", first_attempts=0, projection_kind="audit",
    )
    before = _execute(
        "SELECT count(*) count FROM agent_projection_dead_recoveries",
    )[0]["count"]
    not_before = datetime.now(UTC) + timedelta(seconds=1)
    assert _requeue(
        dead["outbox1"], str(uuid4()), not_before, version=1,
    )["outcome"] == "stale_version"
    assert _requeue(
        dead["outbox1"], str(uuid4()), not_before, attempts=9,
    )["outcome"] == "attempt_count_conflict"
    assert _requeue(
        pending["outbox1"], str(uuid4()), not_before,
    )["outcome"] == "not_dead"
    _execute("""
        SET ROLE everydayai_owner;
        UPDATE agent_projection_outbox
           SET status='processing',lease_token=gen_random_uuid(),
               lease_expires_at=clock_timestamp()-interval '1 minute'
         WHERE id=%s;
        RESET ROLE;
    """, (processing["outbox1"],))
    assert _requeue(
        processing["outbox1"], str(uuid4()), not_before,
    )["outcome"] == "not_dead"
    assert _execute(
        "SELECT count(*) count FROM agent_projection_dead_recoveries",
    )[0]["count"] == before

    claimed = _decoded(_execute(
        "SELECT claim_agent_projection_outbox(100,60) value",
        role="everydayai_worker",
    )[0]["value"])
    assert claimed
    assert {row["projection_kind"] for row in claimed} == {"audit"}
    assert any(row["id"] == audit["outbox1"] for row in claimed)

    matrix = _execute("""
        SELECT
          has_function_privilege(
            'everydayai_runtime',
            'requeue_agent_projection_dead('
            'uuid,text,bigint,integer,uuid,text,timestamptz)','EXECUTE') runtime,
          has_function_privilege(
            'everydayai_worker',
            'requeue_agent_projection_dead('
            'uuid,text,bigint,integer,uuid,text,timestamptz)','EXECUTE') worker,
          has_function_privilege(
            'everydayai_wecom_runtime',
            'get_agent_projection_dead_item(uuid)','EXECUTE') wecom,
          has_function_privilege(
            'everydayai_sync',
            'list_agent_projection_dead_items(integer)','EXECUTE') sync,
          has_function_privilege(
            'public',
            'requeue_agent_projection_dead('
            'uuid,text,bigint,integer,uuid,text,timestamptz)','EXECUTE') public,
          has_table_privilege(
            'everydayai_runtime',
            'agent_projection_dead_recoveries','SELECT') direct_runtime,
          has_table_privilege(
            'everydayai_worker',
            'agent_projection_dead_recoveries','SELECT') direct_worker
    """)[0]
    assert matrix == {
        "runtime": True, "worker": False, "wecom": False, "sync": False,
        "public": False,
        "direct_runtime": False, "direct_worker": False,
    }
    rls = _execute("""
        SELECT relrowsecurity,relforcerowsecurity
        FROM pg_class WHERE relname='agent_projection_dead_recoveries'
    """)[0]
    assert rls == {"relrowsecurity": True, "relforcerowsecurity": True}

    ordinary = str(uuid4())
    _execute(
        "SET ROLE everydayai_owner; "
        "INSERT INTO users(id,status,role) VALUES (%s,'active','user'); "
        "RESET ROLE", (ordinary,),
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _execute(
            "SELECT list_agent_projection_dead_items(10)",
            role="everydayai_runtime", actor=ordinary,
        )
    assert _decoded(_execute(
        "SELECT get_agent_projection_dead_item(%s) value",
        (dead["outbox1"],), role="everydayai_runtime", org=str(uuid4()),
    )[0]["value"])["outcome"] == "not_found"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _execute(
            "SELECT requeue_agent_projection_dead("
            "%s,'dead',0,8,%s,'cross tenant recovery',%s)",
            (dead["outbox1"], str(uuid4()), not_before),
            role="everydayai_runtime", org=str(uuid4()),
        )


def test_requeue_and_compat_claim_loop_has_no_lock_inversion() -> None:
    for _ in range(5):
        ids = _seed_stream()
        not_before = datetime.now(UTC)
        with ThreadPoolExecutor(max_workers=2) as pool:
            recovery = pool.submit(
                _requeue, ids["outbox1"], str(uuid4()), not_before,
            )
            claim = pool.submit(
                _execute,
                "SELECT claim_agent_compat_projection_outbox(100,60) value",
                (),
                role="everydayai_worker",
            )
            assert recovery.result()["outcome"] == "requeued"
            claim.result()


def test_checkpoint_result_and_association_anomalies_are_zero_mutation() -> None:
    checkpoint = _seed_stream()
    result = _seed_stream()
    association = _seed_stream()
    other_org = str(uuid4())
    _execute("""
        SET ROLE everydayai_owner;
        UPDATE agent_compat_projection_checkpoints
           SET through_sequence=1,last_event_id=%s,state_version=1
         WHERE session_id=%s AND projection_kind='web_runtime';
        INSERT INTO agent_compat_projection_results(
          outbox_id,event_id,session_id,projection_kind,event_sequence,
          projection_action
        ) VALUES (%s,%s,%s,'web_runtime',1,'checkpoint_only');
        INSERT INTO organizations(id,status) VALUES (%s,'active');
        UPDATE agent_projection_outbox SET org_id=%s WHERE id=%s;
        RESET ROLE;
    """, (
        checkpoint["event1"], checkpoint["session"],
        result["outbox1"], result["event1"], result["session"],
        other_org, other_org, association["outbox1"],
    ))
    not_before = datetime.now(UTC) + timedelta(seconds=1)
    before = _execute(
        "SELECT count(*) count FROM agent_projection_dead_recoveries",
    )[0]["count"]
    assert _requeue(
        checkpoint["outbox1"], str(uuid4()), not_before,
    )["outcome"] == "checkpoint_already_advanced"
    assert _requeue(
        result["outbox1"], str(uuid4()), not_before,
    )["outcome"] == "projection_result_conflict"
    association_value = _execute("""
        SELECT requeue_agent_projection_dead(
          %s,'dead',0,8,%s,'association validation',%s
        ) value
    """, (
        association["outbox1"], str(uuid4()), not_before,
    ), role="everydayai_runtime", org=other_org)[0]["value"]
    assert _decoded(association_value)["outcome"] == "wrong_stream"
    assert _execute(
        "SELECT count(*) count FROM agent_projection_dead_recoveries",
    )[0]["count"] == before
