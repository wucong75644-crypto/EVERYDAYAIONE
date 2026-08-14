"""Static contract for AR-18-A1.2-B1.1 callable cancel facade."""

from pathlib import Path
import re

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_23_agent_runtime_task_cancel_facade_callable.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/227_23_agent_runtime_task_cancel_facade_callable_rollback.sql"
)
V1_SIGNATURE = "UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT"
V2_SIGNATURE = "UUID,UUID,UUID,UUID,UUID,UUID,TEXT"


def _body(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE(?: OR REPLACE)? FUNCTION {name}\b.*?AS \$\$(.*?)\$\$;",
        sql,
        re.DOTALL,
    )
    assert match, name
    return match.group(1)


def test_lane_identity_and_rollback_mapping() -> None:
    selected = [
        item for item in discover_migrations(ROOT / "migrations")
        if item.path == MIGRATION
    ]
    assert len(selected) == 1
    assert selected[0].rollback_identity == ROLLBACK.name


def test_v2_computes_canonical_hash_and_delegates_all_enforcement() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    body = _body(sql, "request_agent_runtime_task_cancel_v2")
    declaration = sql[:sql.index("AS $$")]
    assert "p_request_hash" not in declaration
    assert "SELECT session.user_id INTO v_scope_user_id" in body
    assert "FOR UPDATE" not in body
    assert "_agent_runtime_task_cancel_request_hash" in body
    assert "RETURN request_agent_runtime_task_cancel_v1" in body
    assert "_assert_agent_runtime_actor" not in body
    assert "jsonb_build_object" not in body
    assert len(body.splitlines()) <= 120
    assert "SECURITY DEFINER" in declaration
    assert "SET search_path = pg_catalog, public" in sql


def test_acl_exposes_only_v2_and_keeps_hash_helper_private() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert f"request_agent_runtime_task_cancel_v1(\n        {V1_SIGNATURE})" in sql
    assert f"request_agent_runtime_task_cancel_v2(\n        {V2_SIGNATURE})" in sql
    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime" in sql
    for role in (
        "PUBLIC", "everydayai_runtime", "everydayai_wecom_runtime",
        "everydayai_worker", "everydayai_sync", "everydayai",
        "everydayai_agent_runtime_worker", "everydayai_agent_model_gateway",
        "everydayai_projection_worker", "everydayai_authorization_worker",
        "everydayai_sandbox_worker",
    ):
        assert role in sql
    assert (
        f"GRANT EXECUTE ON FUNCTION request_agent_runtime_task_cancel_v2(\n"
        f"    {V2_SIGNATURE})\nTO everydayai_runtime, everydayai_wecom_runtime;"
    ) in sql
    assert "GRANT EXECUTE ON FUNCTION request_agent_runtime_task_cancel_v1" not in sql
    assert "GRANT EXECUTE ON FUNCTION _agent_runtime_task_cancel_request_hash" not in sql


def test_rollback_owns_no_facts_and_restores_v1_acl_exactly() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "owns no facts" in sql
    assert "FACTS_EXIST" not in sql
    assert "agent_runtime_task_cancel_intents" not in sql
    assert sql.index("DROP FUNCTION request_agent_runtime_task_cancel_v2") < sql.index(
        "GRANT EXECUTE ON FUNCTION request_agent_runtime_task_cancel_v1"
    )
    assert f"    {V1_SIGNATURE})\nTO everydayai_runtime, everydayai_wecom_runtime;" in sql
