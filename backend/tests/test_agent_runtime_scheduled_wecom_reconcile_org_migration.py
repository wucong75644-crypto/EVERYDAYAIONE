from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_52_agent_runtime_scheduled_wecom_reconcile_org.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_52_agent_runtime_scheduled_wecom_reconcile_org_rollback.sql",
).read_text()
PREDECESSOR = ROOT.joinpath(
    "migrations/227_41_agent_runtime_scheduled_wecom_reconcile_claim.sql",
).read_text()
HELPER = "_agent_runtime_scheduled_wecom_reconcile_json"


def _function(sql: str, name: str) -> str:
    marker = f"FUNCTION {name}("
    start = sql.index(marker)
    start = sql.rfind("CREATE", 0, start)
    end = sql.index("\n$$;", start) + len("\n$$;")
    return sql[start:end].replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)


def test_reconcile_org_comes_only_from_locked_delivery_identity() -> None:
    helper = _function(MIGRATION, HELPER)
    assert "'org_id',p_delivery.org_id" in helper
    assert helper.count("'org_id'") == 1
    assert "p_org_id" not in helper
    assert "channel" not in helper.lower()
    for rpc in ("claim_", "renew_", "read_"):
        assert f"CREATE FUNCTION {rpc}agent_runtime_scheduled_wecom_reconcile" not in MIGRATION


def test_replace_preserves_security_shape_and_adds_no_database_surface() -> None:
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_ORG_DEPENDENCY_DRIFT" in MIGRATION
    assert "pg_get_userbyid(p.proowner)<>'everydayai_owner'" in MIGRATION
    assert "STABLE SECURITY DEFINER SET search_path=pg_catalog,public" in MIGRATION
    for forbidden in (
        "CREATE TABLE", "CREATE INDEX", "ALTER TABLE", "CREATE POLICY",
        "GRANT ", "REVOKE ", "CREATE ROLE", "ALTER ROLE",
    ):
        assert forbidden not in MIGRATION
        assert forbidden not in ROLLBACK


def test_rollback_restores_exact_predecessor_helper_without_fact_guard() -> None:
    assert _function(ROLLBACK, HELPER) == _function(PREDECESSOR, HELPER)
    assert "DO $$" not in ROLLBACK
    assert "DROP " not in ROLLBACK
    assert "org_id" not in _function(ROLLBACK, HELPER)
    assert len(MIGRATION.splitlines()) <= 500
    assert len(ROLLBACK.splitlines()) <= 500
