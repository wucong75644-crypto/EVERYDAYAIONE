"""Static contracts for additive 228.08b media WeCom terminal delivery."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_08b_agent_runtime_media_wecom_delivery.sql"
ROLLBACK = ROOT / (
    "migrations/rollback/228_08b_agent_runtime_media_wecom_delivery_rollback.sql"
)


def test_media_wecom_delivery_is_additive_single_owner_and_fail_closed() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "228_06_agent_runtime_media_projection.sql" not in sql
    assert "CREATE FUNCTION _project_agent_runtime_media_wecom_delivery_v1()" in sql
    assert "AFTER INSERT ON agent_runtime_media_projection_results" in sql
    assert "WHEN (NEW.projection_kind='wecom')" in sql
    assert "_agent_runtime_media_projection_scope_v1()" in sql
    assert "v_outbox.status<>'processing'" in sql
    assert "v_event.event_type NOT IN" in sql
    assert "'run.completed','run.failed','run.cancelled'" in sql
    assert "NEW.projection_action NOT IN (v_expected_action,'checkpoint_only')" in sql
    assert "'{\"actor\":false,\"runtime\":true,\"channel\":\"wecom\"}'" in sql
    assert "INSERT INTO conversation_deliveries" in sql
    assert "'wecom','assistant_terminal'" in sql
    assert "ON CONFLICT (task_id,channel,delivery_kind) DO NOTHING" in sql
    assert "FOR UPDATE" not in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path=pg_catalog,public" in sql
    assert "TO everydayai_projection_worker" not in sql


def test_media_wecom_delivery_rollback_is_exact_and_guarded() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_IN_FLIGHT" in rollback
    assert "AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_HISTORY_PRESENT" in rollback
    assert "DROP TRIGGER agent_runtime_media_wecom_delivery_v1" in rollback
    assert "DROP FUNCTION _project_agent_runtime_media_wecom_delivery_v1()" in rollback
    assert "DROP TABLE" not in rollback
    assert "DELETE FROM" not in rollback


def test_media_wecom_delivery_has_one_migration_identity() -> None:
    assert list((ROOT / "migrations").glob("228_08b_*.sql")) == [MIGRATION]
    assert list((ROOT / "migrations/rollback").glob("228_08b_*.sql")) == [
        ROLLBACK
    ]
