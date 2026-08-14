from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import ORG, USER, database
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare, _prepare_legacy_schema, _seed_batch,
)
from tests.test_agent_runtime_media_projection_postgres_external import (
    _install_projection_migration, _projection_connection, _seed_terminal_event,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_06c_agent_runtime_media_slot_release.sql"
ROLLBACK = ROOT / "migrations/rollback/228_06c_agent_runtime_media_slot_release_rollback.sql"
ISOLATION = ROOT / "migrations/228_06a_agent_runtime_media_projection_isolation.sql"


def _install(database_url: str, *, isolation: bool = False) -> None:
    _prepare_legacy_schema(database_url)
    _install_projection_migration(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        if isolation:
            connection.execute(ISOLATION.read_text(encoding="utf-8"))
        connection.execute(MIGRATION.read_text(encoding="utf-8"))


def _project_failed_terminal(database_url: str) -> tuple[UUID, UUID]:
    batch = _seed_batch(database_url, 1, credits=1000)
    assert _prepare(database_url, batch.attempts[0])["outcome"] == "prepared"
    action_id = batch.attempts[0].action_id
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
            UPDATE tasks SET request_params=jsonb_build_object(
                '_task_slot_id','durable-media-slot'
            ) WHERE id=(SELECT task_id FROM agent_runtime_media_action_bindings
                         WHERE action_id=%s)
        """, (action_id,))
    outbox_id = _seed_terminal_event(
        database_url, action_id, "https://provider.example/failed.webp",
        event_type="action.failed",
    )
    with _projection_connection(database_url) as connection:
        [claimed] = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
        applied = connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,'action_progress',NULL)",
            (outbox_id, UUID(claimed["lease_token"])),
        ).fetchone()[0]
        assert applied["outcome"] == "applied"
    return action_id, outbox_id


def test_slot_release_is_transactional_reclaimable_and_fenced(database: str) -> None:
    _install(database)
    action_id, outbox_id = _project_failed_terminal(database)
    with psycopg.connect(database) as connection:
        release = connection.execute("""
            SELECT release_id,status,task_slot_id
              FROM agent_runtime_media_slot_release_outbox
             WHERE source_projection_outbox_id=%s
        """, (outbox_id,)).fetchone()
        assert release[1:] == ("pending", "durable-media-slot")
        assert connection.execute("""
            SELECT task.status='failed' AND binding.credit_state='refunded'
              FROM tasks task JOIN agent_runtime_media_action_bindings binding
                ON binding.task_id=task.id WHERE binding.action_id=%s
        """, (action_id,)).fetchone()[0] is True

    with _projection_connection(database) as connection:
        [first] = connection.execute(
            "SELECT claim_agent_runtime_media_slot_release_v1(1,15)",
        ).fetchone()[0]
    with psycopg.connect(database) as connection:
        connection.execute("""
            UPDATE agent_runtime_media_slot_release_outbox
               SET lease_expires_at=clock_timestamp()-interval '1 second'
             WHERE release_id=%s
        """, (release[0],))
    with _projection_connection(database) as connection:
        [second] = connection.execute(
            "SELECT claim_agent_runtime_media_slot_release_v1(1,15)",
        ).fetchone()[0]
        assert second["lease_token"] != first["lease_token"]
        stale = connection.execute(
            "SELECT ack_agent_runtime_media_slot_release_v1(%s,%s)",
            (release[0], UUID(first["lease_token"])),
        ).fetchone()[0]
        assert stale["outcome"] == "ownership_lost"
        acked = connection.execute(
            "SELECT ack_agent_runtime_media_slot_release_v1(%s,%s)",
            (release[0], UUID(second["lease_token"])),
        ).fetchone()[0]
        assert acked["outcome"] == "acked"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM agent_runtime_media_slot_release_outbox")


def test_slot_release_backoff_dead_and_admin_requeue(database: str) -> None:
    _install(database)
    _project_failed_terminal(database)
    release_id = None
    for attempt in range(1, 9):
        with _projection_connection(database) as connection:
            [claimed] = connection.execute(
                "SELECT claim_agent_runtime_media_slot_release_v1(1,15)",
            ).fetchone()[0]
            release_id = UUID(claimed["release_id"])
            failed = connection.execute(
                "SELECT fail_agent_runtime_media_slot_release_v1(%s,%s,'redis_io')",
                (release_id, UUID(claimed["lease_token"])),
            ).fetchone()[0]
            assert failed["status"] == ("dead" if attempt == 8 else "pending")
        if attempt < 8:
            with psycopg.connect(database) as connection:
                connection.execute("""
                    UPDATE agent_runtime_media_slot_release_outbox
                       SET next_attempt_at=clock_timestamp() WHERE release_id=%s
                """, (release_id,))

    recovery_id = uuid4()
    not_before = datetime.now(UTC) + timedelta(seconds=1)
    admin_url = database.replace("postgres@", "everydayai_runtime_admin@")
    with psycopg.connect(admin_url) as connection:
        connection.execute(
            "SELECT set_config('app.actor_user_id',%s,false)", (str(USER),),
        )
        connection.execute(
            "SELECT set_config('app.org_id',%s,false)", (str(ORG),),
        )
        connection.execute(
            "SELECT set_config('app.access_kind','runtime_admin',false)",
        )
        connection.execute(
            "SELECT set_config('app.request_id','slot-release-recovery',false)",
        )
        result = connection.execute("""
            SELECT requeue_agent_runtime_media_slot_release_v1(
                %s,0,8,%s,'verified Redis outage',%s
            )
        """, (release_id, recovery_id, not_before)).fetchone()[0]
        assert result["outcome"] == "requeued"
    with psycopg.connect(database) as connection:
        assert connection.execute("""
            SELECT status='pending' AND recovery_version=1 AND recovery_count=1
              FROM agent_runtime_media_slot_release_outbox WHERE release_id=%s
        """, (release_id,)).fetchone()[0] is True
        assert connection.execute("""
            SELECT count(*) FROM agent_runtime_media_slot_release_recoveries
             WHERE recovery_request_id=%s
        """, (recovery_id,)).fetchone()[0] == 1


def test_poison_projection_enqueues_slot_release_before_checkpoint(database: str) -> None:
    _install(database, isolation=True)
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    action_id = batch.attempts[0].action_id
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
            UPDATE tasks SET request_params='{"_task_slot_id":"poison-slot"}'
             WHERE id=(SELECT task_id FROM agent_runtime_media_action_bindings
                        WHERE action_id=%s)
        """, (action_id,))
    outbox_id = _seed_terminal_event(
        database, action_id, "https://provider.example/poison.webp",
    )
    with _projection_connection(database) as connection:
        connection.execute(
            "SELECT set_config('app.request_id','poison-worker',false)",
        )
        [claimed] = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
        isolated = connection.execute(
            "SELECT isolate_agent_runtime_media_projection_v1(%s,%s,'poison')",
            (outbox_id, UUID(claimed["lease_token"])),
        ).fetchone()[0]
        assert isolated["outcome"] == "isolated"
    with psycopg.connect(database) as connection:
        assert connection.execute("""
            SELECT count(*) FROM agent_runtime_media_slot_release_outbox
             WHERE source_projection_outbox_id=%s AND status='pending'
        """, (outbox_id,)).fetchone()[0] == 1


def test_slot_release_rollback_reapply_requires_empty_history(database: str) -> None:
    _install(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regclass('agent_runtime_media_slot_release_outbox') IS NULL",
        ).fetchone()[0] is True
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        assert connection.execute("""
            SELECT relrowsecurity AND relforcerowsecurity FROM pg_class
             WHERE oid='agent_runtime_media_slot_release_outbox'::regclass
        """).fetchone()[0] is True
