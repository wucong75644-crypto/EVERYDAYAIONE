from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar17_postgres_external import _connect, _settings
from tests.test_agent_runtime_ar173_postgres_external import _worker_rpc
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare_legacy_schema, _seed_batch, _worker_call,
)
from tests.agent_runtime_media_manifest_test_support import (
    _apply_runtime_fence_schema,
    _assert_legacy_media_owner,
    _convert_to_prepared_media,
    _create_legacy_prepared_task,
    _function_exists,
    _prepare_asset_schema,
    _projection_migration_sql,
    _seed_attempt_fence,
    _set_media_flags,
    _set_media_ready,
    _set_projection_readiness,
    _submit_prepared_media,
    assert_media_submission_persistence_and_cancel_convergence,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_05_agent_runtime_media_manifest_readback.sql"
ROLLBACK = ROOT / "migrations/rollback/228_05_agent_runtime_media_manifest_readback_rollback.sql"


def test_manifest_readback_apply_rollback_reapply(database: str) -> None:
    _prepare_legacy_schema(database)
    _prepare_asset_schema(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        assert _function_exists(connection, "prepare_agent_runtime_media_dispatch_v1")
        projection_sql = _projection_migration_sql()
        connection.execute(projection_sql)
        assert connection.execute(
            "SELECT to_regprocedure(%s)",
            ("_agent_runtime_media_action_facts_v1(agent_runtime_events)",),
        ).fetchone()[0] is not None
        connection.execute(ROLLBACK.read_text(encoding="utf-8"))
        assert not _function_exists(connection, "prepare_agent_runtime_media_dispatch_v1")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        assert _function_exists(connection, "prepare_agent_runtime_media_dispatch_v1")


def test_provider_request_readback_is_server_normalized(database: str) -> None:
    _prepare_legacy_schema(database)
    _prepare_asset_schema(database)
    _apply_runtime_fence_schema(database)
    batch = _seed_batch(database, 1, credits=1_000)
    _seed_attempt_fence(database, batch.attempts[0])
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        connection.commit()
    _set_media_ready(database)
    prepared = _worker_call(
        database, "prepare_agent_runtime_media_dispatch_v1", batch.attempts[0],
    )
    assert prepared["outcome"] == "prepared"
    result = _worker_call(
        database, "read_agent_runtime_media_provider_request_v1", batch.attempts[0],
    )
    assert result["outcome"] == "found"
    assert result["provider_request"]["model"]
    assert "task_id" not in str(result["provider_request"])
    assert "credit_transaction_id" not in str(result["provider_request"])


def test_prepared_media_ingress_uses_runtime_owner(
    database: str,
) -> None:
    kind, model = "image", "gpt-image-2-image-to-image"
    _prepare_legacy_schema(database)
    _prepare_asset_schema(database)
    _apply_runtime_fence_schema(database)
    batch = _seed_batch(database, 1, credits=1_000)
    prepared_task = _convert_to_prepared_media(
        database, batch.attempts[0], kind=kind, model=model,
    )
    _seed_attempt_fence(database, batch.attempts[0])
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
    _set_media_ready(database)
    receipt = _worker_call(
        database, "prepare_agent_runtime_media_dispatch_v1", batch.attempts[0],
    )
    assert receipt["outcome"] == "prepared"
    request = _worker_call(
        database, "read_agent_runtime_media_provider_request_v1",
        batch.attempts[0],
    )
    assert request["source"] == "media_ingress"
    assert request["kind"] == kind
    assert request["provider_request"]["model"] == model
    assert "task_id" not in str(request["provider_request"])
    with psycopg.connect(database) as connection:
        canonical = connection.execute(
            "SELECT task_id,credit_transaction_id FROM "
            "agent_runtime_media_action_bindings WHERE action_id=%s",
            (batch.attempts[0].action_id,),
        ).fetchone()
        slot = connection.execute(
            "SELECT part FROM messages message CROSS JOIN LATERAL "
            "jsonb_array_elements(message.content::jsonb) part "
            "WHERE message.id=(SELECT output_message_id FROM "
            "agent_runtime_media_action_bindings WHERE action_id=%s) "
            "AND part->>'slot_id'=%s",
            (batch.attempts[0].action_id, str(batch.attempts[0].action_id)),
        ).fetchone()
    assert canonical[0] == prepared_task
    assert canonical[1] is not None
    assert slot[0]["slot_status"] == "pending"
    with _connect(database, "everydayai_worker") as connection:
        _settings(connection, "everydayai_worker")
        [discovered] = connection.execute(
            "SELECT worker_discover_media_tasks(100)",
        ).fetchone()
    assert str(prepared_task) not in str(discovered)


def test_prepared_video_creates_runtime_binding_and_provider_request(
    database: str,
) -> None:
    _prepare_legacy_schema(database)
    _prepare_asset_schema(database)
    _apply_runtime_fence_schema(database)
    batch = _seed_batch(database, 1, credits=1_000)
    prepared_task = _convert_to_prepared_media(
        database, batch.attempts[0], kind="video",
        model="sora-2-image-to-video",
    )
    _seed_attempt_fence(database, batch.attempts[0])
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
    _set_media_ready(database)
    prepared = _worker_call(
        database, "prepare_agent_runtime_media_dispatch_v1",
        batch.attempts[0],
    )
    assert prepared["outcome"] == "prepared"
    request = _worker_call(
        database, "read_agent_runtime_media_provider_request_v1",
        batch.attempts[0],
    )
    assert request["kind"] == "video"
    assert request["provider_request"]["model"] == "sora-2-image-to-video"
    assert request["provider_request"]["input"]["n_frames"] == "10"
    with _connect(database, "everydayai_worker") as connection:
        _settings(connection, "everydayai_worker")
        [discovered] = connection.execute(
            "SELECT worker_discover_media_tasks(100)",
        ).fetchone()
    assert str(prepared_task) not in str(discovered)
    with psycopg.connect(database) as connection:
        bound = connection.execute(
            "SELECT binding.media_kind,task.credit_transaction_id IS NOT NULL,"
            "task.credits_locked,message.content::jsonb->0->>'type' "
            "FROM agent_runtime_prepared_media_action_bindings binding "
            "JOIN tasks task ON task.id=binding.task_id "
            "JOIN messages message ON message.id=binding.output_message_id "
            "WHERE binding.task_id=%s",
            (prepared_task,),
        ).fetchone()
    assert bound == ("video", True, 31, "video")


@pytest.mark.parametrize(("kind", "model"), (
    ("image", "gpt-image-2-image-to-image"),
    ("video", "sora-2-text-to-video"),
))
def test_prepared_media_waits_for_fresh_projection_owner(
    database: str, kind: str, model: str,
) -> None:
    _prepare_legacy_schema(database)
    _prepare_asset_schema(database)
    batch = _seed_batch(database, 1, credits=1_000)
    task_id = _create_legacy_prepared_task(
        database, batch.attempts[0], kind=kind, model=model,
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for predecessor in (
            "227_63_agent_runtime_chat_action_submission.sql",
            "227_67_agent_runtime_chat_action_catalog_fix.sql",
        ):
            connection.execute(
                (ROOT / "migrations" / predecessor).read_text(encoding="utf-8"),
            )
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
    _set_media_flags(database)

    projection_off = _submit_prepared_media(database, task_id, kind)
    assert projection_off["outcome"] == "media_not_ready"
    assert projection_off["runtime_owned"] is False
    _assert_legacy_media_owner(database, task_id, runtime_actions=0)

    _set_projection_readiness(database, ready=True, ttl_seconds=5)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_media_owner_readiness "
            "SET projection_heartbeat_at=statement_timestamp()-interval '6 seconds' "
            "WHERE singleton",
        )
    stale = _submit_prepared_media(database, task_id, kind)
    assert stale["outcome"] == "media_not_ready"
    assert stale["runtime_owned"] is False
    _assert_legacy_media_owner(database, task_id, runtime_actions=0)

    ready_fact = _set_projection_readiness(database, ready=True, ttl_seconds=30)
    assert ready_fact["ready"] is True
    adopted = _submit_prepared_media(database, task_id, kind)
    assert adopted["outcome"] in {"created", "already_exists"}
    assert adopted["runtime_owned"] is True
    _assert_legacy_media_owner(database, task_id, runtime_actions=1, legacy=False)


def test_narrow_rpc_acl_and_rls_are_enforced(database: str) -> None:
    _prepare_legacy_schema(database)
    _prepare_asset_schema(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        rows = connection.execute("""
          SELECT relrowsecurity,relforcerowsecurity FROM pg_class
           WHERE relname IN (
             'agent_runtime_prepared_media_video_pricing_facts',
             'agent_runtime_prepared_media_action_bindings',
             'agent_runtime_media_owner_readiness'
           ) ORDER BY relname
        """).fetchall()
        assert rows == [(True, True), (True, True), (True, True)]
        assert connection.execute(
            "SELECT runtime_enabled,provider_probe_passed,production_ready,"
            "projection_owner_ready,projection_heartbeat_at "
            "FROM agent_runtime_media_owner_readiness WHERE singleton",
        ).fetchone() == (False, False, False, False, None)
        signature = (
            "get_agent_runtime_media_configuration_v1"
            "(uuid,uuid,text,uuid,bigint,text,text)"
        )
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_agent_runtime_worker", signature),
        ).fetchone()[0] is True
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_worker", signature),
        ).fetchone()[0] is False
        for narrow in (
            "record_agent_runtime_media_provider_rejected_v1"
            "(uuid,uuid,text,bigint,jsonb)",
            "record_agent_runtime_media_provider_unknown_v1"
            "(uuid,uuid,bigint,text,jsonb,jsonb,timestamptz)",
            "record_agent_runtime_media_cancel_unproven_v1"
            "(uuid,uuid,bigint,text,jsonb,jsonb,timestamptz)",
            "finalize_agent_runtime_media_after_cancel_v1"
            "(uuid,uuid,uuid,integer,text,text,jsonb,jsonb,text,bigint,"
            "bigint,text,text,text)",
        ):
            assert connection.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')",
                ("everydayai_agent_runtime_worker", narrow),
            ).fetchone()[0] is True
            assert connection.execute(
                "SELECT has_function_privilege(%s,%s,'EXECUTE')",
                ("everydayai_worker", narrow),
            ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_table_privilege(%s,%s,'SELECT')",
            ("everydayai_agent_runtime_worker",
             "agent_runtime_prepared_media_action_bindings"),
        ).fetchone()[0] is False
        projection_readiness = (
            "record_agent_runtime_media_projection_readiness_v1"
            "(text,text,boolean,integer)"
        )
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_projection_worker", projection_readiness),
        ).fetchone()[0] is True
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_agent_runtime_worker", projection_readiness),
        ).fetchone()[0] is False


