from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare,
    _prepare_legacy_schema,
    _seed_batch,
)
from tests.test_agent_runtime_media_projection_postgres_external import (
    _convert_to_prepared_binding,
    _install_projection_migration,
    _projection_connection,
    _seed_terminal_event,
)
from tests.test_agent_runtime_media_projection_review_postgres_external import (
    _seed_initial_run_terminal_event,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_08b_agent_runtime_media_wecom_delivery.sql"
ROLLBACK = ROOT / (
    "migrations/rollback/228_08b_agent_runtime_media_wecom_delivery_rollback.sql"
)
COMPOSITION = tuple(
    ROOT / "migrations" / name
    for name in (
        "228_06a_agent_runtime_media_projection_isolation.sql",
        "228_06b_agent_runtime_media_projection_readiness.sql",
        "228_06c_agent_runtime_media_slot_release.sql",
        "228_07_agent_runtime_media_controls.sql",
    )
)


def _install(database_url: str) -> None:
    _prepare_legacy_schema(database_url)
    _install_projection_migration(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))


def _task_id(database_url: str, action_id: UUID, *, chat: bool) -> UUID:
    column = "chat_task_id" if chat else "task_id"
    with psycopg.connect(database_url) as connection:
        return connection.execute(
            f"SELECT {column} FROM agent_runtime_media_action_bindings "
            "WHERE action_id=%s",
            (action_id,),
        ).fetchone()[0]


def _set_target(
    database_url: str,
    *,
    task_id: UUID,
    outbox_id: UUID,
    channel: str,
) -> dict[str, object]:
    context: dict[str, object] = {
        "actor": False,
        "runtime": True,
        "channel": channel,
        "transport": "app",
        "chatid": "runtime-media-group-1",
    }
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE tasks SET delivery_context=%s WHERE id=%s",
            (Jsonb(context), task_id),
        )
        connection.execute(
            "UPDATE agent_projection_outbox SET projection_kind=%s WHERE id=%s",
            ("wecom" if channel == "wecom" else "web_runtime", outbox_id),
        )
    return context


def _claim(database_url: str, function_name: str) -> list[dict[str, object]]:
    with _projection_connection(database_url) as connection:
        return connection.execute(
            f"SELECT {function_name}(10,15)",
        ).fetchone()[0]


def _apply(
    database_url: str,
    *,
    outbox_id: UUID,
    lease_token: UUID,
    action: str,
) -> dict[str, object]:
    with _projection_connection(database_url) as connection:
        return connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,NULL)",
            (outbox_id, lease_token, action),
        ).fetchone()[0]


def test_wecom_run_terminal_delivery_completed_failed_cancelled_and_replay(
    database: str,
) -> None:
    _install(database)
    actions = {
        "run.completed": "run_completed",
        "run.failed": "run_failed",
        "run.cancelled": "run_cancelled",
    }
    for event_type, projection_action in actions.items():
        batch = _seed_batch(database, 1, credits=1000)
        action_id = batch.attempts[0].action_id
        assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
        outbox_id = _seed_initial_run_terminal_event(
            database, action_id, event_type,
        )
        task_id = _task_id(database, action_id, chat=True)
        target = _set_target(
            database, task_id=task_id, outbox_id=outbox_id, channel="wecom",
        )
        [claimed] = _claim(
            database, "claim_agent_runtime_media_projection_v1",
        )
        lease_token = UUID(str(claimed["lease_token"]))
        applied = _apply(
            database,
            outbox_id=outbox_id,
            lease_token=lease_token,
            action=projection_action,
        )
        assert applied["outcome"] == "applied"
        replay = _apply(
            database,
            outbox_id=outbox_id,
            lease_token=lease_token,
            action=projection_action,
        )
        assert replay["outcome"] == "already_applied"
        with psycopg.connect(database) as connection:
            deliveries = connection.execute("""
                SELECT task_id,channel,delivery_kind,target_context
                  FROM conversation_deliveries WHERE task_id=%s
            """, (task_id,)).fetchall()
            assert deliveries == [(
                task_id, "wecom", "assistant_terminal", target,
            )]
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        with pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState,
            match="AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_HISTORY_PRESENT",
        ):
            connection.execute(ROLLBACK.read_text(encoding="utf-8"))
        connection.rollback()


