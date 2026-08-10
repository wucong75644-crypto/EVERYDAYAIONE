from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
IDENTITY = "227_35_agent_runtime_scheduled_delivery_intents.sql"
ROLLBACK_IDENTITY = "227_35_agent_runtime_scheduled_delivery_intents_rollback.sql"
SQL = (ROOT / "migrations" / IDENTITY).read_text(encoding="utf-8")
ROLLBACK = (ROOT / "migrations" / "rollback" / ROLLBACK_IDENTITY).read_text(
    encoding="utf-8",
)


def test_migration_identity_order_and_additive_boundary() -> None:
    discovered = discover_migrations(ROOT / "migrations")
    identities = [item.identity for item in discovered]
    item = next(item for item in discovered if item.identity == IDENTITY)
    assert item.rollback_identity == ROLLBACK_IDENTITY
    assert identities.index(IDENTITY) > identities.index(
        "227_34_agent_runtime_scheduled_run_credit_budget.sql"
    )
    for frozen in ("227_28", "227_29", "227_30", "227_31", "227_32", "227_33", "227_34"):
        assert frozen not in SQL + ROLLBACK


def test_snapshot_and_intent_facts_are_immutable_and_secret_free() -> None:
    for table in (
        "agent_runtime_scheduled_delivery_snapshots",
        "agent_runtime_scheduled_delivery_targets",
        "agent_runtime_scheduled_delivery_runtime_bindings",
        "agent_runtime_scheduled_delivery_intents",
    ):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in SQL
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in SQL
        assert f"ON {table} FOR EACH ROW" in SQL
    assert "CHECK(status='pending')" in SQL
    assert "CHECK(state_version=0)" in SQL
    assert "result_hash" in SQL and "content_identity_hash" in SQL
    lowered = SQL.lower()
    for forbidden in (
        "provider_payload", "model_result body", "api_key", "password",
        "credential_handle", "storage_path", "stack_trace",
    ):
        assert forbidden not in lowered


def test_real_submission_and_finalization_surfaces_are_trigger_bound() -> None:
    assert "AFTER INSERT ON agent_runtime_scheduled_submission_intents" in SQL
    assert "AFTER UPDATE OF runtime_run_id ON agent_runtime_scheduled_run_bindings" in SQL
    assert "AFTER UPDATE OF status ON agent_runtime_scheduled_finalization_intents" in SQL
    assert "binding.owner_kind<>'runtime'" in SQL
    assert "NEW.status<>'applied'" in SQL
    assert "runtime_run_id" in SQL and "finalization_application_hash" in SQL
    assert "Redis" not in SQL and "publish" not in SQL.lower()


def test_target_contract_uses_proven_shapes_and_bounded_expansion() -> None:
    for target_type in ("web", "wecom_user", "wecom_group", "multi"):
        assert f"kind='{target_type}'" in SQL
    assert "_runtime_scheduler_push_target_allowed" in SQL
    assert "p_depth NOT BETWEEN 0 AND 4" in SQL
    assert "raw_count NOT BETWEEN 1 AND 20" in SQL
    assert "DISTINCT ON(target_key)" in SQL
    assert "ORDER BY normalized.target_key" in SQL


def test_projection_readback_is_narrow_and_tables_have_no_worker_rights() -> None:
    signature = "read_agent_runtime_scheduled_delivery_intents_v1(UUID,UUID,UUID)"
    assert f"GRANT EXECUTE ON FUNCTION {signature}" in SQL
    assert "TO everydayai_projection_worker" in SQL
    assert "session_user<>'everydayai_projection_worker'" in SQL
    assert "current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'projection'" in SQL
    assert "SECURITY DEFINER SET search_path=pg_catalog,public" in SQL
    assert "everydayai_agent_runtime_worker" in SQL
    assert "GRANT SELECT" not in SQL and "GRANT INSERT" not in SQL


def test_apply_and_rollback_fail_closed_without_silent_backfill() -> None:
    assert "AGENT_RUNTIME_SCHEDULED_DELIVERY_BACKFILL_REQUIRED" in SQL
    assert "LOCK TABLE agent_runtime_scheduled_submission_intents" in SQL
    assert "agent_runtime_scheduled_submission_intents" in SQL.split("CREATE TABLE", 1)[0]
    assert "INSERT INTO agent_runtime_scheduled_delivery_snapshots" in SQL
    assert "INSERT INTO agent_runtime_scheduled_delivery_intents" in SQL
    assert "AGENT_RUNTIME_SCHEDULED_DELIVERY_ROLLBACK_FACTS_EXIST" in ROLLBACK
    assert "LOCK TABLE agent_runtime_scheduled_delivery_intents" in ROLLBACK
    guard = ROLLBACK.split("DROP TRIGGER", 1)[0]
    for table in (
        "agent_runtime_scheduled_delivery_snapshots",
        "agent_runtime_scheduled_delivery_targets",
        "agent_runtime_scheduled_delivery_runtime_bindings",
        "agent_runtime_scheduled_delivery_intents",
    ):
        assert table in guard
