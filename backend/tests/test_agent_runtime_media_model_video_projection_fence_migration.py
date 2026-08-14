from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
E1 = ROOT / "migrations/228_08e1_agent_runtime_media_model_video_fence.sql"
E1_ROLLBACK = ROOT / (
    "migrations/rollback/228_08e1_agent_runtime_media_model_video_fence_rollback.sql"
)
E2 = ROOT / "migrations/228_08e2_agent_runtime_media_model_video_projection.sql"
E2_ROLLBACK = ROOT / (
    "migrations/rollback/228_08e2_agent_runtime_media_model_video_projection_rollback.sql"
)


def test_model_video_patch_is_ordered_and_bounded() -> None:
    e1 = E1.read_text(encoding="utf-8")
    e2 = E2.read_text(encoding="utf-8")
    assert len(e1.splitlines()) < 500
    assert len(e2.splitlines()) < 500
    assert "AGENT_RUNTIME_MEDIA_228_08D_08A_REQUIRED" in e1
    assert "AGENT_RUNTIME_MEDIA_228_08E1_08B_REQUIRED" in e2
    assert "228_08d_agent_runtime_media_atomic_image_batch" not in e1 + e2
    assert "CREATE OR REPLACE FUNCTION apply_agent_runtime_media_projection_v1" not in e2
    assert "CREATE OR REPLACE FUNCTION read_agent_runtime_media_projection_v1" not in e2


def test_fence_recheck_follows_every_serializing_lock() -> None:
    sql = E1.read_text(encoding="utf-8")
    helper = sql[sql.index("CREATE FUNCTION _prepare_agent_runtime_model_video_fenced_v1"):]
    recheck = helper.index("fresh_context:=_agent_runtime_media_attempt_context_v2")
    legacy = helper.index("RETURN _prepare_agent_runtime_model_video_v1")
    assert helper.index("pg_advisory_xact_lock") < recheck
    assert helper.index("FROM agent_action_attempts") < recheck
    assert helper.index("FOR UPDATE OF intent,receipt") < recheck < legacy
    assert "worker_id',p_worker_id" in sql
    assert "owner_token',p_owner_token" in sql
    assert "expected_attempt_version',p_expected_attempt_version" in sql


def test_projection_patch_separates_action_run_and_wecom_owners() -> None:
    sql = E2.read_text(encoding="utf-8")
    rollback = E2_ROLLBACK.read_text(encoding="utf-8")
    assert "_agent_runtime_prepared_media_source_v1" in sql
    assert "_agent_runtime_media_model_video_action_projection_v1" in sql
    assert "_agent_runtime_media_model_video_run_projection_v1" in sql
    assert "BEFORE INSERT ON agent_runtime_media_projection_results" in sql
    assert "agent_runtime_media_model_video_wecom_delivery_v1" in sql
    assert "AGENT_RUNTIME_228_08E2_ACTIVE_MODEL_VIDEO_FACTS" in rollback
    assert "RENAME TO _agent_runtime_media_prepared_action_projection_v1" in rollback
    assert "AGENT_RUNTIME_228_08E2_MUST_ROLL_BACK_FIRST" in E1_ROLLBACK.read_text(
        encoding="utf-8"
    )
