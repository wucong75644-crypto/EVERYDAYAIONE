"""Static contracts for the additive 228.06 media Projection lane."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_06_agent_runtime_media_projection.sql"
ROLLBACK = ROOT / "migrations/rollback/228_06_agent_runtime_media_projection_rollback.sql"


def test_projection_lane_is_additive_and_fenced() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE agent_runtime_media_projection_checkpoints" in sql
    assert "CREATE TABLE agent_runtime_media_projection_results" in sql
    assert "ALTER TABLE agent_runtime_media_action_bindings ADD COLUMN slot_id UUID" in sql
    assert sql.index("UPDATE agent_runtime_media_action_bindings SET slot_id = action_id") < sql.index(
        "ALTER TABLE agent_runtime_media_action_bindings ALTER COLUMN slot_id SET NOT NULL",
    )
    assert "CREATE TRIGGER agent_runtime_media_binding_slot_default_v1" in sql
    assert "claim_agent_runtime_media_projection_v1" in sql
    assert "read_agent_runtime_media_projection_v1" in sql
    assert "apply_agent_runtime_media_projection_v1" in sql
    assert "register_agent_runtime_media_asset_v1" in sql
    assert "fail_agent_runtime_media_projection_v1" in sql
    assert "session_user <> 'everydayai_projection_worker'" in sql
    assert "app.access_kind', TRUE) IS DISTINCT FROM 'projection'" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "event.sequence > checkpoint.through_sequence" in sql
    assert "AGENT_RUNTIME_MEDIA_PROJECTION_GAP" in sql
    assert "lease_token IS DISTINCT FROM p_lease_token" in sql
    assert "GRANT SELECT" not in sql
    assert "GRANT EXECUTE ON FUNCTION" in sql
    assert "CREATE OR REPLACE FUNCTION claim_agent_compat_projection_outbox" in sql
    assert "agent_runtime_media_action_bindings binding" in sql
    assert "action.rejected" in sql


def test_terminal_projection_reads_persistent_facts_and_merges_slots() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "agent_action_results" in sql
    assert "agent_runtime_provider_submission_facts" in sql
    assert "p_content_part->>'source_url'" in sql
    assert "AGENT_RUNTIME_MEDIA_AUTHORITATIVE_RESULT_REQUIRED" in sql
    assert "AGENT_RUNTIME_MEDIA_FACT_SCOPE_INVALID" in sql
    assert "credits_locked = 0" in sql
    assert "credits_used = v_binding.unit_credits" in sql
    assert "result = p_content_part" in sql
    assert "result_data =" not in sql
    assert "AGENT_RUNTIME_MEDIA_PROVIDER_FACT_REQUIRED" in sql
    assert "event_sequence = v_event.sequence" in sql
    assert "settle_agent_runtime_media_credit_v1" in sql
    assert "refund_agent_runtime_media_credit_v1" in sql
    assert "v_slots || v_other || jsonb_build_array(p_final)" in sql
    assert "candidate.slot_id" in sql
    assert "v_binding.slot_id" in sql


def test_rollback_guard_requires_drained_projection_and_bindings() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "AGENT_RUNTIME_MEDIA_PROJECTION_IN_USE" in rollback
    assert "AGENT_RUNTIME_MEDIA_CONTROLS_MUST_ROLL_BACK_FIRST" in rollback
    assert "agent_runtime_media_projection_results" in rollback
    assert "projection_revision > 0 OR credit_state <> 'pending'" in rollback
    assert "DROP TABLE agent_runtime_media_projection_results" in rollback
    assert "DROP TABLE agent_runtime_media_projection_checkpoints" in rollback
    assert "ALTER TABLE agent_runtime_media_action_bindings DROP COLUMN slot_id" in rollback
    assert "DROP FUNCTION _agent_runtime_media_binding_slot_default_v1()" in rollback
    assert "DROP FUNCTION _agent_runtime_media_action_projection_v1" in rollback
    assert "restore its exact 220.12" in rollback
    assert "CREATE OR REPLACE FUNCTION claim_agent_compat_projection_outbox" in rollback
    assert "'action.rejected'" not in rollback
