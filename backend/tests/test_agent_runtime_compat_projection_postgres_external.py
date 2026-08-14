"""Real PostgreSQL AR-15 ordering, replay, ModelResult, and rollback contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.test_agent_runtime_model_attempt_postgres_external import (
    CREDITS_BOOTSTRAP,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("AR15_TEST_DATABASE_URL", "")
MIGRATIONS = [
    *(ROOT / "migrations" / name for name in (
        "212_agent_runtime_core_foundation.sql",
        "213_agent_runtime_session_run_rpcs.sql",
        "214_agent_runtime_run_lifecycle_rpcs.sql",
        "215_agent_runtime_model_event_projection_rpcs.sql",
    )),
    *sorted((ROOT / "migrations").glob("217_0*.sql")),
    *sorted((ROOT / "migrations").glob("218_0*.sql")),
    *sorted((ROOT / "migrations").glob("220_0*.sql")),
    *sorted((ROOT / "migrations").glob("220_1*.sql")),
]


def _psql(sql: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", DATABASE_URL, "-c", sql],
        check=check, capture_output=True, text=True,
    )


def _file(path: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-1",
         "-d", DATABASE_URL, "-f", str(path)],
        check=check, capture_output=True, text=True,
    )


def _worker(sql: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _psql(
        "SET SESSION AUTHORIZATION everydayai_worker;"
        "SELECT set_config('app.access_kind','worker',false);"
        "SELECT set_config('app.request_id','ar15-test',false);" + sql,
        check=check,
    )


@pytest.fixture(scope="module", autouse=True)
def database() -> None:
    if os.getenv("RUN_AR15_DB_TEST") != "1" or not DATABASE_URL:
        pytest.skip("RUN_AR15_DB_TEST=1 and AR15_TEST_DATABASE_URL required")
    if "ar15" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR15 database name required")
    _file(ROOT / "tests/fixtures/agent_runtime_core_postgres_bootstrap.sql")
    _file(ROOT / "tests/fixtures/agent_runtime_compat_projection_legacy.sql")
    _psql(CREDITS_BOOTSTRAP)
    for migration in MIGRATIONS:
        _file(migration)


def _claim() -> list[dict[str, object]]:
    result = _worker("SELECT claim_agent_compat_projection_outbox(20,60);")
    return json.loads(result.stdout.strip().splitlines()[-2])


def _apply(row: dict[str, object], action: str, *, check: bool = True):
    return _worker(
        "SELECT apply_agent_compat_projection("
        f"'{row['id']}','{row['lease_token']}','{action}');",
        check=check,
    )


def test_order_replay_model_result_delivery_and_permissions() -> None:
    ids = {
        "session": "81000000-0000-0000-0000-000000000001",
        "command": "81000000-0000-0000-0000-000000000002",
        "run": "81000000-0000-0000-0000-000000000003",
        "step": "81000000-0000-0000-0000-000000000004",
        "result": "81000000-0000-0000-0000-000000000005",
    }
    _psql(f"""
        SET ROLE everydayai_owner;
        INSERT INTO agent_runtime_sessions(
            id,conversation_id,user_id,scope_kind,scope_id,created_by_user_id,
            agent_definition_id,agent_definition_revision,next_event_sequence
        ) VALUES ('{ids["session"]}',
          '33333333-3333-3333-3333-333333333333',
          '11111111-1111-1111-1111-111111111111','user',
          '11111111-1111-1111-1111-111111111111',
          '11111111-1111-1111-1111-111111111111','default','v1',4);
        INSERT INTO agent_session_commands(
            id,session_id,user_id,command_type,idempotency_key,payload,request_hash
        ) VALUES ('{ids["command"]}','{ids["session"]}',
          '11111111-1111-1111-1111-111111111111','submit_input','command',
          '{{"text":"hello","delivery_context":{{"channel":"wecom"}}}}','{'1' * 32}');
        INSERT INTO agent_runs(
            id,session_id,command_id,user_id,run_kind,status,idempotency_key,
            request_hash,result_hash,completed_at
        ) VALUES ('{ids["run"]}','{ids["session"]}','{ids["command"]}',
          '11111111-1111-1111-1111-111111111111','user','completed','run',
          '{'2' * 32}',encode(digest(convert_to('answer','UTF8'),'sha256'),'hex'),
          clock_timestamp());
        INSERT INTO agent_model_steps(
            id,run_id,session_id,user_id,step_number,status,model_id,provider,
            model_revision,prompt_revision,tool_catalog_revision,stop_reason,
            completed_at
        ) VALUES ('{ids["step"]}','{ids["run"]}','{ids["session"]}',
          '11111111-1111-1111-1111-111111111111',1,'completed','model',
          'provider','v1','v1','v1','final',clock_timestamp());
        INSERT INTO agent_model_results(
            id,model_step_id,run_id,session_id,user_id,output_kind,text_content,
            content_hash
        ) VALUES ('{ids["result"]}','{ids["step"]}','{ids["run"]}',
          '{ids["session"]}','11111111-1111-1111-1111-111111111111',
          'text','answer',encode(digest(convert_to('answer','UTF8'),'sha256'),'hex'));
        INSERT INTO agent_runtime_events(
            id,session_id,sequence,user_id,scope_kind,scope_id,event_type,
            run_id,correlation_id,actor_type,payload,payload_hash
        ) VALUES
          (gen_random_uuid(),'{ids["session"]}',1,
           '11111111-1111-1111-1111-111111111111','user',
           '11111111-1111-1111-1111-111111111111','command.accepted',NULL,
           '{ids["command"]}','user','{{}}','hash'),
          (gen_random_uuid(),'{ids["session"]}',2,
           '11111111-1111-1111-1111-111111111111','user',
           '11111111-1111-1111-1111-111111111111','run.created','{ids["run"]}',
           '{ids["command"]}','system','{{}}','hash'),
          (gen_random_uuid(),'{ids["session"]}',3,
           '11111111-1111-1111-1111-111111111111','user',
           '11111111-1111-1111-1111-111111111111','run.completed','{ids["run"]}',
           '{ids["command"]}','system','{{}}','hash');
        INSERT INTO agent_projection_outbox(event_id,session_id,user_id,projection_kind)
        SELECT id,session_id,user_id,'web_runtime' FROM agent_runtime_events
         WHERE session_id='{ids["session"]}';
    """)
    actions = ["user_message", "run_pending", "run_completed"]
    rows = []
    for action in actions:
        [row] = _claim()
        rows.append(row)
        _apply(row, action)
    replay = _apply(rows[-1], "run_completed")
    assert "already_applied" in replay.stdout
    counts = _psql(
        "SELECT (SELECT count(*) FROM messages),"
        "(SELECT count(*) FROM tasks),"
        "(SELECT count(*) FROM conversation_deliveries),"
        "(SELECT through_sequence FROM agent_compat_projection_checkpoints);"
    ).stdout
    assert "2 |" in counts and "1 |" in counts and "|                3" in counts
    assert "t" in _psql(
        "SELECT has_function_privilege('everydayai_worker',"
        "'apply_agent_compat_projection(uuid,uuid,text)','EXECUTE');"
    ).stdout
    assert "f" in _psql(
        "SELECT has_table_privilege('everydayai_worker',"
        "'agent_compat_projection_results','SELECT');"
    ).stdout


def test_hash_conflict_rolls_back_then_retries_without_duplicates() -> None:
    session_id = "81000000-0000-0000-0000-000000000001"
    run_id = "81000000-0000-0000-0000-000000000003"
    result_id = "81000000-0000-0000-0000-000000000005"
    _psql(f"""
        SET ROLE everydayai_owner;
        UPDATE agent_model_results SET content_hash = repeat('b',64)
         WHERE id = '{result_id}';
        UPDATE agent_runtime_sessions SET next_event_sequence = 5
         WHERE id = '{session_id}';
        WITH event AS (
          INSERT INTO agent_runtime_events(
            id,session_id,sequence,user_id,scope_kind,scope_id,event_type,
            run_id,correlation_id,actor_type,payload,payload_hash
          ) VALUES (
            gen_random_uuid(),'{session_id}',4,
            '11111111-1111-1111-1111-111111111111','user',
            '11111111-1111-1111-1111-111111111111','run.completed','{run_id}',
            gen_random_uuid(),'system','{{}}','hash'
          ) RETURNING *
        ) INSERT INTO agent_projection_outbox(
            event_id,session_id,user_id,projection_kind
        ) SELECT id,session_id,user_id,'web_runtime' FROM event;
    """)
    [row] = _claim()
    failed = _apply(row, "run_completed", check=False)
    assert failed.returncode != 0
    assert "AGENT_COMPAT_MODEL_RESULT_INVALID" in failed.stderr
    assert "0" in _psql(
        f"SELECT count(*) FROM agent_compat_projection_results "
        f"WHERE event_sequence=4 AND session_id='{session_id}';"
    ).stdout
    _worker(
        "SELECT fail_agent_projection_outbox("
        f"'{row['id']}','{row['lease_token']}','hash_conflict');"
    )
    _psql(f"""
        SET ROLE everydayai_owner;
        UPDATE agent_model_results
           SET content_hash=encode(digest(convert_to('answer','UTF8'),'sha256'),'hex')
         WHERE id='{result_id}';
        UPDATE agent_projection_outbox SET next_attempt_at=clock_timestamp()
         WHERE id='{row["id"]}';
    """)
    [retried] = _claim()
    _apply(retried, "run_completed")
    counts = _psql(
        "SELECT (SELECT count(*) FROM messages),"
        "(SELECT count(*) FROM tasks),"
        "(SELECT count(*) FROM conversation_deliveries);"
    ).stdout
    assert "2 |" in counts and counts.count("1") >= 2


def test_missing_and_wrong_model_result_stay_retryable() -> None:
    session_id = "81000000-0000-0000-0000-000000000001"
    command_id = "82000000-0000-0000-0000-000000000002"
    run_id = "82000000-0000-0000-0000-000000000003"
    step_id = "82000000-0000-0000-0000-000000000004"
    result_id = "82000000-0000-0000-0000-000000000005"
    _psql(f"""
        SET ROLE everydayai_owner;
        UPDATE agent_runtime_sessions SET next_event_sequence=7
         WHERE id='{session_id}';
        INSERT INTO agent_session_commands(
          id,session_id,user_id,command_type,idempotency_key,payload,request_hash
        ) VALUES ('{command_id}','{session_id}',
          '11111111-1111-1111-1111-111111111111','submit_input','second',
          '{{}}','{'3' * 32}');
        INSERT INTO agent_runs(
          id,session_id,command_id,user_id,run_kind,status,idempotency_key,
          request_hash,result_hash,completed_at
        ) VALUES ('{run_id}','{session_id}','{command_id}',
          '11111111-1111-1111-1111-111111111111','user','completed','second',
          '{'4' * 32}',encode(digest(convert_to('second','UTF8'),'sha256'),'hex'),
          clock_timestamp());
        INSERT INTO agent_model_steps(
          id,run_id,session_id,user_id,step_number,status,model_id,provider,
          model_revision,prompt_revision,tool_catalog_revision,stop_reason,
          completed_at
        ) VALUES ('{step_id}','{run_id}','{session_id}',
          '11111111-1111-1111-1111-111111111111',1,'completed','model',
          'provider','v1','v1','v1','final',clock_timestamp());
        WITH events AS (
          INSERT INTO agent_runtime_events(
            id,session_id,sequence,user_id,scope_kind,scope_id,event_type,
            run_id,correlation_id,actor_type,payload,payload_hash
          ) VALUES
          (gen_random_uuid(),'{session_id}',5,
           '11111111-1111-1111-1111-111111111111','user',
           '11111111-1111-1111-1111-111111111111','run.created','{run_id}',
           '{command_id}','system','{{}}','hash'),
          (gen_random_uuid(),'{session_id}',6,
           '11111111-1111-1111-1111-111111111111','user',
           '11111111-1111-1111-1111-111111111111','run.completed','{run_id}',
           '{command_id}','system','{{}}','hash')
          RETURNING *
        ) INSERT INTO agent_projection_outbox(
          event_id,session_id,user_id,projection_kind
        ) SELECT id,session_id,user_id,'web_runtime' FROM events;
    """)
    [created] = _claim()
    _apply(created, "run_pending")
    [missing] = _claim()
    first = _apply(missing, "run_completed", check=False)
    assert "AGENT_COMPAT_MODEL_RESULT_INVALID" in first.stderr
    _worker(
        "SELECT fail_agent_projection_outbox("
        f"'{missing['id']}','{missing['lease_token']}','missing_result');"
    )
    _psql(f"""
        SET ROLE everydayai_owner;
        INSERT INTO agent_model_results(
          id,model_step_id,run_id,session_id,user_id,output_kind,text_content,
          content_hash
        ) VALUES ('{result_id}','{step_id}',
          '81000000-0000-0000-0000-000000000003','{session_id}',
          '11111111-1111-1111-1111-111111111111','text','second',
          encode(digest(convert_to('second','UTF8'),'sha256'),'hex'));
        UPDATE agent_projection_outbox SET next_attempt_at=clock_timestamp()
         WHERE id='{missing["id"]}';
    """)
    [wrong] = _claim()
    second = _apply(wrong, "run_completed", check=False)
    assert "AGENT_COMPAT_MODEL_RESULT_INVALID" in second.stderr
    _worker(
        "SELECT fail_agent_projection_outbox("
        f"'{wrong['id']}','{wrong['lease_token']}','wrong_association');"
    )
    _psql(f"""
        SET ROLE everydayai_owner;
        UPDATE agent_model_results SET run_id='{run_id}' WHERE id='{result_id}';
        UPDATE agent_projection_outbox SET next_attempt_at=clock_timestamp()
         WHERE id='{wrong["id"]}';
    """)
    [valid] = _claim()
    _apply(valid, "run_completed")


def test_same_session_serializes_while_sessions_claim_in_parallel() -> None:
    session_ids = [
        "83000000-0000-0000-0000-000000000001",
        "84000000-0000-0000-0000-000000000001",
    ]
    _psql(f"""
        SET ROLE everydayai_owner;
        INSERT INTO agent_runtime_sessions(
          id,conversation_id,org_id,user_id,scope_kind,scope_id,created_by_user_id,
          agent_definition_id,agent_definition_revision,next_event_sequence
        ) VALUES
        ('{session_ids[0]}','55555555-5555-5555-5555-555555555555',
         '22222222-2222-2222-2222-222222222222',
         '44444444-4444-4444-4444-444444444444','user',
         '44444444-4444-4444-4444-444444444444',
         '44444444-4444-4444-4444-444444444444','default','v1',3),
        ('{session_ids[1]}','66666666-6666-6666-6666-666666666666',
         '22222222-2222-2222-2222-222222222222',NULL,
         'channel','wecom:group:test',
         '44444444-4444-4444-4444-444444444444','default','v1',2);
        WITH events AS (
          INSERT INTO agent_runtime_events(
            id,session_id,sequence,org_id,user_id,scope_kind,scope_id,event_type,
            correlation_id,actor_type,payload,payload_hash
          ) VALUES
          (gen_random_uuid(),'{session_ids[0]}',1,
           '22222222-2222-2222-2222-222222222222',
           '44444444-4444-4444-4444-444444444444','user',
           '44444444-4444-4444-4444-444444444444','session.created',
           gen_random_uuid(),'system','{{}}','hash'),
          (gen_random_uuid(),'{session_ids[0]}',2,
           '22222222-2222-2222-2222-222222222222',
           '44444444-4444-4444-4444-444444444444','user',
           '44444444-4444-4444-4444-444444444444','model_step.created',
           gen_random_uuid(),'system','{{}}','hash'),
          (gen_random_uuid(),'{session_ids[1]}',1,
           '22222222-2222-2222-2222-222222222222',NULL,'channel',
           'wecom:group:test','session.created',gen_random_uuid(),'system',
           '{{}}','hash')
          RETURNING *
        ) INSERT INTO agent_projection_outbox(
          event_id,session_id,org_id,user_id,projection_kind
        ) SELECT id,session_id,org_id,user_id,'web_runtime' FROM events;
    """)
    claimed = _claim()
    assert len(claimed) == 2
    assert {row["session_id"] for row in claimed} == set(session_ids)
    for row in claimed:
        _apply(row, "checkpoint_only")
    [second] = _claim()
    assert second["session_id"] == session_ids[0]
    _apply(second, "checkpoint_only")


def test_z_rollback_guard_clean_rollback_and_reapply() -> None:
    rollback_12 = (
        ROOT / "migrations/rollback/"
        "220_12_agent_runtime_compat_projection_rpcs_rollback.sql"
    )
    rollback_11 = (
        ROOT / "migrations/rollback/"
        "220_11_agent_runtime_compat_projection_foundation_rollback.sql"
    )
    migration_11 = (
        ROOT / "migrations/220_11_agent_runtime_compat_projection_foundation.sql"
    )
    migration_12 = (
        ROOT / "migrations/220_12_agent_runtime_compat_projection_rpcs.sql"
    )

    guarded = _file(rollback_12, check=False)
    assert guarded.returncode != 0
    assert "AGENT_COMPAT_PROJECTION_ROLLBACK_HAS_FACTS" in guarded.stderr
    _psql("""
        SET ROLE everydayai_owner;
        DELETE FROM agent_compat_projection_results;
        UPDATE agent_compat_projection_checkpoints
           SET through_sequence=0,last_event_id=NULL;
    """)
    _file(rollback_12)
    _file(rollback_11)
    _file(migration_11)
    _file(migration_12)
    assert "3" in _psql("""
        SELECT count(*) FROM pg_proc WHERE proname IN (
          'claim_agent_compat_projection_outbox',
          'apply_agent_compat_projection',
          'get_agent_compat_projection_result'
        );
    """).stdout
