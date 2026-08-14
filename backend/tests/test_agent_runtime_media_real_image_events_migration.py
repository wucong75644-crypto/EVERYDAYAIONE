from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
I1 = ROOT / (
    "migrations/228_08i1_agent_runtime_media_real_image_event_normalization.sql"
)
I1_ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08i1_agent_runtime_media_real_image_event_normalization_rollback.sql"
)
I2 = ROOT / (
    "migrations/228_08i2_agent_runtime_media_model_image_wecom_outbox.sql"
)
I2_ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08i2_agent_runtime_media_model_image_wecom_outbox_rollback.sql"
)


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_i1_normalizes_only_strict_real_image_events() -> None:
    sql = _sql(I1)
    assert len(sql.splitlines()) < 500
    assert "_agent_runtime_media_normalize_image_event_v1" in sql
    assert "action.tool_name<>'generate_image'" in sql
    assert "_agent_runtime_prepared_media_source_v1(action.id)<>'media_ingress'" in sql
    assert "p_event.actor_type IN ('model','user')" in sql
    assert "p_event.actor_type IN ('executor','system')" in sql
    assert "WHEN 'action.rejected' THEN p_event.actor_type='system'" in sql
    assert "'system','executor','reconciler'" in sql
    assert "action_loop" not in sql
    assert "p_event.actor_type<>'user'" in sql
    assert "p_event.actor_type<>'model'" in sql
    assert "source NOT IN ('model_loop','runtime_executor_registry')" in sql
    for scope in (
        "action.session_id IS DISTINCT FROM p_event.session_id",
        "action.run_id IS DISTINCT FROM p_event.run_id",
        "action.model_step_id IS DISTINCT FROM p_event.model_step_id",
        "action.org_id IS DISTINCT FROM p_event.org_id",
        "action.user_id IS DISTINCT FROM p_event.user_id",
    ):
        assert scope in sql
    for event_type in (
        "action.provider.accepted", "action.provider.unknown",
        "action.completed_after_cancel", "action.failed_after_cancel",
    ):
        assert event_type in sql
    assert "normalized.action_id:=action.id" in sql
    assert "_agent_runtime_media_action_projection_v1" in sql
    assert "agent_runtime_media_image_normalized_event_v1" in sql
    assert "agent_runtime_media_image_zbatch_result_v1" in sql
    assert sql.index("agent_runtime_media_image_normalized_event_v1") < sql.index(
        "agent_runtime_media_image_zbatch_result_v1"
    )
    assert "SET search_path=pg_catalog,public" in sql


def test_i1_rollback_is_ordered_and_drain_guarded() -> None:
    rollback = _sql(I1_ROLLBACK)
    assert "AGENT_RUNTIME_228_08I2_MUST_ROLL_BACK_FIRST" in rollback
    assert "AGENT_RUNTIME_228_08I1_IMAGE_EVENTS_NOT_DRAINED" in rollback
    assert "AGENT_RUNTIME_228_08I1_IMAGE_PROJECTION_NOT_DRAINED" in rollback
    assert "'claimed','dispatching','accepted','unknown'" in rollback
    assert "RENAME TO _agent_runtime_media_normalize_model_video_event_v1" in rollback


def test_i2_replaces_video_only_trigger_with_strict_media_dispatcher() -> None:
    sql = _sql(I2)
    assert len(sql.splitlines()) < 500
    assert "DROP TRIGGER agent_runtime_media_model_video_wecom_outbox_v1" in sql
    assert "agent_runtime_media_model_media_wecom_outbox_v2" in sql
    assert "run.capability_snapshot->>'channel'<>'wecom'" in sql
    assert "prepared.action_id IS NULL" in sql
    assert "action.tool_name='generate_image'" in sql
    assert "binding.chat_task_id IS DISTINCT FROM parent_task.id" in sql
    assert "binding.output_message_id IS DISTINCT FROM output_message.id" in sql
    assert "image_count NOT BETWEEN 1 AND 10" in sql
    assert "AGENT_RUNTIME_MEDIA_RUN_OWNER_AMBIGUOUS" in sql
    assert "INSERT INTO agent_projection_outbox" in sql
    assert "agent_runtime_media_image_wecom_outbox_facts_v1" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "current_setting('app.access_kind',TRUE)" in sql
    assert "SET search_path=pg_catalog,public" in sql


def test_i2_rollback_restores_g2_owner_after_delivery_drain() -> None:
    rollback = _sql(I2_ROLLBACK)
    assert "AGENT_RUNTIME_228_08I2_IMAGE_WECOM_NOT_DRAINED" in rollback
    assert "AGENT_RUNTIME_228_08I2_IMAGE_WECOM_DELIVERY_NOT_DRAINED" in rollback
    assert "source_outbox.status='delivered'" in rollback
    assert "delivery_outbox.status='delivered'" in rollback
    assert "delivery.status='delivered'" in rollback
    assert "CREATE TRIGGER agent_runtime_media_model_video_wecom_outbox_v1" in rollback


def test_i_identities_are_unique() -> None:
    assert list((ROOT / "migrations").glob("228_08i1_*.sql")) == [I1]
    assert list((ROOT / "migrations").glob("228_08i2_*.sql")) == [I2]
    assert list((ROOT / "migrations/rollback").glob("228_08i1_*.sql")) == [
        I1_ROLLBACK
    ]
    assert list((ROOT / "migrations/rollback").glob("228_08i2_*.sql")) == [
        I2_ROLLBACK
    ]
