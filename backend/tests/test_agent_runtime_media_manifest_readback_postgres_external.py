from __future__ import annotations

from pathlib import Path
import subprocess
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar17_postgres_external import _connect, _settings
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare_legacy_schema, _seed_batch, _worker_call,
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


def test_prepared_video_is_runtime_owned_and_fails_closed(database: str) -> None:
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
    with pytest.raises(psycopg.Error, match="PREPARED_VIDEO_UNAVAILABLE"):
        _worker_call(
            database, "prepare_agent_runtime_media_dispatch_v1",
            batch.attempts[0],
        )
    with _connect(database, "everydayai_worker") as connection:
        _settings(connection, "everydayai_worker")
        [discovered] = connection.execute(
            "SELECT worker_discover_media_tasks(100)",
        ).fetchone()
    assert str(prepared_task) not in str(discovered)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT credit_transaction_id FROM tasks WHERE id=%s",
            (prepared_task,),
        ).fetchone()[0] is None


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
             'agent_runtime_prepared_media_action_bindings'
           ) ORDER BY relname
        """).fetchall()
        assert rows == [(True, True), (True, True)]
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
        assert connection.execute(
            "SELECT has_table_privilege(%s,%s,'SELECT')",
            ("everydayai_agent_runtime_worker",
             "agent_runtime_prepared_media_action_bindings"),
        ).fetchone()[0] is False


def _function_exists(connection: psycopg.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT to_regprocedure(%s)",
        (f"{name}(uuid,uuid,text,uuid,bigint,text)",),
    ).fetchone()[0] is not None


def _projection_migration_sql() -> str:
    path = "backend/migrations/228_06_agent_runtime_media_projection.sql"
    result = subprocess.run(
        ["git", "show", f"0cc37ef2:{path}"], check=True,
        capture_output=True, text=True,
    )
    assert "v_data->'image_urls'" in result.stdout
    return result.stdout


def _prepare_asset_schema(database: str) -> None:
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
          CREATE TABLE user_assets(
            id UUID PRIMARY KEY,org_id UUID,storage_scope TEXT,
            storage_owner_key TEXT,storage_provider TEXT,workspace_path TEXT,
            media_type TEXT,status TEXT,download_url TEXT,original_url TEXT
          );
          CREATE TABLE conversation_attachment_refs(
            id UUID PRIMARY KEY,org_id UUID,conversation_id UUID,
            workspace_path TEXT,status TEXT,reference_state TEXT,url TEXT
          );
        """)


def _apply_runtime_fence_schema(database: str) -> None:
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for name in (
            "227_01_agent_runtime_production_closure.sql",
            "227_06_agent_runtime_tenant_kill_control.sql",
            "227_07_agent_runtime_kill_epoch_fence.sql",
        ):
            connection.execute(
                (ROOT / "migrations" / name).read_text(encoding="utf-8"),
            )


def _seed_attempt_fence(database: str, attempt) -> None:
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            """
            INSERT INTO agent_runtime_owner_fences(
              owner_kind,owner_id,org_id,execution_token,tenant_kill_epoch,
              provider_kill_epoch,capability_kill_epoch,provider_revision,
              capability_revision,state_version,
              lease_expires_at,status
            ) SELECT 'attempt',attempt.id,attempt.org_id,attempt.execution_token,
                     0,0,0,action.policy_snapshot->>'provider_revision',
                     action.policy_snapshot->>'capability_revision',0,
                     clock_timestamp()+interval '10 minutes','active'
                FROM agent_action_attempts attempt
                JOIN agent_actions action ON action.id=attempt.action_id
               WHERE attempt.id=%s
            """,
            (attempt.attempt_id,),
        )


def _convert_to_prepared_media(database: str, attempt, *, kind: str, model: str):
    task_id = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        input_id, output_id, conversation_id, org_id, user_id = connection.execute(
            """
            SELECT task.input_message_id,task.assistant_message_id,
                   task.conversation_id,task.org_id,task.user_id
              FROM tasks task JOIN agent_actions action
                ON action.session_id=(
                  SELECT session_id FROM agent_actions WHERE id=%s)
             WHERE task.type='chat' LIMIT 1
            """, (attempt.action_id,),
        ).fetchone()
        request = {
            "prompt": "prepared media",
            "model": model,
            "aspect_ratio": "1:1" if kind == "image" else "landscape",
            "resolution": "1K" if kind == "image" else None,
            "n_frames": "10" if kind == "video" else None,
        }
        connection.execute("""
          INSERT INTO tasks(
            id,user_id,org_id,conversation_id,type,status,model_id,
            request_params,assistant_message_id,input_message_id,
            delivery_context
          ) VALUES(%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s)
        """, (
            task_id, user_id, org_id, conversation_id, kind, model,
            Jsonb(request), output_id, input_id, Jsonb({
                "actor": False, "runtime": True,
                "runtime_owner": "action_loop",
                "runtime_action_id": str(attempt.action_id),
            }),
        ))
        executor = f"runtime_media_generation:generate_{kind}"
        connection.execute("""
          UPDATE agent_actions SET tool_name=%s,
            policy_snapshot=jsonb_build_object(
              'source','media_ingress','task_id',%s,
              'input_message_id',%s,'output_message_id',%s,
              'provider','kie','provider_revision','kie-runtime-media-v1',
              'capability','media.provider.submit',
              'capability_revision','v1')
           WHERE id=%s
        """, (
            f"generate_{kind}", task_id, input_id, output_id,
            attempt.action_id,
        ))
        connection.execute(
            "UPDATE agent_policy_receipts SET executor_type=%s "
            "WHERE action_id=%s",
            (executor, attempt.action_id),
        )
        connection.execute(
            "UPDATE agent_action_dispatch_intents SET executor_type=%s "
            "WHERE action_id=%s",
            (executor, attempt.action_id),
        )
    return task_id
