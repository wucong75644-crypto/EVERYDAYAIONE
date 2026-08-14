from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_07_agent_runtime_media_controls.sql"
ROLLBACK = ROOT / "migrations/rollback/228_07_agent_runtime_media_controls_rollback.sql"
CANCEL_HANDOFF = ROOT / "migrations/227_24_agent_runtime_provider_cancel_handoff.sql"


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
    assert "CREATE TRIGGER agent_runtime_media_retry_run_guard" in sql
    assert "'source','runtime_media_retry'" in sql
    assert "'execution_mode','one_shot_action'" in sql
    assert "'projection_mode','media_action_only'" in sql
    assert "model_loop_enabled" not in sql
    assert "_agent_runtime_media_cancel_dispatch_v1" in sql
    assert "'action.unknown'" in sql
    assert "provider_task_ref" in sql
    assert "provider_idempotency_key" in sql
    assert "provider_request_hash" in sql
    assert "source_binding.credit_state <> 'refunded'" in sql
    assert "source_task.status::TEXT NOT IN ('failed','cancelled')" in sql
    assert "conversation.org_id,conversation.user_id" in sql
    assert "'assistant',p_org_id,p_user_id" in sql
    assert "'task_id',action_id" in sql


def test_runtime_media_controls_rollback_only_drops_228_07() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "AGENT_RUNTIME_MEDIA_MESSAGE_CONTROL_IN_USE" in sql
    assert "DROP TABLE agent_runtime_media_retry_lineage" in sql
    assert "DROP TABLE agent_runtime_media_cancel_requests" in sql
    assert "DROP FUNCTION _agent_runtime_media_cancel_dispatch_v1" in sql
    assert '"source":"runtime_media_retry"' in sql
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


def test_cancelled_run_unknown_attempt_uses_ar18_cancel_handoff() -> None:
    sql = CANCEL_HANDOFF.read_text(encoding="utf-8")
    assert "operation:=CASE WHEN run.status='cancelled' THEN 'cancel'" in sql
    assert "request_agent_runtime_provider_cancel" in sql
