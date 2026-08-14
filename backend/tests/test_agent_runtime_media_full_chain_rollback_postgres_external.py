from __future__ import annotations

from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_media_atomic_image_batch_postgres_external import (
    _apply,
    _set_ready,
)
from tests.test_agent_runtime_media_atomic_image_batch_ownership_postgres_external import (
    _candidate,
    _submit_v2,
)
from tests.test_agent_runtime_media_manifest_readback_postgres_external import (
    _prepare_asset_schema,
)
from tests.test_agent_runtime_media_prepared_image_batch_projection_postgres_external import (
    _apply_projection,
)
from tests.test_agent_runtime_media_projection_postgres_external import (
    _seed_terminal_event,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
FORWARD_NAMES = (
    "228_01_agent_runtime_action_hash_canonicalization.sql",
    "228_02_agent_runtime_batch_media_release.sql",
    "228_03_agent_runtime_media_authorization_group.sql",
    "228_04_agent_runtime_media_action_bindings.sql",
    "228_05_agent_runtime_media_manifest_readback.sql",
    "228_06_agent_runtime_media_projection.sql",
    "228_06a_agent_runtime_media_projection_isolation.sql",
    "228_06b_agent_runtime_media_projection_readiness.sql",
    "228_06c_agent_runtime_media_slot_release.sql",
    "228_07_agent_runtime_media_controls.sql",
    "228_08a_agent_runtime_media_model_video.sql",
    "228_08b_agent_runtime_media_wecom_delivery.sql",
    "228_08c_agent_runtime_media_worker_scope.sql",
    "228_08d_agent_runtime_media_atomic_image_batch.sql",
    "228_08e1_agent_runtime_media_model_video_fence.sql",
    "228_08e2_agent_runtime_media_model_video_projection.sql",
    "228_08f1_agent_runtime_media_prepared_image_batch_projection.sql",
    "228_08f2_agent_runtime_media_atomic_image_batch_ownership.sql",
    "228_08g1_agent_runtime_media_real_event_normalization.sql",
    "228_08g2_agent_runtime_media_model_video_wecom_outbox.sql",
)
FORWARDS = tuple(ROOT / "migrations" / name for name in FORWARD_NAMES)
ROLLBACKS = tuple(
    ROOT / "migrations" / "rollback" / f"{path.stem}_rollback.sql"
    for path in reversed(FORWARDS)
)
ROLLBACK_08D = ROOT / (
    "migrations/rollback/228_08d_agent_runtime_media_atomic_image_batch_rollback.sql"
)
V1 = (
    "submit_agent_runtime_media_image_batch_v1"
    "(uuid,uuid,uuid,text,text,uuid,text,text,uuid,uuid,uuid,text,text,text,"
    "text,text,text,jsonb)"
)
V2 = V1.replace("_v1", "_v2")
OWNERSHIP = (
    "_agent_runtime_media_image_batch_ownership_v1"
    "(uuid,uuid,uuid,uuid,uuid,text,text,jsonb)"
)


def _prepare_prerequisites(database_url: str) -> None:
    _prepare_asset_schema(database_url)
    prerequisites = (
        "226_01_agent_runtime_action_provider_reconciliation.sql",
        "227_01_agent_runtime_production_closure.sql",
        "227_03_agent_runtime_tenant_provider_bindings.sql",
        "227_04_agent_runtime_provider_submission_facts.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_24_agent_runtime_provider_cancel_handoff.sql",
        "227_63_agent_runtime_chat_action_submission.sql",
        "227_67_agent_runtime_chat_action_catalog_fix.sql",
    )
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "DO $$ BEGIN IF to_regrole('everydayai_agent_model_gateway') IS NULL "
            "THEN CREATE ROLE everydayai_agent_model_gateway LOGIN; END IF; END $$",
        )
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
            ALTER TABLE tasks
              ADD COLUMN placeholder_created_at TIMESTAMPTZ,
              ADD COLUMN base_context_revision BIGINT,
              ADD COLUMN context_through_message_id UUID,
              ADD COLUMN image_index INTEGER,
              ADD COLUMN batch_id TEXT,
              ADD COLUMN credit_transaction_id UUID REFERENCES credit_transactions(id),
              ADD COLUMN last_polled_at TIMESTAMPTZ
        """)
        for name in prerequisites:
            connection.execute(
                (ROOT / "migrations" / name).read_text(encoding="utf-8"),
            )


def _execute_paths(database_url: str, paths: tuple[Path, ...]) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for path in paths:
            connection.execute(path.read_text(encoding="utf-8"))


def _install_full_chain(database_url: str) -> None:
    _prepare_prerequisites(database_url)
    _execute_paths(database_url, FORWARDS)


def _assert_installed(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        functions = connection.execute(
            "SELECT to_regprocedure(%s),to_regprocedure(%s),to_regprocedure(%s)",
            (V1, V2, OWNERSHIP),
        ).fetchone()
        rls = connection.execute("""
            SELECT count(*),bool_and(relrowsecurity AND relforcerowsecurity)
              FROM pg_class
             WHERE relname=ANY(ARRAY[
               'agent_runtime_media_action_bindings',
               'agent_runtime_media_projection_results',
               'agent_runtime_prepared_image_batch_slots',
               'agent_runtime_media_normalized_projection_inputs_v1',
               'agent_runtime_media_wecom_outbox_facts_v1'
             ])
        """).fetchone()
        acl = connection.execute("""
            SELECT has_function_privilege('everydayai_runtime',%s,'EXECUTE'),
                   has_function_privilege('everydayai_runtime',%s,'EXECUTE'),
                   has_function_privilege('everydayai_worker',%s,'EXECUTE'),
                   has_function_privilege(
                     'everydayai_projection_worker',
                     'apply_agent_runtime_media_projection_v1(uuid,uuid,text,jsonb)',
                     'EXECUTE'
                   ),
                   has_table_privilege(
                     'everydayai_projection_worker',
                     'agent_runtime_media_normalized_projection_inputs_v1','SELECT'
                   )
        """, (V1, V2, V2)).fetchone()
    assert all(value is not None for value in functions)
    assert rls == (5, True)
    assert acl == (False, True, False, True, False)


def _assert_rolled_back(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        functions = connection.execute(
            "SELECT to_regprocedure(%s),to_regprocedure(%s),to_regprocedure(%s)",
            (V1, V2, OWNERSHIP),
        ).fetchone()
        tables = connection.execute("""
            SELECT to_regclass('agent_runtime_media_action_bindings'),
                   to_regclass('agent_runtime_prepared_media_action_bindings'),
                   to_regclass('agent_runtime_prepared_image_batch_slots'),
                   to_regclass('agent_runtime_media_normalized_projection_inputs_v1'),
                   to_regclass('agent_runtime_media_wecom_outbox_facts_v1')
        """).fetchone()
        release_count = connection.execute(
            "SELECT count(*) FROM agent_runtime_definition_facts "
            "WHERE definition_revision='v7'",
        ).fetchone()[0]
        group_column = connection.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='agent_interactions' "
            "AND column_name='confirmation_group_hash'",
        ).fetchone()[0]
    assert functions == (None, None, None)
    assert tables == (None, None, None, None, None)
    assert release_count == 0
    assert group_column == 0


def test_full_chain_wrong_order_reverse_reapply_and_readback(database: str) -> None:
    _install_full_chain(database)
    _assert_installed(database)

    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
        match="ROLLBACK_08F2_REQUIRED",
    ):
        _apply(database, ROLLBACK_08D)
    with psycopg.connect(database) as connection:
        assert all(connection.execute(
            "SELECT to_regprocedure(%s)", (signature,),
        ).fetchone()[0] is not None for signature in (V1, V2, OWNERSHIP))

    _execute_paths(database, ROLLBACKS)
    _assert_rolled_back(database)
    _execute_paths(database, FORWARDS)
    _assert_installed(database)


def test_active_batch_blocks_strict_reverse_until_drained(database: str) -> None:
    _install_full_chain(database)
    _set_ready(database)
    task_ids, batch_id, anchor = _candidate(database)
    assert _submit_v2(database, batch_id, anchor)["runtime_owned"] is True

    _execute_paths(database, ROLLBACKS[:3])
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
        match="PROJECTION_NOT_DRAINED",
    ):
        _apply(database, ROLLBACKS[3])

    with psycopg.connect(database) as connection:
        action_rows = connection.execute(
            "SELECT task.image_index,binding.action_id "
            "FROM tasks task JOIN agent_runtime_prepared_media_action_bindings binding "
            "ON binding.task_id=task.id WHERE task.id=ANY(%s) ORDER BY task.image_index",
            (task_ids,),
        ).fetchall()
    actions = {index: UUID(str(action_id)) for index, action_id in action_rows}
    completed = _seed_terminal_event(
        database, actions[0], "https://provider.example/first.png",
    )
    _apply_projection(database, completed, {
        "type": "image",
        "url": "https://cdn.example/first.png",
        "source_url": "https://provider.example/first.png",
    })
    failed = _seed_terminal_event(
        database, actions[1], "https://provider.example/failed.png",
        event_type="action.failed",
    )
    _apply_projection(database, failed)

    _apply(database, ROLLBACKS[3])
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regclass('agent_runtime_prepared_image_batch_slots')",
        ).fetchone()[0] is None
