"""247 必须使用无歧义的 text[] 路径追加。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "247_scheduled_task_nested_form_state_array_path.sql"
ROLLBACK = MIGRATIONS / "rollback/247_scheduled_task_nested_form_state_array_path_rollback.sql"


def test_recursive_path_uses_array_append_not_an_ambiguous_operator() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "array_append(node.path, 'next_form')" in sql
    assert "node.path || 'next_form'" not in sql
    assert "jsonb_set(v_content, v_path, v_block, FALSE)" in sql


def test_rollback_preserves_the_safe_nested_form_state_function() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")

    assert "array_append(node.path, 'next_form')" in rollback
    assert "node.path || 'next_form'" not in rollback