def test_action_progress_and_web_run_never_create_wecom_terminal_delivery(
    database: str,
) -> None:
    _install(database)
    progress_batch = _seed_batch(database, 1, credits=1000)
    progress_action = progress_batch.attempts[0].action_id
    assert _prepare(database, progress_batch.attempts[0])["outcome"] == "prepared"
    _convert_to_prepared_binding(database, progress_action, "image")
    progress_outbox = _seed_terminal_event(
        database,
        progress_action,
        "https://provider.example/action-progress.webp",
        event_type="action.failed",
    )
    progress_task = _task_id(database, progress_action, chat=False)
    _set_target(
        database,
        task_id=progress_task,
        outbox_id=progress_outbox,
        channel="wecom",
    )
    [claimed] = _claim(database, "claim_agent_runtime_media_projection_v1")
    progress_result = _apply(
        database,
        outbox_id=progress_outbox,
        lease_token=UUID(str(claimed["lease_token"])),
        action="action_progress",
    )
    assert progress_result["outcome"] == "applied"
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM conversation_deliveries WHERE task_id=%s",
            (progress_task,),
        ).fetchone()[0] == 0

    terminal_outbox = _seed_initial_run_terminal_event(
        database, progress_action, "run.failed",
    )
    _set_target(
        database,
        task_id=progress_task,
        outbox_id=terminal_outbox,
        channel="wecom",
    )
    [claimed] = _claim(database, "claim_agent_runtime_media_projection_v1")
    terminal_result = _apply(
        database,
        outbox_id=terminal_outbox,
        lease_token=UUID(str(claimed["lease_token"])),
        action="run_failed",
    )
    assert terminal_result["outcome"] == "applied"
    assert terminal_result["result"]["projection_action"] == "checkpoint_only"

    web_batch = _seed_batch(database, 1, credits=1000)
    web_action = web_batch.attempts[0].action_id
    assert _prepare(database, web_batch.attempts[0])["outcome"] == "prepared"
    web_outbox = _seed_initial_run_terminal_event(
        database, web_action, "run.completed",
    )
    web_task = _task_id(database, web_action, chat=True)
    _set_target(
        database, task_id=web_task, outbox_id=web_outbox, channel="web",
    )
    [claimed] = _claim(database, "claim_agent_runtime_media_projection_v1")
    web_result = _apply(
        database,
        outbox_id=web_outbox,
        lease_token=UUID(str(claimed["lease_token"])),
        action="run_completed",
    )
    assert web_result["outcome"] == "applied"

    with psycopg.connect(database) as connection:
        assert connection.execute("""
            SELECT count(*) FROM conversation_deliveries
             WHERE task_id=%s AND channel='wecom'
               AND delivery_kind='assistant_terminal'
        """, (progress_task,)).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM conversation_deliveries WHERE task_id=%s",
            (web_task,),
        ).fetchone()[0] == 0


