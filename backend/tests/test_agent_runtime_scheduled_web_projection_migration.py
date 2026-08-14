from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_36_agent_runtime_scheduled_web_projection.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_36_agent_runtime_scheduled_web_projection_rollback.sql",
).read_text()
APPLICATION = ROOT.joinpath(
    "services/agent/runtime/application/scheduled_delivery_projection.py",
).read_text()


def test_migration_has_narrow_projection_acl_and_failure_closed_rollback() -> None:
    assert "ENABLE ROW LEVEL SECURITY" in MIGRATION
    assert "FORCE ROW LEVEL SECURITY" in MIGRATION
    assert "TO everydayai_owner" in MIGRATION
    assert "session_user<>'everydayai_projection_worker'" in MIGRATION
    assert "app.access_kind',TRUE) IS DISTINCT FROM 'projection'" in MIGRATION
    assert MIGRATION.count("SET search_path=pg_catalog,public") >= 7
    assert "AGENT_RUNTIME_SCHEDULED_WEB_PROJECTION_ROLLBACK_HAS_FACTS" in ROLLBACK
    for role in (
        "PUBLIC", "everydayai_worker", "everydayai_agent_runtime_worker",
        "everydayai_authorization_worker", "everydayai_sandbox_worker",
    ):
        assert role in MIGRATION


def test_worker_has_no_legacy_delivery_or_direct_redis_dependency() -> None:
    for forbidden in (
        "MessageGateway", "PushDispatcher", "RedisClient", "message_gateway",
        "push_dispatcher", "services.scheduler",
    ):
        assert forbidden not in APPLICATION
    assert "send_to_user" in APPLICATION
    assert "production_ready = False" in APPLICATION
