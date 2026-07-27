"""Real PostgreSQL regressions for historical Command pending eligibility."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.test_agent_runtime_command_claim_gaps_postgres_external import (
    event_count,
)
from tests.test_agent_runtime_command_claim_postgres_external import (
    DATABASE_URL,
    MIGRATIONS,
    insert_command,
    psql,
    USER_ID,
    value,
    worker,
)


pytestmark = pytest.mark.external


@pytest.fixture(scope="module", autouse=True)
def database() -> None:
    if not DATABASE_URL or "ar13" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR13_TEST_DATABASE_URL is required")
    from tests.test_agent_runtime_command_claim_postgres_external import BOOTSTRAP

    psql(path=BOOTSTRAP)
    for migration in MIGRATIONS:
        psql(path=migration)


def historical_run(session_id: str) -> tuple[str, str]:
    command_id = insert_command(session_id)
    created = worker(
        "SELECT create_agent_run("
        f"'{session_id}','{command_id}','{command_id}','user',"
        "'{\"revision\":\"v1\"}','{\"revision\":\"v1\"}',"
        "'{\"revision\":\"v1\"}');"
    )
    return command_id, str(created["entity_id"])


def create_test_session() -> str:
    conversation_id = str(uuid4())
    return value(
        "SET ROLE everydayai_owner;"
        "INSERT INTO conversations(id,user_id,scope_type,scope_id)"
        f" VALUES ('{conversation_id}','{USER_ID}','user','{USER_ID}');"
        "INSERT INTO agent_runtime_sessions("
        "conversation_id,user_id,scope_kind,scope_id,created_by_user_id,"
        "agent_definition_id,agent_definition_revision"
        f") VALUES ('{conversation_id}','{USER_ID}','user','{USER_ID}',"
        f"'{USER_ID}','default','v1') RETURNING id;"
    )


def set_run_status(run_id: str, status: str) -> None:
    terminal = status in {"completed", "failed", "cancelled"}
    terminal_reason = f"'history_{status}'" if terminal else "NULL"
    psql(
        "SET ROLE everydayai_owner;"
        "UPDATE agent_runs SET "
        f"status='{status}',state_version=state_version+1,"
        f"completed_at={'clock_timestamp()' if terminal else 'NULL'},"
        f"terminal_reason={terminal_reason},"
        "execution_token=NULL,lease_expires_at=NULL "
        f"WHERE id='{run_id}';"
        + (
            "SELECT append_agent_runtime_event("
            f"session_id,'run.{status}',id,NULL,gen_random_uuid(),"
            "'system','history-test',"
            f"jsonb_build_object('reason','history_{status}'),"
            "ARRAY['web_runtime','audit']::text[]) "
            f"FROM agent_runs WHERE id='{run_id}';"
            if terminal else ""
        )
    )


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_history_is_closed_without_active_claim(status: str) -> None:
    session_id = create_test_session()
    command_id, run_id = historical_run(session_id)
    set_run_status(run_id, status)
    before = value(
        "SET ROLE everydayai_owner;"
        f"SELECT state_version FROM agent_runs WHERE id='{run_id}';"
    )
    before_events = event_count(run_id, f"run.{status}")

    receipt = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        f"'terminal-{status}',90,3);"
    )

    assert receipt["outcome"] == "already_processed"
    assert receipt["run_status"] == status
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT status <> 'claimed' FROM agent_command_claims "
        f"WHERE command_id='{command_id}';"
    ) == "t"
    assert value(
        "SET ROLE everydayai_owner;"
        f"SELECT status || ':' || state_version FROM agent_runs WHERE id='{run_id}';"
    ) == f"{status}:{before}"
    assert event_count(run_id, f"run.{status}") == before_events
    assert worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        f"'terminal-{status}-repeat',90,3);"
    )["outcome"] == "not_found"


def test_live_running_is_not_claimed_and_expired_running_uses_run_fencing() -> None:
    session_id = create_test_session()
    command_id, run_id = historical_run(session_id)
    first = worker(f"SELECT claim_agent_run('{run_id}','run-owner',90,3);")
    original = value(
        "SET ROLE everydayai_owner;"
        "SELECT execution_token || ':' || attempt_count "
        f"FROM agent_runs WHERE id='{run_id}';"
    )

    assert worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'command-owner-2',90,3);"
    )["outcome"] == "not_found"
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT execution_token || ':' || attempt_count "
        f"FROM agent_runs WHERE id='{run_id}';"
    ) == original
    assert value(
        "SET ROLE everydayai_owner;"
        f"SELECT count(*) FROM agent_command_claims WHERE command_id='{command_id}';"
    ) == "0"

    psql(
        "SET ROLE everydayai_owner;"
        "UPDATE agent_runs SET lease_expires_at=clock_timestamp()-interval '1s' "
        f"WHERE id='{run_id}';"
    )
    command_claim = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'command-recovery',90,3);"
    )
    recovered = worker(
        f"SELECT claim_agent_run('{run_id}','run-recovery',90,3);"
    )
    assert command_claim["outcome"] == "claimed"
    assert recovered["outcome"] == "claimed"
    assert recovered["execution_token"] != first["execution_token"]
    assert worker(
        f"SELECT renew_agent_run('{run_id}','{first['execution_token']}',90);"
    )["outcome"] == "ownership_lost"


@pytest.mark.parametrize(
    "status", ["waiting_actions", "waiting_interaction", "paused"],
)
def test_waiting_and_paused_runs_are_not_reexecuted(status: str) -> None:
    session_id = create_test_session()
    command_id, run_id = historical_run(session_id)
    set_run_status(run_id, status)

    receipt = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        f"'waiting-{status}',90,3);"
    )

    assert receipt["outcome"] == "already_processed"
    assert receipt["run_status"] == status
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT status <> 'claimed' FROM agent_command_claims "
        f"WHERE command_id='{command_id}';"
    ) == "t"


def test_invalid_result_links_fail_closed_without_replacement_run() -> None:
    session_a = create_test_session()
    session_b = create_test_session()
    command_a = insert_command(session_a)
    command_b, run_b = historical_run(session_b)
    psql(
        "SET ROLE everydayai_owner;"
        "UPDATE agent_session_commands "
        f"SET result_entity_id='{run_b}' WHERE id='{command_a}';"
    )

    receipt = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'invalid-link',90,3);"
    )

    assert receipt["outcome"] == "association_rejected"
    assert receipt["command_id"] == command_a
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT status || ':' || error_class FROM agent_command_claims "
        f"WHERE command_id='{command_a}';"
    ) == "failed:association_rejected"
    assert value(
        "SET ROLE everydayai_owner;"
        f"SELECT count(*) FROM agent_runs WHERE command_id='{command_a}';"
    ) == "0"
    assert value(
        "SET ROLE everydayai_owner;"
        f"SELECT command_id FROM agent_runs WHERE id='{run_b}';"
    ) == command_b
    linked = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'linked-command',90,3);"
    )
    worker(
        "SELECT finish_agent_command_claim("
        f"'{command_b}','{linked['fencing_token']}','completed',NULL);"
    )

    missing_command = insert_command(session_a)
    psql(
        "ALTER TABLE agent_session_commands DISABLE TRIGGER ALL;"
        f"UPDATE agent_session_commands SET result_entity_id='{uuid4()}' "
        f"WHERE id='{missing_command}';"
        "ALTER TABLE agent_session_commands ENABLE TRIGGER ALL;"
    )
    assert worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'missing-link',90,3);"
    )["outcome"] == "association_rejected"


def test_linked_run_scope_mismatch_fails_closed() -> None:
    session_id = create_test_session()
    command_id, run_id = historical_run(session_id)
    psql(
        "SET ROLE everydayai_owner;"
        "UPDATE agent_runs "
        "SET user_id='44444444-4444-4444-4444-444444444444' "
        f"WHERE id='{run_id}';"
    )

    receipt = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'scope-link',90,3);"
    )

    assert receipt["outcome"] == "association_rejected"
    assert receipt["command_id"] == command_id
    assert value(
        "SET ROLE everydayai_owner;"
        f"SELECT count(*) FROM agent_runs WHERE command_id='{command_id}';"
    ) == "1"


def test_queued_history_and_new_command_remain_unique_under_concurrency() -> None:
    session_id = create_test_session()
    historical_command, run_id = historical_run(session_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(
            lambda worker_id: worker(
                "SELECT claim_pending_agent_command_and_ensure_run("
                f"'{worker_id}',90,3);"
            ),
            ("queued-worker-a", "queued-worker-b"),
        ))
    active = [item for item in receipts if item["outcome"] == "claimed"]
    assert len(active) == 1
    assert active[0]["command_id"] == historical_command
    assert active[0]["run_id"] == run_id
    assert value(
        "SET ROLE everydayai_owner;"
        f"SELECT count(*) FROM agent_runs WHERE command_id='{historical_command}';"
    ) == "1"
    assert event_count(run_id, "run.created") == 1

    worker(
        "SELECT finish_agent_command_claim("
        f"'{historical_command}','{active[0]['fencing_token']}',"
        "'completed',NULL);"
    )
    fresh_command = insert_command(session_id)
    fresh = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'fresh-command',90,3);"
    )
    assert fresh["command_id"] == fresh_command
    assert value(
        "SET ROLE everydayai_owner;"
        f"SELECT count(*) FROM agent_runs WHERE command_id='{fresh_command}';"
    ) == "1"


def test_eligibility_helpers_are_owner_only() -> None:
    assert value("""
        SELECT
            has_function_privilege(
                'everydayai_worker',
                'claim_pending_agent_command_and_ensure_run(text,integer,integer)',
                'EXECUTE')
            AND NOT has_function_privilege(
                'everydayai_worker',
                '_agent_command_run_eligibility(agent_session_commands)',
                'EXECUTE')
            AND NOT has_function_privilege(
                'everydayai_runtime',
                '_agent_command_run_eligibility(agent_session_commands)',
                'EXECUTE')
            AND NOT has_function_privilege(
                'everydayai_wecom_runtime',
                '_close_nonexecuting_agent_command(agent_session_commands,'
                'agent_runtime_sessions,text,text)',
                'EXECUTE')
            AND NOT has_function_privilege(
                'public',
                '_claim_eligible_agent_command(agent_session_commands,'
                'agent_runtime_sessions,agent_command_claims,text,integer,integer)',
                'EXECUTE');
    """) == "t"
