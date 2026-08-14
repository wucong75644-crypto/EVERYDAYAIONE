from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
G1 = ROOT / "migrations/228_08g1_agent_runtime_media_real_event_normalization.sql"
G1_ROLLBACK = ROOT / (
    "migrations/rollback/228_08g1_agent_runtime_media_real_event_normalization_rollback.sql"
)
G2 = ROOT / "migrations/228_08g2_agent_runtime_media_model_video_wecom_outbox.sql"
G2_ROLLBACK = ROOT / (
    "migrations/rollback/228_08g2_agent_runtime_media_model_video_wecom_outbox_rollback.sql"
)
VIDEO_ROLLBACK = ROOT / (
    "migrations/rollback/228_08a_agent_runtime_media_model_video_rollback.sql"
)


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_real_event_normalization_is_narrow_and_reversible() -> None:
    sql = _sql(G1)
    rollback = _sql(G1_ROLLBACK)
    assert len(sql.splitlines()) < 500
    assert "p_event.correlation_id" in sql
    for event_type in (
        "action.provider.accepted", "action.provider.unknown",
        "action.completed_after_cancel", "action.failed_after_cancel",
    ):
        assert event_type in sql
    for scope in (
        "action.session_id IS DISTINCT FROM p_event.session_id",
        "action.run_id IS DISTINCT FROM p_event.run_id",
        "action.model_step_id IS DISTINCT FROM p_event.model_step_id",
        "action.org_id IS DISTINCT FROM p_event.org_id",
        "action.user_id IS DISTINCT FROM p_event.user_id",
    ):
        assert scope in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "SET search_path=pg_catalog,public" in sql
    assert "current_setting('app.access_kind',TRUE)" in sql
    assert "TO everydayai_projection_worker" in sql
    assert "_apply_agent_runtime_media_projection_228_06_v1" in rollback
    assert "_read_agent_runtime_media_projection_228_06_v1" in rollback
    assert "AGENT_RUNTIME_228_08G2_MUST_ROLL_BACK_FIRST" in rollback


def test_wecom_outbox_is_terminal_scoped_and_has_exact_guard() -> None:
    sql = _sql(G2)
    rollback = _sql(G2_ROLLBACK)
    assert len(sql.splitlines()) < 500
    assert "AFTER INSERT ON agent_projection_outbox" in sql
    assert "event.event_type NOT IN ('run.completed','run.failed','run.cancelled')" in sql
    assert "run.capability_snapshot->>'channel'<>'wecom'" in sql
    assert '\"channel\":\"wecom\"' in sql
    assert "agent_runtime_media_wecom_outbox_facts_v1" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "current_setting('app.access_kind',TRUE)" in sql
    assert "AGENT_RUNTIME_228_08G2_WECOM_OUTBOX_IN_USE" in rollback
    assert "AGENT_RUNTIME_228_08G2_WECOM_DELIVERY_NOT_DRAINED" in rollback
    assert "'claimed','dispatching','accepted','unknown'" in rollback
    assert "source_outbox.status='delivered'" in rollback
    assert "delivery_outbox.status='delivered'" in rollback
    assert "delivery.status='delivered'" in rollback


def test_normalization_rollback_requires_action_projection_drain() -> None:
    rollback = _sql(G1_ROLLBACK)
    assert "AGENT_RUNTIME_228_08G1_MODEL_VIDEO_NOT_DRAINED" in rollback
    assert "AGENT_RUNTIME_228_08G1_EVENT_PROJECTION_NOT_DRAINED" in rollback
    assert "'claimed','dispatching','accepted','unknown'" in rollback
    assert "outbox.status<>'delivered'" in rollback
    assert "result.action_id=action.id" in rollback


def test_08a_rollback_refuses_installed_08e1_wrapper() -> None:
    rollback = _sql(VIDEO_ROLLBACK)
    order_guard = rollback.index("_prepare_agent_runtime_model_video_fenced_v1")
    data_guard = rollback.index("ACTIVE_MODEL_VIDEO_FACTS")
    assert order_guard < data_guard
    assert "AGENT_RUNTIME_228_08E1_MUST_ROLL_BACK_FIRST" in rollback
