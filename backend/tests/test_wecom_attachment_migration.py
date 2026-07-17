from pathlib import Path


SQL = (
    Path(__file__).parent.parent
    / "migrations/129_conversation_attachments.sql"
).read_text()


def test_attachment_table_has_stable_provider_id() -> None:
    assert "CREATE TABLE IF NOT EXISTS conversation_attachment_refs" in SQL
    assert "UNIQUE (org_id, channel, source_provider_id)" in SQL
    assert "url TEXT NOT NULL" in SQL


def test_attachment_state_and_scope_are_constrained() -> None:
    assert "storage_scope IN ('user', 'channel')" in SQL
    assert "'receiving', 'stored', 'ready', 'failed', 'orphan'" in SQL
    assert "'active', 'referenced', 'replaced', 'expired'" in SQL


def test_staging_is_atomic_without_creating_task() -> None:
    assert "CREATE OR REPLACE FUNCTION stage_wecom_attachment" in SQL
    assert "INSERT INTO messages" in SQL
    assert "INSERT INTO tasks" not in SQL
