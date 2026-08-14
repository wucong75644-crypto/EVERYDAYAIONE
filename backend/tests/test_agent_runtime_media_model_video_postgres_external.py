from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import (
    USER, _connect, _settings, database,
)
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare_legacy_schema, _seed_batch, _worker_call,
)
from tests.test_agent_runtime_media_manifest_readback_postgres_external import (
    _apply_runtime_fence_schema, _prepare_asset_schema, _seed_attempt_fence,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_08a_agent_runtime_media_model_video.sql"
ROLLBACK = ROOT / (
    "migrations/rollback/228_08a_agent_runtime_media_model_video_rollback.sql"
)
PREDECESSORS = tuple(ROOT / "migrations" / name for name in (
    "228_05_agent_runtime_media_manifest_readback.sql",
    "228_06_agent_runtime_media_projection.sql",
    "228_06a_agent_runtime_media_projection_isolation.sql",
    "228_06b_agent_runtime_media_projection_readiness.sql",
    "228_06c_agent_runtime_media_slot_release.sql",
    "228_07_agent_runtime_media_controls.sql",
))


def _apply_predecessors(database_url: str) -> None:
    _prepare_legacy_schema(database_url)
    _prepare_asset_schema(database_url)
    _apply_runtime_fence_schema(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for migration in PREDECESSORS:
            connection.execute(migration.read_text(encoding="utf-8"))


def _definition(connection: psycopg.Connection, name: str) -> str:
    definition = connection.execute(
        "SELECT pg_get_functiondef(%s::regprocedure)", (name,),
    ).fetchone()[0]
    return re.sub(r"\s+", "", definition)


def _seed_model_video(database_url: str, channel: str = "web"):
    batch = _seed_batch(database_url, 1, credits=1_000)
    fact = batch.attempts[0]
    arguments = {"prompt": "sunrise over the sea", "duration": 10}
    arguments_hash = hashlib.sha256(json.dumps(
        arguments, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runs SET capability_snapshot=%s WHERE id=(SELECT "
            "run_id FROM agent_actions WHERE id=%s)",
            (Jsonb({"channel": channel}), fact.action_id),
        )
        connection.execute(
            "UPDATE agent_actions SET tool_name='generate_video',arguments=%s,"
            "arguments_hash=%s,policy_snapshot=%s WHERE id=%s",
            (Jsonb(arguments), arguments_hash, Jsonb({
                "source": "runtime_executor_registry",
                "provider_revision": "kie-runtime-media-v1",
                "capability_revision": "v1",
            }), fact.action_id),
        )
        connection.execute(
            "UPDATE agent_policy_receipts SET arguments_hash=%s,"
            "executor_type='runtime_media_generation:generate_video' "
            "WHERE action_id=%s", (arguments_hash, fact.action_id),
        )
        connection.execute(
            "UPDATE agent_action_dispatch_intents SET "
            "executor_type='runtime_media_generation:generate_video' "
            "WHERE action_id=%s", (fact.action_id,),
        )
    _seed_attempt_fence(database_url, fact)
    return batch


def _set_ready(database_url: str) -> None:
    release = "228-08a-test"
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_control SET projection_enabled=TRUE,"
            "release_revision=%s WHERE singleton", (release,),
        )
        connection.execute(
            "UPDATE agent_runtime_media_owner_readiness SET "
            "runtime_enabled=TRUE,provider_probe_passed=TRUE,"
            "production_ready=TRUE WHERE singleton",
        )
    with _connect(database_url, "everydayai_projection_worker") as connection:
        connection.execute("SELECT set_config('app.access_kind','projection',false)")
        connection.execute(
            "SELECT report_agent_runtime_worker_heartbeat("
            "'projection','model-video-test',%s,TRUE,FALSE,'accepting',%s)",
            (release, Jsonb({
                "media_projection_enabled": True,
                "media_provider_probe_passed": True,
            })),
        )
        readiness = connection.execute(
            "SELECT record_agent_runtime_media_projection_readiness_v1("
            "'model-video-test',%s,TRUE,30)", (release,),
        ).fetchone()[0]
    assert readiness["ready"] is True


def test_model_video_apply_rollback_reapply(database: str) -> None:
    _apply_predecessors(database)
    prepare_signature = (
        "prepare_agent_runtime_media_dispatch_v1"
        "(uuid,uuid,text,uuid,bigint,text)"
    )
    read_signature = (
        "read_agent_runtime_media_provider_request_v1"
        "(uuid,uuid,text,uuid,bigint,text)"
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        before = (
            _definition(connection, prepare_signature),
            _definition(connection, read_signature),
        )
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regprocedure(%s)",
            ("_prepare_agent_runtime_model_video_v1(jsonb,text)",),
        ).fetchone()[0] is not None
        connection.execute(ROLLBACK.read_text(encoding="utf-8"))
        assert (
            _definition(connection, prepare_signature),
            _definition(connection, read_signature),
        ) == before
        assert connection.execute(
            "SELECT to_regprocedure(%s)",
            ("_prepare_agent_runtime_model_video_v1(jsonb,text)",),
        ).fetchone()[0] is None
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regprocedure(%s)",
            ("_prepare_agent_runtime_model_video_v1(jsonb,text)",),
        ).fetchone()[0] is not None


@pytest.mark.parametrize("channel", ("web", "wecom"))
def test_model_video_binds_child_task_and_kie_readback(
    database: str, channel: str,
) -> None:
    _apply_predecessors(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
    batch = _seed_model_video(database, channel)
    fact = batch.attempts[0]
    _set_ready(database)

    prepared = _worker_call(
        database, "prepare_agent_runtime_media_dispatch_v1", fact,
    )
    repeated = _worker_call(
        database, "prepare_agent_runtime_media_dispatch_v1", fact,
    )
    readback = _worker_call(
        database, "read_agent_runtime_media_provider_request_v1", fact,
    )

    assert prepared["outcome"] == "prepared"
    assert repeated["outcome"] == "already_prepared"
    assert readback["source"] == "model_loop"
    assert readback["kind"] == "video"
    assert readback["provider_request"] == {
        "model": "sora-2-text-to-video",
        "input": {
            "prompt": "sunrise over the sea", "aspect_ratio": "landscape",
            "n_frames": "10", "remove_watermark": True,
        },
    }
    assert "task_id" not in str(readback["provider_request"])
    with psycopg.connect(database) as connection:
        row = connection.execute("""
          SELECT binding.action_id,binding.task_id,binding.session_id,
                 binding.run_id,binding.model_step_id,binding.media_kind,
                 binding.unit_credits,task.type::text,task.status::text,
                 task.credits_locked,task.credit_transaction_id,
                 task.delivery_context->>'channel',message.content::jsonb,
                 app_user.credits,
                 (SELECT count(*) FROM credit_transactions transaction
                   WHERE transaction.task_id=binding.task_id),
                 (SELECT count(*) FROM credits_history history
                   WHERE history.description='Agent Runtime model video reservation')
            FROM agent_runtime_prepared_media_action_bindings binding
            JOIN tasks task ON task.id=binding.task_id
            JOIN messages message ON message.id=binding.output_message_id
            JOIN users app_user ON app_user.id=binding.user_id
           WHERE binding.action_id=%s
        """, (fact.action_id,)).fetchone()
        security = connection.execute("""
          SELECT class.relrowsecurity,class.relforcerowsecurity,
                 has_table_privilege(
                   'everydayai_agent_runtime_worker',
                   'agent_runtime_prepared_media_action_bindings','SELECT'),
                 has_function_privilege(
                   'everydayai_agent_runtime_worker',
                   '_prepare_agent_runtime_model_video_v1(jsonb,text)','EXECUTE'),
                 has_function_privilege(
                   'everydayai_agent_runtime_worker',
                   'prepare_agent_runtime_media_dispatch_v1'
                   '(uuid,uuid,text,uuid,bigint,text)','EXECUTE')
            FROM pg_class class
           WHERE class.oid='agent_runtime_prepared_media_action_bindings'::regclass
        """).fetchone()
    assert row[:7] == (
        fact.action_id, fact.action_id, row[2], row[3], batch.step_id, "video", 31,
    )
    assert row[7:12] == ("video", "preparing", 31, row[10], channel)
    assert row[10] is not None
    assert row[12][0]["type"] == "video"
    assert row[12][0]["slot_id"] == str(fact.action_id)
    assert row[13:] == (969, 1, 1)
    assert security == (True, True, False, False, True)
    with _connect(database, "everydayai_worker") as connection:
        _settings(connection, "everydayai_worker")
        discovered = connection.execute(
            "SELECT worker_discover_media_tasks(100)",
        ).fetchone()[0]
    assert str(fact.action_id) not in str(discovered)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        with pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState,
            match="AGENT_RUNTIME_228_08A_ACTIVE_MODEL_VIDEO_FACTS",
        ):
            with connection.transaction():
                connection.execute(ROLLBACK.read_text(encoding="utf-8"))


def test_model_image_dispatch_remains_compatible(database: str) -> None:
    _apply_predecessors(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
    batch = _seed_batch(database, 1, credits=1_000)
    fact = batch.attempts[0]
    _seed_attempt_fence(database, fact)
    _set_ready(database)

    prepared = _worker_call(
        database, "prepare_agent_runtime_media_dispatch_v1", fact,
    )
    readback = _worker_call(
        database, "read_agent_runtime_media_provider_request_v1", fact,
    )

    assert prepared["outcome"] == "prepared"
    assert readback["source"] == "model_loop"
    assert readback["kind"] == "image"
    assert readback["provider_request"]["model"] == "gpt-image-2-image-to-image"


@pytest.mark.parametrize("status", ("accepted", "unknown"))
def test_model_video_terminal_ambiguity_is_readback_only(
    database: str, status: str,
) -> None:
    _apply_predecessors(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
    batch = _seed_model_video(database)
    fact = batch.attempts[0]
    _set_ready(database)
    _worker_call(database, "prepare_agent_runtime_media_dispatch_v1", fact)
    reconciliation_token = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_action_attempts SET status=%s,reconciliation_token=%s,"
            "reconciliation_lease_expires_at=%s,state_version=1,"
            "dispatch_phase=%s,external_receipt=%s,ambiguity_evidence=%s,"
            "accepted_at=%s,next_reconcile_at=%s WHERE id=%s",
            (status, reconciliation_token,
             datetime.now(timezone.utc) + timedelta(minutes=5),
             "accepted" if status == "accepted" else "request_started",
             Jsonb({"state": status}), Jsonb({"state": status}),
             datetime.now(timezone.utc) if status == "accepted" else None,
             datetime.now(timezone.utc) + timedelta(minutes=1), fact.attempt_id),
        )
        connection.execute(
            "UPDATE agent_actions SET status=%s,accepted_at=%s,"
            "state_version=state_version+1 WHERE id=%s",
            (status, datetime.now(timezone.utc)
             if status == "accepted" else None, fact.action_id),
        )
    params = (
        fact.action_id, fact.attempt_id, "media-worker", reconciliation_token,
        1, fact.request_hash,
    )
    with _connect(database, "everydayai_agent_runtime_worker") as connection:
        _settings(connection, "everydayai_agent_runtime_worker")
        readback = connection.execute(
            "SELECT read_agent_runtime_media_provider_request_v1("
            "%s,%s,%s,%s,%s,%s)", params,
        ).fetchone()[0]
        assert readback["source"] == "model_loop"
        with pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState,
            match="AGENT_RUNTIME_MODEL_VIDEO_READBACK_ONLY",
        ):
            with connection.transaction():
                connection.execute(
                    "SELECT prepare_agent_runtime_media_dispatch_v1("
                    "%s,%s,%s,%s,%s,%s)", params,
                )
    with psycopg.connect(database) as connection:
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM tasks WHERE id=%s),"
            "(SELECT count(*) FROM credit_transactions WHERE task_id=%s)",
            (fact.action_id, fact.action_id),
        ).fetchone()
    assert counts == (1, 1)
