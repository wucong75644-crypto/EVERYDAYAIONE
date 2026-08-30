from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_adapter_migration_has_revision_receipt_and_fixed_rpc():
    sql = (ROOT / "migrations/249_scheduled_task_changeset_adapter.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS revision BIGINT" in sql
    assert "ADD COLUMN IF NOT EXISTS data_scope JSONB" in sql
    assert "CREATE TABLE IF NOT EXISTS public.scheduled_task_change_receipts" in sql
    assert "CREATE OR REPLACE FUNCTION public.commit_scheduled_task_changeset" in sql
    assert "p_base_revision BIGINT" in sql
    assert "FOR UPDATE" in sql
    assert "CREATE TABLE.*change_sets" not in sql


def test_new_entry_does_not_use_legacy_draft_tables():
    source = (ROOT / "services/scheduler/scheduled_task_change_adapter.py").read_text(encoding="utf-8")
    assert "scheduled_task_drafts" not in source
    assert "scheduled_task_preflight_runs" not in source


def test_chat_changeset_reference_migration_is_recursive_and_idempotent():
    sql = (ROOT / "migrations/250_chat_changeset_reference.sql").read_text(encoding="utf-8")
    rollback = (ROOT / "migrations/rollback/250_chat_changeset_reference_rollback.sql").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION public.attach_chat_form_changeset" in sql
    assert "WITH RECURSIVE form_nodes(path, block)" in sql
    assert "change_set_id" in sql
    assert "status', 'submitted'" in sql
    assert "RETURN jsonb_build_object('outcome', 'existing'" in sql
    assert "DROP FUNCTION IF EXISTS public.attach_chat_form_changeset" in rollback
