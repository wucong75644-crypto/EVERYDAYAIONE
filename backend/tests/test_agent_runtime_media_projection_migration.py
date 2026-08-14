"""Static contracts for the additive 228.06 media Projection lane."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_06_agent_runtime_media_projection.sql"
ROLLBACK = ROOT / "migrations/rollback/228_06_agent_runtime_media_projection_rollback.sql"
ISOLATION = ROOT / "migrations/228_06a_agent_runtime_media_projection_isolation.sql"
ISOLATION_ROLLBACK = ROOT / "migrations/rollback/228_06a_agent_runtime_media_projection_isolation_rollback.sql"
READINESS = ROOT / "migrations/228_06b_agent_runtime_media_projection_readiness.sql"
READINESS_ROLLBACK = ROOT / "migrations/rollback/228_06b_agent_runtime_media_projection_readiness_rollback.sql"
SLOT_RELEASE = ROOT / "migrations/228_06c_agent_runtime_media_slot_release.sql"
SLOT_RELEASE_ROLLBACK = ROOT / "migrations/rollback/228_06c_agent_runtime_media_slot_release_rollback.sql"


def test_projection_lane_is_additive_and_fenced() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE agent_runtime_media_projection_checkpoints" in sql
    assert "CREATE TABLE agent_runtime_media_projection_results" in sql
    assert "CREATE TABLE agent_runtime_media_projection_recoveries" in sql
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
    assert "agent_runtime_prepared_media_action_bindings binding" in sql
    assert "action.rejected" in sql
    assert "_agent_runtime_media_owner_readiness_v1" not in sql
    assert "v_readiness" not in sql


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
    assert "v_data->'result_urls'" in sql
    assert "v_prepared.media_kind" in sql
    assert "attempt_count >= 8" in sql
    assert "requeue_agent_runtime_media_projection_v1" in sql
    assert "runtime_media_retry" in sql
    assert "one_shot_action" in sql
    assert "media_action_only" in sql
    assert "runtime_media_slot_retry" not in sql
    assert "action_only_run_v1(v_event.run_id)" in sql
    assert "WHERE id=v_binding.task_id FOR UPDATE" in sql
    assert "WHERE action_id=p_event.action_id FOR UPDATE" in sql
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
    assert "agent_runtime_media_projection_recoveries" in rollback
    assert "OR EXISTS (SELECT 1 FROM agent_runtime_media_projection_recoveries)" in rollback
    assert "action.status NOT IN ('completed','failed','rejected','cancelled')" in rollback
    assert "outbox.status<>'delivered'" in rollback
    assert "credit_state='pending'" in rollback
    assert "SELECT 1 FROM agent_runtime_media_projection_checkpoints" not in rollback
    assert "DROP TABLE agent_runtime_media_projection_results" in rollback
    assert "DROP TABLE agent_runtime_media_projection_checkpoints" in rollback
    assert "ALTER TABLE agent_runtime_media_action_bindings DROP COLUMN slot_id" in rollback
    assert "DROP FUNCTION _agent_runtime_media_action_only_run_v1(UUID)" in rollback
    assert "DROP FUNCTION _agent_runtime_media_binding_slot_default_v1()" in rollback
    assert "DROP FUNCTION _agent_runtime_media_action_projection_v1" in rollback
    assert "restore its exact 220.12" in rollback
    assert "CREATE OR REPLACE FUNCTION claim_agent_compat_projection_outbox" in rollback
    assert "'action.rejected'" not in rollback


def test_poison_isolation_is_fenced_audited_and_compensating() -> None:
    sql = ISOLATION.read_text(encoding="utf-8")
    rollback = ISOLATION_ROLLBACK.read_text(encoding="utf-8")
    assert "CREATE TABLE agent_runtime_media_projection_isolations" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "isolate_agent_runtime_media_projection_v1" in sql
    assert "isolate_dead_agent_runtime_media_projection_v1" in sql
    assert "lease_token IS DISTINCT FROM p_lease_token" in sql
    assert "recovery_version IS DISTINCT FROM p_expected_recovery_version" in sql
    assert "atomic_refund_credits" in sql
    assert sql.index("FROM tasks WHERE id=v_binding.task_id FOR UPDATE") < sql.index(
        "FROM agent_runtime_media_action_bindings\n             WHERE action_id=v_event.action_id FOR UPDATE"
    ) < sql.index("FROM messages\n             WHERE id=v_binding.output_message_id FOR UPDATE")
    assert "through_sequence=v_event.sequence" in sql
    assert "'isolated',TRUE" in sql
    assert "TO everydayai_projection_worker" in sql
    assert "TO everydayai_runtime_admin" in sql
    assert "AGENT_RUNTIME_MEDIA_ISOLATION_AUDIT_PRESENT" in rollback
    lock = "WHERE id=p_outbox_id FOR UPDATE"
    assert sql.index(lock) < sql.index("IF p_worker_id IS NOT NULL")
    assert sql.index(lock) < sql.index(
        "v_outbox.recovery_version IS DISTINCT FROM p_expected_recovery_version"
    )


def test_projection_readiness_requires_control_probe_heartbeat_and_fence() -> None:
    sql = READINESS.read_text(encoding="utf-8")
    rollback = READINESS_ROLLBACK.read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION record_agent_runtime_media_projection_readiness_v1" in sql
    assert "runtime_control.projection_enabled" in sql
    assert "runtime_control.release_revision=btrim(p_projection_revision)" in sql
    assert "media_control.provider_probe_passed" in sql
    assert "media_control.runtime_enabled" in sql
    assert "heartbeat.ready AND NOT heartbeat.draining" in sql
    assert "heartbeat.observed_at>=statement_timestamp()" in sql
    assert "media_projection_enabled" in sql
    assert "media_provider_probe_passed" in sql
    assert "projection_owner_ready=COALESCE(effective_ready,FALSE)" in sql
    assert "projection_owner_ready=p_ready" in rollback
    assert "AGENT_RUNTIME_MEDIA_READINESS_ROLLBACK_NOT_DRAINED" in rollback
    assert "AGENT_RUNTIME_MEDIA_READINESS_ROLLBACK_HEARTBEAT_ACTIVE" in rollback
    assert "AGENT_RUNTIME_MEDIA_READINESS_ROLLBACK_IN_FLIGHT" in rollback


def test_durable_slot_release_is_fenced_retryable_and_audited() -> None:
    sql = SLOT_RELEASE.read_text(encoding="utf-8")
    rollback = SLOT_RELEASE_ROLLBACK.read_text(encoding="utf-8")
    assert "CREATE TABLE agent_runtime_media_slot_release_outbox" in sql
    assert "CREATE TABLE agent_runtime_media_slot_release_recoveries" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "AFTER INSERT ON agent_runtime_media_projection_results" in sql
    assert "claim_agent_runtime_media_slot_release_v1" in sql
    assert "ack_agent_runtime_media_slot_release_v1" in sql
    assert "fail_agent_runtime_media_slot_release_v1" in sql
    assert "requeue_agent_runtime_media_slot_release_v1" in sql
    assert "attempt_count>=8" in sql
    assert "lease_token IS DISTINCT FROM p_lease_token" in sql
    assert "app.access_kind',TRUE) IS DISTINCT FROM 'runtime_admin'" in sql
    assert "TO everydayai_projection_worker" in sql
    assert "TO everydayai_runtime_admin" in sql
    assert "AGENT_RUNTIME_MEDIA_SLOT_RELEASE_HISTORY_PRESENT" in rollback
