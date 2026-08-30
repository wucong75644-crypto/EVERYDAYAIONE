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