def test_wecom_media_has_one_projection_owner_under_concurrent_replay(
    database: str,
) -> None:
    _install(database)
    batch = _seed_batch(database, 1, credits=1000)
    action_id = batch.attempts[0].action_id
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    outbox_id = _seed_initial_run_terminal_event(
        database, action_id, "run.completed",
    )
    task_id = _task_id(database, action_id, chat=True)
    _set_target(
        database, task_id=task_id, outbox_id=outbox_id, channel="wecom",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        runtime = executor.submit(
            _claim, database, "claim_agent_runtime_media_projection_v1",
        )
        compat = executor.submit(
            _claim, database, "claim_agent_compat_projection_outbox",
        )
        runtime_claims, compat_claims = runtime.result(), compat.result()
    runtime_ids = {UUID(str(item["id"])) for item in runtime_claims}
    compat_ids = {UUID(str(item["id"])) for item in compat_claims}
    assert runtime_ids == {outbox_id}
    assert outbox_id not in compat_ids
    assert runtime_ids.isdisjoint(compat_ids)

    lease_token = UUID(str(runtime_claims[0]["lease_token"]))
    barrier = Barrier(2)

    def concurrent_apply() -> str:
        with _projection_connection(database) as connection:
            barrier.wait(timeout=10)
            result = connection.execute(
                "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,NULL)",
                (outbox_id, lease_token, "run_completed"),
            ).fetchone()[0]
            return str(result["outcome"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: concurrent_apply(), range(2)))
    assert sorted(outcomes) == ["already_applied", "applied"]
    with psycopg.connect(database) as connection:
        assert connection.execute("""
            SELECT count(*) FROM conversation_deliveries
             WHERE task_id=%s AND channel='wecom'
               AND delivery_kind='assistant_terminal'
        """, (task_id,)).fetchone()[0] == 1


def test_apply_readback_rollback_reapply_acl_and_invalid_target_fail_closed(
    database: str,
) -> None:
    _install(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        definition = connection.execute("""
            SELECT pg_get_functiondef(
              '_project_agent_runtime_media_wecom_delivery_v1()'::regprocedure
            )
        """).fetchone()[0]
        assert "SECURITY DEFINER" in definition
        assert "SET search_path TO 'pg_catalog', 'public'" in definition
        assert connection.execute("""
            SELECT has_function_privilege(
                'everydayai_projection_worker',
                '_project_agent_runtime_media_wecom_delivery_v1()','EXECUTE'
            ),has_table_privilege(
                'everydayai_projection_worker',
                'conversation_deliveries','INSERT'
            )
        """).fetchone() == (False, False)
        connection.execute(ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute("""
            SELECT to_regprocedure(
                '_project_agent_runtime_media_wecom_delivery_v1()'
            ) IS NULL
        """).fetchone()[0] is True
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        assert connection.execute("""
            SELECT to_regprocedure(
                '_project_agent_runtime_media_wecom_delivery_v1()'
            ) IS NOT NULL
        """).fetchone()[0] is True

    batch = _seed_batch(database, 1, credits=1000)
    action_id = batch.attempts[0].action_id
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    outbox_id = _seed_initial_run_terminal_event(
        database, action_id, "run.failed",
    )
    task_id = _task_id(database, action_id, chat=True)
    _set_target(
        database, task_id=task_id, outbox_id=outbox_id, channel="web",
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_projection_outbox SET projection_kind='wecom' WHERE id=%s",
            (outbox_id,),
        )
    [claimed] = _claim(database, "claim_agent_runtime_media_projection_v1")
    with _projection_connection(database) as connection:
        with pytest.raises(
            psycopg.errors.InsufficientPrivilege,
            match="AGENT_RUNTIME_MEDIA_WECOM_TASK_SCOPE_INVALID",
        ):
            connection.execute(
                "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,NULL)",
                (outbox_id, UUID(str(claimed["lease_token"])), "run_failed"),
            )
        connection.rollback()

    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM conversation_deliveries",
        ).fetchone()[0] == 0
        assert connection.execute("""
            SELECT count(*) FROM agent_runtime_media_projection_results
             WHERE outbox_id=%s
        """, (outbox_id,)).fetchone()[0] == 0
        connection.execute("SET ROLE everydayai_owner")
        with pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState,
            match="AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_IN_FLIGHT",
        ):
            connection.execute(ROLLBACK.read_text(encoding="utf-8"))
        connection.rollback()


def test_real_formal_media_composition_rollback_reapply(database: str) -> None:
    _prepare_legacy_schema(database)
    _install_projection_migration(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for migration in COMPOSITION:
            connection.execute(migration.read_text(encoding="utf-8"))
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        triggers = connection.execute("""
            SELECT tgname FROM pg_trigger
             WHERE tgrelid='agent_runtime_media_projection_results'::regclass
               AND NOT tgisinternal ORDER BY tgname
        """).fetchall()
        assert triggers == [
            ("agent_runtime_media_slot_release_enqueue_v1",),
            ("agent_runtime_media_wecom_delivery_v1",),
        ]
        connection.execute(ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute("""
            SELECT to_regprocedure(
                '_project_agent_runtime_media_wecom_delivery_v1()'
            ) IS NULL
        """).fetchone()[0] is True
        assert connection.execute("""
            SELECT count(*) FROM pg_trigger
             WHERE tgrelid='agent_runtime_media_projection_results'::regclass
               AND tgname='agent_runtime_media_slot_release_enqueue_v1'
               AND NOT tgisinternal
        """).fetchone()[0] == 1
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        assert connection.execute("""
            SELECT to_regprocedure(
                '_project_agent_runtime_media_wecom_delivery_v1()'
            ) IS NOT NULL
        """).fetchone()[0] is True
