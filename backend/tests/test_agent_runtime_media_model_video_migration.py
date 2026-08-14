from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_08a_agent_runtime_media_model_video.sql"
ROLLBACK = ROOT / (
    "migrations/rollback/228_08a_agent_runtime_media_model_video_rollback.sql"
)


def test_model_video_migration_is_additive_and_worker_scoped() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE FUNCTION _prepare_agent_runtime_model_video_v1" in sql
    assert "CREATE OR REPLACE FUNCTION prepare_agent_runtime_media_dispatch_v1" in sql
    assert "CREATE OR REPLACE FUNCTION read_agent_runtime_media_provider_request_v1" in sql
    assert "SET search_path=pg_catalog,public" in sql
    assert "PERFORM _agent_runtime_media_worker_v1()" in sql
    assert "_agent_runtime_media_attempt_context_v2" in sql
    assert "p_context->>'source' NOT IN ('model_loop','runtime_executor_registry')" in sql
    assert "agent_runtime_prepared_media_action_bindings" in sql
    assert "agent_runtime_prepared_media_video_pricing_facts" in sql
    assert "'sora-2-text-to-video'" in sql
    assert "runtime_session.conversation_id,'video'" in sql
    assert "'preparing',pricing.user_credits" in sql
    assert "'runtime_owner','action_loop'" in sql
    assert "AGENT_RUNTIME_MODEL_VIDEO_READBACK_ONLY" in sql
    assert "attempt.status IN ('accepted','unknown')" in sql
    assert "'source',source,'kind',kind" in sql
    assert "FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime" in sql
    assert "TO everydayai_agent_runtime_worker" not in sql
    assert "CREATE TABLE" not in sql
    assert "ALTER TABLE" not in sql
    assert "adapter.generate" not in sql


def test_model_video_rollback_restores_prior_dispatch_contract() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")

    assert "AGENT_RUNTIME_228_08A_ACTIVE_MODEL_VIDEO_FACTS" in rollback
    assert "action.tool_name='generate_video'" in rollback
    assert "CREATE OR REPLACE FUNCTION prepare_agent_runtime_media_dispatch_v1" in rollback
    assert "context->>'tool_name'<>'generate_image'" in rollback
    assert "CREATE OR REPLACE FUNCTION read_agent_runtime_media_provider_request_v1" in rollback
    assert "'source',context->>'source'" in rollback
    assert "DROP FUNCTION _prepare_agent_runtime_model_video_v1(JSONB,TEXT)" in rollback
    assert "DELETE FROM" not in rollback
    assert "DROP TABLE" not in rollback
