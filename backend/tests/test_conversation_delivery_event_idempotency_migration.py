"""Conversation Actor 交付事件幂等迁移契约。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "248_conversation_delivery_event_idempotency.sql"
ROLLBACK = MIGRATIONS / "rollback" / "248_conversation_delivery_event_idempotency_rollback.sql"


def test_delivery_event_migration_adds_stable_event_identity():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS event_id UUID" in sql
    assert "idx_conversation_delivery_events_stream_event" in sql
    assert "ON public.conversation_delivery_events(stream_id, event_id)" in sql


def test_delivery_event_migration_reuses_sequence_for_a_retry():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "p_event_id UUID" in sql
    assert "ON CONFLICT (stream_id, event_id) DO NOTHING" in sql
    assert "v_inserted := v_row_count = 1" in sql
    assert "v_seq := v_event.delivery_seq" in sql
    assert "DELIVERY_EVENT_ID_REUSE" in sql


def test_delivery_event_migration_preserves_legacy_rpc_and_has_rollback():
    migration_sql = MIGRATION.read_text(encoding="utf-8")
    rollback_sql = ROLLBACK.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION public.append_conversation_delivery_event(" in migration_sql
    assert "UUID, UUID, TEXT, JSONB, UUID" in rollback_sql
    assert "DROP COLUMN IF EXISTS event_id" in rollback_sql
