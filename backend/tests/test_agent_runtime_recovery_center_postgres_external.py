from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar173_postgres_external import (
    _seed_specialist_action,
    _worker_rpc,
    database,
)
from tests.test_agent_runtime_tenant_kill_control_postgres_external import _admin_call


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ORG = "22222222-2222-2222-2222-222222222222"
USER = "44444444-4444-4444-4444-444444444444"


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))


def _apply_all(url: str) -> None:
    for index in range(1, 11):
        _apply(url, next((ROOT / "migrations").glob(f"227_{index:02d}_*.sql")))
    _apply(url, ROOT / "migrations/227_11_agent_runtime_recovery_center.sql")


def test_recovery_snapshot_intent_claim_and_rollback(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, "
            "relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)"
        )
        conn.execute(
            "CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, "
            "status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())"
        )
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")))
    ids = _seed_specialist_action(database)
    _apply_all(database)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO agent_runtime_owner_fences "
            "(owner_kind,owner_id,org_id,execution_token,tenant_kill_epoch,status) "
            "VALUES('attempt',%s,%s,%s,0,'active')",
            (ids["attempt"], ORG, ids["token"]),
        )
        conn.commit()
    assert _worker_rpc(database, "link_agent_action_artifact", (
        ids["action"], ids["attempt"], ids["artifact"], "output", None,
        "e" * 64, 1, "materialize_failed", "normal",
    ))["outcome"] == "linked"

    status = _admin_call(database, "list_agent_runtime_recovery_snapshot", (
        ORG, "artifact", "materialize_failed", 100,
    ))
    assert status["outcome"] == "readback"
    assert status["items"][0]["recovery_domain"] == "artifact"
    rendered = repr(status)
    assert "execution_token" not in rendered
    assert "relative_path" not in rendered
    assert "oss_object_key" not in rendered
    full_status = _admin_call(database, "list_agent_runtime_recovery_snapshot", (
        ORG, None, None, 100,
    ))
    assert full_status["outcome"] == "readback"
    assert full_status["items"]
    for domain in ("workspace", "scheduler", "child_run", "sandbox"):
        domain_status = _admin_call(database, "list_agent_runtime_recovery_snapshot", (
            ORG, domain, None, 100,
        ))
        assert domain_status == {
            "outcome": "readback", "org_id": ORG, "items": [], "count": 0,
        }

    request_id = str(uuid4())
    intent = _admin_call(database, "request_agent_runtime_recovery", (
        request_id, ORG, "artifact", ids["artifact"], "recover", 0,
        "verify artifact readback", "recovery-key-1",
    ))
    assert intent["outcome"] == "applied"
    assert _admin_call(database, "request_agent_runtime_recovery", (
        request_id, ORG, "artifact", ids["artifact"], "recover", 0,
        "verify artifact readback", "recovery-key-1",
    ))["outcome"] == "already_applied"
    assert _admin_call(database, "request_agent_runtime_recovery", (
        str(uuid4()), ORG, "artifact", ids["artifact"], "cleanup", 0,
        "different operation", "recovery-key-1",
    ))["outcome"] == "idempotency_conflict"
    assert _admin_call(database, "request_agent_runtime_recovery", (
        str(uuid4()), ORG, "artifact", ids["artifact"], "recover", 9,
        "stale", "recovery-key-2",
    ))["outcome"] == "stale_version"
    with pytest.raises(psycopg.Error, match="RUNTIME_ADMIN_REQUIRED"):
        _admin_call(database, "request_agent_runtime_recovery", (
            str(uuid4()), "33333333-3333-3333-3333-333333333333", "artifact",
            ids["artifact"], "recover", 0, "cross tenant", "recovery-key-3",
        ))

    claimed = _worker_rpc(database, "claim_agent_runtime_recovery", (
        intent["intent_id"], "recovery-worker", 120,
    ))
    assert claimed["outcome"] == "applied"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        with pytest.raises(psycopg.Error, match="RUNTIME_RECOVERY_INTENT_IMMUTABLE"):
            conn.execute(
                "UPDATE agent_runtime_recovery_intents SET reason='tampered' WHERE id=%s",
                (intent["intent_id"],),
            )
        conn.rollback()
        with pytest.raises(psycopg.Error, match="RUNTIME_RECOVERY_AUDIT_IMMUTABLE"):
            conn.execute("DELETE FROM agent_runtime_recovery_audit")
        conn.rollback()
        assert conn.execute(
            "SELECT has_table_privilege('everydayai_agent_runtime_worker',"
            "'agent_runtime_recovery_intents','SELECT')"
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_worker',"
            "'request_agent_runtime_recovery(uuid,uuid,text,text,text,bigint,text,text)','EXECUTE')"
        ).fetchone()[0] is False

    rollback = ROOT / "migrations/rollback/227_11_agent_runtime_recovery_center_rollback.sql"
    with pytest.raises(psycopg.Error, match="AR_17_5_ROLLBACK_BLOCKED_RECOVERY_FACTS"):
        _apply(database, rollback)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("TRUNCATE agent_runtime_recovery_audit, agent_runtime_recovery_intents")
        conn.commit()
    _apply(database, rollback)
    _apply(database, ROOT / "migrations/227_11_agent_runtime_recovery_center.sql")
    _apply(database, rollback)
