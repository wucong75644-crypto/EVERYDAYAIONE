from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "125_wecom_actor_enqueue.sql"
ROLLBACK = MIGRATIONS / "rollback" / "125_wecom_actor_enqueue_rollback.sql"


def test_rpc_creates_messages_before_actor_enqueue_and_checks_conflicts():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.index("INSERT INTO messages(") < sql.index(
        "SELECT enqueue_generation_turn("
    )
    assert sql.count("ON CONFLICT (id) DO NOTHING") == 2
    assert "v_input.content IS DISTINCT FROM p_input_content" in sql
    assert "v_conversation.source IS DISTINCT FROM 'wecom'" in sql
    assert "increment_message_count" in sql
    assert "REVOKE ALL ON FUNCTION enqueue_wecom_generation_turn" in sql


def test_rollback_removes_wecom_enqueue_rpc():
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "DROP FUNCTION IF EXISTS enqueue_wecom_generation_turn" in sql
