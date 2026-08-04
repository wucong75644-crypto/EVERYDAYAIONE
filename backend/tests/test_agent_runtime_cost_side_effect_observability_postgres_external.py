from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar173_postgres_external import _seed_specialist_action, _worker_rpc, database
from tests.test_agent_runtime_tenant_kill_control_postgres_external import _admin_call


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ORG = "22222222-2222-2222-2222-222222222222"


def _apply(url: str, path: Path) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))


def test_cost_side_effect_snapshot_and_rollback(database: str) -> None:
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
    for index in range(1, 13):
        _apply(database, next((ROOT / "migrations").glob(f"227_{index:02d}_*.sql")))
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
        ids["request_hash"], "mock-provider", "mock-v1", "ledger-key",
    ))
    assert created["outcome"] == "created"
    submitted = _worker_rpc(database, "record_agent_runtime_provider_submitted", (
        created["submission_id"], ids["token"], ids["request_hash"], 0,
        "provider-task-ledger", "/status", None,
    ))
    assert submitted["state"] == "submitted"
    reserve = _worker_rpc(database, "record_agent_action_cost_strict", (
        ids["action"], ids["attempt"], "reserve", 7, 0, "credits", "runtime", None,
    ))
    assert reserve["outcome"] == "applied"
    assert _worker_rpc(database, "record_agent_action_cost_strict", (
        ids["action"], ids["attempt"], "reserve", 7, 0, "credits", "runtime", None,
    ))["outcome"] in {"idempotent_readback", "duplicate"}
    snapshot = _admin_call(database, "get_agent_runtime_cost_side_effect_snapshot", (
        ORG, None, None, None, 100,
    ))
    assert snapshot["outcome"] == "readback"
    assert snapshot["currency_contract"] == "credits_minor_integer"
    assert snapshot["cost_ledger"][0]["cost_state"] == "SETTLEMENT_PENDING"
    assert snapshot["side_effect_ledger"][0]["provider"] == "mock-provider"
    assert snapshot["production_ready"] is False
    rendered = repr(snapshot)
    assert "execution_token" not in rendered
    assert "provider_payload" not in rendered
    assert "credential" not in rendered.lower()
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_agent_runtime_worker',"
            "'get_agent_runtime_cost_side_effect_snapshot(uuid,text,text,text,integer)','EXECUTE')"
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_runtime_admin',"
            "'get_agent_runtime_cost_side_effect_snapshot(uuid,text,text,text,integer)','EXECUTE')"
        ).fetchone()[0] is True
    rollback = ROOT / "migrations/rollback/227_12_agent_runtime_cost_side_effect_observability_rollback.sql"
    with pytest.raises(psycopg.Error, match="AR_17_6_ROLLBACK_BLOCKED_LEDGER_FACTS"):
        _apply(database, rollback)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "TRUNCATE agent_action_cost_settlements, agent_runtime_provider_operation_intents, "
            "agent_runtime_provider_submission_facts"
        )
        conn.commit()
    _apply(database, rollback)
