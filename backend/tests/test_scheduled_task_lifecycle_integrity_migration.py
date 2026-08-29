"""定时任务表单状态与修订工作流的数据库契约。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "245_scheduled_task_lifecycle_integrity.sql"
ROLLBACK = MIGRATIONS / "rollback/245_scheduled_task_lifecycle_integrity_rollback.sql"


def test_migration_persists_form_state_and_fences_legacy_tasks() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION public.transition_chat_form_state" in sql
    assert "FOR UPDATE" in sql
    assert "p_expected_status" in sql
    assert "'state_conflict'" in sql
    assert "SELECT content::JSONB INTO v_content" in sql
    assert "UPDATE messages SET content = v_updated::TEXT" in sql
    assert "WHERE execution_policy IS NULL" in sql
    assert "SET status = 'paused'" in sql
    assert "scheduled_tasks_execution_policy_required" in sql


def test_migration_replaces_active_definition_only_after_ready_preflight() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS source_task_id" in sql
    assert "v_draft.status <> 'ready'" in sql
    assert "v_source.status = 'running'" in sql
    assert "'source_running'" in sql
    assert "UPDATE scheduled_tasks" in sql
    assert "'updated'" in sql


def test_rollback_restores_the_previous_confirm_contract_before_dropping_column() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")

    restore = rollback.index("CREATE OR REPLACE FUNCTION public.confirm_scheduled_task_draft")
    drop_column = rollback.index("DROP COLUMN IF EXISTS source_task_id")
    assert restore < drop_column
    assert "v_draft.confirmed_task_id" in rollback[restore:drop_column]
    assert "DROP FUNCTION IF EXISTS public.transition_chat_form_state" in rollback
