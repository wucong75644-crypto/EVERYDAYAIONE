from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "migrations/268_skill_chat_edit.sql"
ROLLBACK = ROOT / "migrations/rollback/268_skill_chat_edit_rollback.sql"


def test_edit_migration_has_a_discoverable_data_preserving_rollback():
    migration = next(item for item in discover_migrations() if item.identity == MIGRATION.name)
    assert migration.rollback_identity == ROLLBACK.name

    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "LOCK TABLE public.skill_chat_proposals IN ACCESS EXCLUSIVE MODE" in rollback
    assert "WHERE operation = 'update'" in rollback
    assert "SKILL_CHAT_EDIT_PROPOSALS_NOT_EMPTY" in rollback
    assert "DROP TABLE" not in rollback
    assert "WHERE package_id IS NOT NULL;" in rollback