def test_kie_nano_banana_request_bodies_match_create_task_contract(
    database: str,
) -> None:
    _prepare_legacy_schema(database)
    _prepare_asset_schema(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        generated = connection.execute(
            "SELECT _agent_runtime_kie_provider_request_v1('image',%s,%s)",
            (Jsonb({
                "model": "google/nano-banana", "prompt": "cat",
                "aspect_ratio": "16:9", "output_format": "jpeg",
            }), Jsonb([])),
        ).fetchone()[0]
        edited = connection.execute(
            "SELECT _agent_runtime_kie_provider_request_v1('image',%s,%s)",
            (Jsonb({
                "model": "google/nano-banana-edit", "prompt": "blue",
                "aspect_ratio": "3:2", "output_format": "png",
            }), Jsonb(["https://cdn.example/input.png"])),
        ).fetchone()[0]
        with pytest.raises(psycopg.Error, match="PROVIDER_REQUEST_INVALID"):
            with connection.transaction():
                connection.execute(
                    "SELECT _agent_runtime_kie_provider_request_v1("
                    "'image',%s,%s)",
                    (Jsonb({
                        "model": "google/nano-banana", "prompt": "cat",
                        "aspect_ratio": "16:9", "output_format": "webp",
                    }), Jsonb([])),
                )
    assert generated == {
        "model": "google/nano-banana",
        "input": {
            "prompt": "cat", "aspect_ratio": "16:9",
            "output_format": "jpeg",
        },
    }
    assert edited == {
        "model": "google/nano-banana-edit",
        "input": {
            "prompt": "blue",
            "image_urls": ["https://cdn.example/input.png"],
            "aspect_ratio": "3:2", "output_format": "png",
        },
    }


def test_media_submission_persists_canonical_provider_hash_and_actual_key(
    database: str,
) -> None:
    assert_media_submission_persistence_and_cancel_convergence(database)


def test_rollback_refuses_active_ordinary_media_facts(database: str) -> None:
    _prepare_legacy_schema(database)
    _prepare_asset_schema(database)
    _apply_runtime_fence_schema(database)
    batch = _seed_batch(database, 1, credits=1_000)
    _seed_attempt_fence(database, batch.attempts[0])
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
    _set_media_ready(database)
    _worker_call(
        database, "prepare_agent_runtime_media_dispatch_v1", batch.attempts[0],
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        with pytest.raises(psycopg.Error, match="ACTIVE_MEDIA_FACTS"):
            connection.execute(ROLLBACK.read_text(encoding="utf-8"))


def test_dispatch_unknown_keeps_latest_provider_fact_identity(database: str) -> None:
    _prepare_legacy_schema(database)
    _prepare_asset_schema(database)
    _apply_runtime_fence_schema(database)
    batch = _seed_batch(database, 1, credits=1_000)
    _seed_attempt_fence(database, batch.attempts[0])
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
    _set_media_ready(database)
    _worker_call(
        database, "prepare_agent_runtime_media_dispatch_v1", batch.attempts[0],
    )
    request = _worker_call(
        database, "read_agent_runtime_media_provider_request_v1",
        batch.attempts[0],
    )
    provider_hash = request["provider_request_hash"]
    provider_key = "c" * 64
    submission_id = str(uuid4())
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        version = connection.execute(
            "UPDATE agent_action_attempts SET status='dispatching',"
            "dispatch_phase='request_started' WHERE id=%s RETURNING state_version",
            (batch.attempts[0].attempt_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO agent_runtime_provider_submission_facts("
            "id,attempt_id,action_id,run_id,org_id,user_id,scope_kind,scope_id,"
            "provider,provider_revision,external_idempotency_key,request_hash,"
            "execution_token,state,ambiguity_evidence,state_version) "
            "SELECT %s,attempt.id,attempt.action_id,attempt.run_id,attempt.org_id,"
            "attempt.user_id,session.scope_kind,session.scope_id,'kie',"
            "'kie-runtime-media-v1',%s,attempt.request_hash,attempt.execution_token,"
            "'unknown',%s,1 FROM agent_action_attempts attempt "
            "JOIN agent_runtime_sessions session ON session.id=attempt.session_id "
            "WHERE attempt.id=%s",
            (submission_id, provider_key, Jsonb({"error_code": "timeout"}),
             batch.attempts[0].attempt_id),
        )
    receipt = {
        "provider": "kie", "provider_task_ref": None,
        "status_locator": None, "state": "unknown",
        "evidence": {
            "error_code": "KIE_SUBMIT_RESULT_UNKNOWN",
            "submission_id": submission_id, "state_version": 1,
            "provider_fact_state": "unknown",
            "provider_request_hash": provider_hash,
            "provider_idempotency_key": provider_key,
        },
    }
    persisted = _worker_rpc(
        database, "record_agent_runtime_media_provider_unknown_v1", (
            batch.attempts[0].attempt_id, batch.attempts[0].token, version,
            batch.attempts[0].request_hash, Jsonb(receipt), Jsonb(receipt),
            datetime.now(timezone.utc) + timedelta(minutes=2),
        ),
    )
    assert persisted["outcome"] == "unknown"
    with psycopg.connect(database) as connection:
        stored = connection.execute(
            "SELECT status,provider,provider_task_ref,provider_idempotency_key,"
            "provider_request_hash,external_receipt#>>'{evidence,submission_id}' "
            "FROM agent_action_attempts WHERE id=%s",
            (batch.attempts[0].attempt_id,),
        ).fetchone()
    assert stored == (
        "unknown", None, None, provider_key, provider_hash, submission_id,
    )
