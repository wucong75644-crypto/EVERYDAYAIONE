"""Real PostgreSQL regressions for the three AR-13 coordinator gaps."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from tests.test_agent_runtime_command_claim_postgres_external import (
    MIGRATIONS,
    create_session,
    insert_command,
    psql,
    value,
    worker,
)


pytestmark = pytest.mark.external
DATABASE_URL = os.getenv("AR13_TEST_DATABASE_URL", "")
ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "tests/fixtures/agent_runtime_core_postgres_bootstrap.sql"


@pytest.fixture(scope="module", autouse=True)
def database() -> None:
    if not DATABASE_URL or "ar13" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR13_TEST_DATABASE_URL is required")
    psql(path=BOOTSTRAP)
    for migration in MIGRATIONS:
        psql(path=migration)


@pytest.fixture(scope="module")
def session_id() -> str:
    return create_session()


def event_count(run_id: str, event_type: str) -> int:
    return int(value(
        "SET ROLE everydayai_owner;"
        "SELECT count(*) FROM agent_runtime_events "
        f"WHERE run_id='{run_id}' AND event_type='{event_type}';"
    ))


def test_normal_and_cancel_before_start_emit_replayable_events(
    session_id: str,
) -> None:
    command_id = insert_command(session_id)
    claimed = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'gap-events',90,3);"
    )
    run_id = str(claimed["run_id"])
    assert claimed["command_id"] == command_id
    assert event_count(run_id, "run.created") == 1

    readback = worker(
        f"SELECT get_agent_command_run_claim('{command_id}','gap-events');"
    )
    assert readback["run_id"] == run_id
    assert event_count(run_id, "run.created") == 1

    target_run_id = str(uuid4())
    cancel_id = insert_command(
        session_id, command_type="cancel", target_run_id=target_run_id,
    )
    cancelled = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'gap-cancel',90,3);"
    )
    assert cancelled["command_id"] == cancel_id
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT string_agg(event_type,',' ORDER BY sequence) "
        "FROM agent_runtime_events "
        f"WHERE run_id='{target_run_id}';"
    ) == "run.created,run.cancelled"
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT count(*) FROM agent_projection_outbox outbox "
        "JOIN agent_runtime_events event ON event.id=outbox.event_id "
        f"WHERE event.run_id='{target_run_id}';"
    ) == "4"
    replay = worker(
        f"SELECT replay_agent_runtime_events('{session_id}',0,500);"
    )
    replay_types = [
        item["event_type"] for item in replay["events"]
        if item["run_id"] == target_run_id
    ]
    assert replay_types == ["run.created", "run.cancelled"]
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT count(*)=max(sequence) AND min(sequence)=1 "
        f"FROM agent_runtime_events WHERE session_id='{session_id}';"
    ) == "t"


def test_attempt_exhaustion_fails_run_once_and_blocks_run_claim(
    session_id: str,
) -> None:
    command_id = insert_command(session_id)
    first = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'gap-exhausted-1',90,1);"
    )
    run_id = str(first["run_id"])
    run_claim = worker(
        f"SELECT claim_agent_run('{run_id}','active-run-worker',90,3);"
    )
    assert run_claim["outcome"] == "claimed"
    psql(
        "SET ROLE everydayai_owner;"
        "UPDATE agent_command_claims SET lease_expires_at="
        "clock_timestamp()-interval '1 second' "
        f"WHERE command_id='{command_id}';"
        "UPDATE agent_runs SET lease_expires_at="
        "clock_timestamp()-interval '1 second' "
        f"WHERE id='{run_id}';"
    )

    exhausted = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'gap-exhausted-2',90,1);"
    )

    assert exhausted["outcome"] == "attempts_exhausted"
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT concat_ws(':',claim.status,run.status,run.terminal_reason) "
        "FROM agent_command_claims claim JOIN agent_runs run ON run.id=claim.run_id "
        f"WHERE claim.command_id='{command_id}';"
    ) == "attempts_exhausted:failed:command_attempts_exhausted"
    assert worker(
        f"SELECT claim_agent_run('{run_id}','late-run-worker',90,3);"
    )["outcome"] == "invalid_transition"
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT count(*)=1 AND bool_and(ended_at IS NOT NULL AND outcome='failed') "
        f"FROM agent_run_attempts WHERE run_id='{run_id}';"
    ) == "t"
    assert event_count(run_id, "run.failed") == 1
    assert event_count(run_id, "command.attempts_exhausted") == 1
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT count(*) FROM agent_projection_outbox outbox "
        "JOIN agent_runtime_events event ON event.id=outbox.event_id "
        f"WHERE event.run_id='{run_id}' "
        "AND event.event_type IN ('run.failed','command.attempts_exhausted');"
    ) == "4"


def test_run_event_failure_rolls_back_run_and_command_claim(
    session_id: str,
) -> None:
    command_id = insert_command(session_id)
    psql("""
        SET ROLE everydayai_owner;
        CREATE FUNCTION reject_ar13_outbox() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'FORCED_AR13_OUTBOX_FAILURE'; END
        $$;
        CREATE TRIGGER reject_ar13_outbox
        BEFORE INSERT ON agent_projection_outbox
        FOR EACH ROW EXECUTE FUNCTION reject_ar13_outbox();
    """)

    failed = psql(
        "SET SESSION AUTHORIZATION everydayai_worker;"
        "SELECT set_config('app.access_kind','worker',false);"
        "SELECT set_config('app.request_id','gap-atomicity',false);"
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'gap-atomicity',90,3);",
        check=False,
    )

    assert failed.returncode != 0
    assert "FORCED_AR13_OUTBOX_FAILURE" in failed.stderr
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT (SELECT count(*) FROM agent_runs "
        f"WHERE command_id='{command_id}')=0 "
        "AND (SELECT count(*) FROM agent_command_claims "
        f"WHERE command_id='{command_id}')=0;"
    ) == "t"
    psql(
        "SET ROLE everydayai_owner;"
        "DROP TRIGGER reject_ar13_outbox ON agent_projection_outbox;"
        "DROP FUNCTION reject_ar13_outbox();"
        f"DELETE FROM agent_session_commands WHERE id='{command_id}';"
    )


def test_request_hash_matches_create_agent_run_both_directions(
    session_id: str,
) -> None:
    old_command_id = insert_command(session_id)
    old_run = worker(
        "SELECT create_agent_run("
        f"'{session_id}','{old_command_id}','{old_command_id}','user',"
        "'{\"revision\":\"v1\"}','{\"revision\":\"v1\"}',"
        "'{\"revision\":\"v1\"}');"
    )
    claimed_old = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'gap-hash-old',90,3);"
    )
    assert claimed_old["run_id"] == old_run["entity_id"]
    assert event_count(str(old_run["entity_id"]), "run.created") == 1

    new_command_id = insert_command(session_id)
    claimed_new = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'gap-hash-new',90,3);"
    )
    existing = worker(
        "SELECT create_agent_run("
        f"'{session_id}','{new_command_id}','{new_command_id}','user',"
        "'{\"revision\":\"v1\"}','{\"revision\":\"v1\"}',"
        "'{\"revision\":\"v1\"}');"
    )
    changed = worker(
        "SELECT create_agent_run("
        f"'{session_id}','{new_command_id}','{new_command_id}','user',"
        "'{\"revision\":\"v1\"}','{\"revision\":\"v2\"}',"
        "'{\"revision\":\"v1\"}');"
    )
    assert existing["outcome"] == "already_exists"
    assert existing["entity_id"] == claimed_new["run_id"]
    assert changed["outcome"] == "idempotency_conflict"
    assert event_count(str(claimed_new["run_id"]), "run.created") == 1

    hash_contract = value(
        "SET ROLE everydayai_owner;"
        "SELECT bool_and(run.request_hash = md5(jsonb_build_object("
        "'command_id',run.command_id,'run_kind',run.run_kind,"
        "'context_receipt',run.context_receipt,"
        "'config_snapshot',run.config_snapshot,"
        "'capability_snapshot',run.capability_snapshot)::text)) "
        "FROM agent_runs run "
        f"WHERE command_id IN ('{old_command_id}','{new_command_id}');"
    )
    assert hash_contract == "t"
    assert value("""
        SELECT
            NOT has_function_privilege(
                'everydayai_worker',
                '_agent_run_request_hash(uuid,text,jsonb,jsonb,jsonb)',
                'EXECUTE')
            AND NOT has_function_privilege(
                'everydayai_runtime',
                '_agent_run_request_hash(uuid,text,jsonb,jsonb,jsonb)',
                'EXECUTE')
            AND NOT has_function_privilege(
                'everydayai_wecom_runtime',
                '_agent_run_request_hash(uuid,text,jsonb,jsonb,jsonb)',
                'EXECUTE')
            AND NOT has_function_privilege(
                'public',
                '_agent_run_request_hash(uuid,text,jsonb,jsonb,jsonb)',
                'EXECUTE');
    """) == "t"
