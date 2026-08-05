from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.agent_runtime_migration_test_support import migration_paths_through
from tests.test_agent_runtime_ar173_postgres_external import (
    _seed_specialist_action,
    _worker_rpc,
    database,
)
from tests.test_agent_runtime_tenant_kill_control_postgres_external import _admin_call


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ORG = "22222222-2222-2222-2222-222222222222"
TOKEN = "88888888-8888-8888-8888-888888888888"


def _apply(url: str, name: str) -> None:
    path = ROOT / "migrations" / name
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute(path.read_text())


def _apply_all(url: str) -> None:
    for path in migration_paths_through(
        ROOT, "227_10_agent_runtime_operations_center.sql"
    ):
        _apply(url, path.name)


def test_operations_center_readback_intent_claim_and_rollback(database: str) -> None:
    _apply_all(database)
    ids = _seed_specialist_action(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO agent_runtime_owner_fences "
            "(owner_kind,owner_id,org_id,execution_token,tenant_kill_epoch,status) "
            "VALUES('attempt',%s,%s,%s,0,'active')",
            (ids["attempt"], ORG, ids["token"]),
        )
        conn.commit()
    created = _worker_rpc(database, "create_agent_runtime_provider_submission", (
        ids["attempt"], ids["action"], ids["run"], ORG,
        "44444444-4444-4444-4444-444444444444", "user",
        "44444444-4444-4444-4444-444444444444", ids["token"],
        ids["request_hash"], "mock-provider", "mock-v1", "ops-key",
    ))
    submitted = _worker_rpc(database, "record_agent_runtime_provider_submitted", (
        created["submission_id"], ids["token"], ids["request_hash"], 0,
        "provider-task-1", "/status", None,
    ))
    unknown = _worker_rpc(database, "record_agent_runtime_provider_unknown", (
        created["submission_id"], ids["token"], ids["request_hash"],
        submitted["state_version"], {"error_code": "response_loss"},
    ))
    assert unknown["state"] == "unknown"

    status = _admin_call(database, "list_agent_runtime_provider_operations", (
        ORG, None, None, "unknown", None, None, 100,
    ))
    assert status["outcome"] == "readback"
    assert status["items"][0]["state"] == "unknown"
    assert "execution_token" not in repr(status)
    assert "provider_request" not in repr(status)
    with pytest.raises(psycopg.Error, match="RUNTIME_ADMIN_REQUIRED"):
        _admin_call(database, "list_agent_runtime_provider_operations", (
            "33333333-3333-3333-3333-333333333333", None, None, "unknown", None, None, 100,
        ))
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT has_table_privilege('everydayai_agent_runtime_worker',"
            "'agent_runtime_provider_operation_intents','SELECT')"
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_worker',"
            "'request_agent_runtime_provider_operation(uuid,uuid,uuid,text,bigint,text,text)','EXECUTE')"
        ).fetchone()[0] is False

    request_id = str(uuid4())
    request = _admin_call(database, "request_agent_runtime_provider_operation", (
        request_id, ORG, created["submission_id"], "reconcile",
        unknown["state_version"], "verify provider response", "ops-request-1",
    ))
    assert request["outcome"] == "applied"
    assert _admin_call(database, "request_agent_runtime_provider_operation", (
        request_id, ORG, created["submission_id"], "reconcile",
        unknown["state_version"], "verify provider response", "ops-request-1",
    ))["outcome"] == "already_applied"
    assert _admin_call(database, "request_agent_runtime_provider_operation", (
        str(uuid4()), ORG, created["submission_id"], "cancel",
        unknown["state_version"], "different intent", "ops-request-1",
    ))["outcome"] == "idempotency_conflict"
    assert _admin_call(database, "request_agent_runtime_provider_operation", (
        str(uuid4()), ORG, created["submission_id"], "reconcile",
        0, "stale", "ops-request-2",
    ))["outcome"] == "stale_version"

    claimed = _worker_rpc(database, "claim_agent_runtime_provider_operation", (
        request["intent_id"], "ops-worker", 120,
    ))
    assert claimed["outcome"] == "applied"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        with pytest.raises(psycopg.Error, match="RUNTIME_PROVIDER_OPERATION_IMMUTABLE"):
            conn.execute(
                "UPDATE agent_runtime_provider_operation_intents SET reason='tampered' WHERE id=%s",
                (request["intent_id"],),
            )

    rollback = ROOT / "migrations/rollback/227_10_agent_runtime_operations_center_rollback.sql"
    with pytest.raises(psycopg.Error, match="AR_17_4_ROLLBACK_BLOCKED_OPERATION_INTENTS"):
        _apply(database, "../migrations/rollback/227_10_agent_runtime_operations_center_rollback.sql")
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("TRUNCATE agent_runtime_provider_operation_intents")
        conn.commit()
    with psycopg.connect(database) as conn:
        with conn.transaction():
            conn.execute(rollback.read_text())
    _apply(database, "227_10_agent_runtime_operations_center.sql")
    with psycopg.connect(database) as conn:
        with conn.transaction():
            conn.execute(rollback.read_text())
