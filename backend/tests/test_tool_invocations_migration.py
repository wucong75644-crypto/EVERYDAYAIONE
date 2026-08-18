"""工具幂等迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "139_tool_invocations.sql"
ROLLBACK = MIGRATIONS / "rollback" / "139_tool_invocations_rollback.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_tool_invocations_are_scoped_and_conservatively_deduplicated():
    sql = _read(MIGRATION)

    assert "CREATE TABLE IF NOT EXISTS tool_invocations" in sql
    assert "UNIQUE (task_id, turn_id, tool_call_id)" in sql
    assert "status IN ('running', 'succeeded', 'uncertain')" in sql
    assert "ACTOR_TOOL_INVOCATION_REUSE_MISMATCH" in sql
    assert "'outcome', 'replay'" in sql
    assert "'outcome', 'uncertain'" in sql


def test_begin_and_complete_require_current_fencing_owner():
    sql = _read(MIGRATION)
    begin = sql[sql.index("CREATE OR REPLACE FUNCTION begin_tool_invocation"):]
    complete = sql[sql.index("CREATE OR REPLACE FUNCTION complete_tool_invocation"):]

    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in begin
    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in complete
    assert "NOT (v_task.delivery_context @> '{\"actor\": true}'::JSONB)" in begin
    assert "NOT (v_task.delivery_context @> '{\"actor\": true}'::JSONB)" in complete


def test_rollback_does_not_delete_uncertain_side_effect_facts():
    sql = _read(ROLLBACK)
    assert "status IN ('running', 'uncertain')" in sql
    assert "ACTOR_TOOL_INVOCATIONS_UNSAFE_TO_ROLLBACK" in sql
    assert "DROP TABLE IF EXISTS tool_invocations" in sql
