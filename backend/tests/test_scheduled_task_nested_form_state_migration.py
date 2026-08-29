"""嵌套定时任务确认表单的数据库状态迁移契约。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "246_scheduled_task_nested_form_state.sql"
ROLLBACK = MIGRATIONS / "rollback/246_scheduled_task_nested_form_state_rollback.sql"


def test_nested_form_state_migration_walks_next_form_recursively() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "WITH RECURSIVE form_nodes(path, block)" in sql
    assert "node.path || 'next_form'" in sql
    assert "block->>'form_id' = p_form_id" in sql
    assert "jsonb_set(v_content, v_path, v_block, FALSE)" in sql
    assert "UPDATE messages SET content = v_content::TEXT" in sql
    assert "FOR UPDATE" in sql


def test_rollback_restores_previous_top_level_transition_contract() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION public.transition_chat_form_state" in rollback
    assert "FOR v_block IN SELECT value FROM jsonb_array_elements(v_content) LOOP" in rollback
    assert "UPDATE messages SET content = v_updated::TEXT" in rollback
