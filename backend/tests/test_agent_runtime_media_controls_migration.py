from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_07_agent_runtime_media_controls.sql"
ROLLBACK = ROOT / "migrations/rollback/228_07_agent_runtime_media_controls_rollback.sql"


def test_runtime_media_controls_are_additive_and_fenced() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE agent_runtime_media_cancel_requests" in sql
    assert "CREATE TABLE agent_runtime_media_retry_lineage" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "retry_agent_runtime_media_slot_v1" in sql
    assert "request_agent_runtime_media_message_cancel_v1" in sql
    assert "cancel_agent_run(" in sql
    assert "generate_image" in sql
    assert "slot_revision" in sql
    assert "action_id,slot_id,task_id" in sql
    assert "AGENT_RUNTIME_MEDIA_PROJECTION_228_06_REQUIRED" in sql
    assert "'completed','failed','rejected','cancelled'" in sql
    assert "TO everydayai_agent_runtime_worker" in sql
    assert "TO everydayai_runtime,everydayai_wecom_runtime" in sql


def test_runtime_media_controls_rollback_only_drops_228_07() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "AGENT_RUNTIME_MEDIA_MESSAGE_CONTROL_IN_USE" in sql
    assert "DROP TABLE agent_runtime_media_retry_lineage" in sql
    assert "DROP TABLE agent_runtime_media_cancel_requests" in sql
    assert "228_04" not in sql


def test_runtime_media_controls_have_one_228_07_identity() -> None:
    assert list((ROOT / "migrations").glob("228_07_*.sql")) == [MIGRATION]
    assert list((ROOT / "migrations/rollback").glob("228_07_*.sql")) == [ROLLBACK]


def test_unified_retry_adapter_has_ordinary_image_gate() -> None:
    adapter = (ROOT / "api/routes/message_runtime_media_retry.py").read_text(encoding="utf-8")
    assert "gen_type != GenerationType.IMAGE" in adapter
    assert "MessageOperation.REGENERATE_SINGLE" in adapter
    assert "runtime_media_batch" in adapter
    assert "image_ecom" not in adapter
