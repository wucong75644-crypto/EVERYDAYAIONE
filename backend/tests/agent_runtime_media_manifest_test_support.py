from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import _connect, _settings
from tests.test_agent_runtime_ar173_postgres_external import _worker_rpc
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare_legacy_schema,
    _seed_batch,
    _worker_call,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_05_agent_runtime_media_manifest_readback.sql"


@dataclass(frozen=True)
class _ProviderSubmissionScenario:
    attempt: object
    provider_hash: str
    provider_key: str
    submission_id: str
    params: tuple[object, ...]


def _prepare_provider_submission_scenario(
    database: str,
) -> _ProviderSubmissionScenario:
    _prepare_legacy_schema(database)
    _prepare_asset_schema(database)
    _apply_runtime_fence_schema(database)
    batch = _seed_batch(database, 1, credits=1_000)
    attempt = batch.attempts[0]
    _seed_attempt_fence(database, attempt)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
    _set_media_ready(database)
    _worker_call(database, "prepare_agent_runtime_media_dispatch_v1", attempt)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_action_attempts SET status='dispatching',"
            "dispatch_phase='request_started' WHERE id=%s",
            (attempt.attempt_id,),
        )
    request = _worker_call(
        database,
        "read_agent_runtime_media_provider_request_v1",
        attempt,
    )
    provider_hash = request["provider_request_hash"]
    provider_key = "c" * 64
    submission_id = str(uuid4())
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO agent_runtime_provider_submission_facts("
            "id,attempt_id,action_id,run_id,org_id,user_id,scope_kind,scope_id,"
            "provider,provider_revision,external_idempotency_key,request_hash,"
            "execution_token,state,provider_task_ref,status_locator) "
            "SELECT %s,attempt.id,attempt.action_id,attempt.run_id,attempt.org_id,"
            "attempt.user_id,session.scope_kind,session.scope_id,'kie',"
            "'kie-runtime-media-v1',%s,attempt.request_hash,attempt.execution_token,"
            "'submitted','kie-task-1','/api/v1/jobs/recordInfo' "
            "FROM agent_action_attempts attempt JOIN agent_runtime_sessions session "
            "ON session.id=attempt.session_id WHERE attempt.id=%s",
            (submission_id, provider_key, attempt.attempt_id),
        )
    receipt = Jsonb({
        "provider": "kie",
        "provider_task_ref": "kie-task-1",
        "evidence": {
            "provider_request_hash": provider_hash,
            "provider_idempotency_key": provider_key,
            "submission_id": submission_id,
            "state_version": 0,
            "provider_fact_state": "submitted",
        },
    })
    params = (
        attempt.attempt_id, attempt.token, attempt.request_hash, "kie",
        "kie-task-1", "/api/v1/jobs/recordInfo", None, provider_key,
        provider_hash, datetime.now(timezone.utc) + timedelta(minutes=2), receipt,
    )
    return _ProviderSubmissionScenario(
        attempt, provider_hash, provider_key, submission_id, params,
    )


def _assert_provider_submission_receipt(
    database: str,
    scenario: _ProviderSubmissionScenario,
) -> None:
    bad_hash = "d" * 64
    bad_params = list(scenario.params)
    bad_params[8] = bad_hash
    bad_params[10] = Jsonb({
        "provider": "kie",
        "provider_task_ref": "kie-task-1",
        "evidence": {
            "provider_request_hash": bad_hash,
            "provider_idempotency_key": scenario.provider_key,
            "submission_id": scenario.submission_id,
            "state_version": 0,
            "provider_fact_state": "submitted",
        },
    })
    with pytest.raises(psycopg.Error, match="AGENT_PROVIDER_RECEIPT_INVALID"):
        with _connect(database, "everydayai_agent_runtime_worker") as connection:
            _settings(connection, "everydayai_agent_runtime_worker")
            connection.execute(
                "SELECT record_agent_runtime_media_provider_submission_v1("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", tuple(bad_params),
            )
    with _connect(database, "everydayai_agent_runtime_worker") as connection:
        _settings(connection, "everydayai_agent_runtime_worker")
        accepted = connection.execute(
            "SELECT record_agent_runtime_media_provider_submission_v1("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", scenario.params,
        ).fetchone()[0]
    assert accepted["outcome"] == "accepted"
    with psycopg.connect(database) as connection:
        stored = connection.execute(
            "SELECT request_hash,provider_request_hash,provider_idempotency_key,"
            "next_reconcile_at FROM agent_action_attempts WHERE id=%s",
            (scenario.attempt.attempt_id,),
        ).fetchone()
    assert stored[0] == scenario.attempt.request_hash
    assert stored[1] == scenario.provider_hash
    assert stored[1] != stored[0]
    assert stored[2] == scenario.provider_key
    assert stored[3] is not None


def _assert_after_cancel_convergence(
    database: str,
    scenario: _ProviderSubmissionScenario,
) -> None:
    attempt = scenario.attempt
    cancel_receipt = {
        "provider": "kie",
        "provider_task_ref": "kie-task-1",
        "status_locator": "/api/v1/jobs/recordInfo",
        "state": "unknown",
        "evidence": {
            "error_code": "CANCEL_UNPROVEN",
            "cancel_unproven": True,
            "submission_id": scenario.submission_id,
            "state_version": 1,
            "provider_fact_state": "cancel_requested",
            "provider_request_hash": scenario.provider_hash,
            "provider_idempotency_key": scenario.provider_key,
        },
    }
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runs SET status='cancelled',blocking_action_count=0,"
            "execution_token=NULL,lease_expires_at=NULL,completed_at=clock_timestamp(),"
            "state_version=state_version+1 WHERE id=(SELECT run_id FROM "
            "agent_actions WHERE id=%s)",
            (attempt.action_id,),
        )
    cancel_claim = _worker_rpc(
        database, "claim_next_agent_action_reconciliation",
        ("media-cancel", 120, 0),
    )
    assert cancel_claim["operation"] == "cancel"
    cancel_fact = _worker_rpc(
        database, "request_agent_runtime_provider_cancel",
        (scenario.submission_id, attempt.token, attempt.request_hash, 0,
         "runtime_cancel_unproven"),
    )
    cancel_receipt["evidence"]["state_version"] = cancel_fact["state_version"]
    persisted = _worker_rpc(
        database, "record_agent_runtime_media_cancel_unproven_v1",
        (attempt.attempt_id, cancel_claim["execution_token"],
         cancel_claim["state_version"], attempt.request_hash,
         Jsonb(cancel_receipt), Jsonb(cancel_receipt),
         datetime.now(timezone.utc) + timedelta(minutes=2)),
    )
    assert persisted["outcome"] == "still_unknown"
    readback = _worker_rpc(
        database, "record_agent_runtime_provider_readback",
        (scenario.submission_id, attempt.token, attempt.request_hash,
         cancel_fact["state_version"], "completed", "e" * 64, "kie-task-1",
         "/api/v1/jobs/recordInfo", Jsonb({"provider_state": "success"})),
    )
    claim = _worker_rpc(
        database, "claim_next_agent_action_reconciliation",
        ("media-cancel-readback", 120, 0),
    )
    assert claim["operation"] == "cancel"
    terminal_receipt = {
        **cancel_receipt,
        "state": "completed",
        "evidence": {
            **cancel_receipt["evidence"],
            "state_version": readback["state_version"],
            "provider_fact_state": "readback_confirmed",
            "provider_state": "success",
        },
    }
    result = {
        "status": "success",
        "summary": "completed after cancel",
        "data": {"image_urls": ["https://cdn.example/result.png"]},
        "artifact_ids": [], "usage": {}, "cost": {},
        "external_receipt": terminal_receipt,
    }
    finalized = _worker_rpc(
        database, "finalize_agent_runtime_media_after_cancel_v1",
        (attempt.attempt_id, None, claim["execution_token"],
         claim["state_version"], attempt.request_hash, "completed",
         Jsonb(terminal_receipt), Jsonb(result), None, 0, 0,
         "credits", "runtime", "f" * 64),
    )
    assert finalized["outcome"] == "completed"
    with psycopg.connect(database) as connection:
        converged = connection.execute(
            "SELECT action.status,attempt.status,run.status FROM agent_actions action "
            "JOIN agent_action_attempts attempt ON attempt.action_id=action.id "
            "JOIN agent_runs run ON run.id=action.run_id WHERE action.id=%s",
            (attempt.action_id,),
        ).fetchone()
    assert converged == ("completed", "completed", "cancelled")


def assert_media_submission_persistence_and_cancel_convergence(
    database: str,
) -> None:
    scenario = _prepare_provider_submission_scenario(database)
    _assert_provider_submission_receipt(database, scenario)
    _assert_after_cancel_convergence(database, scenario)


def _function_exists(connection: psycopg.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT to_regprocedure(%s)",
        (f"{name}(uuid,uuid,text,uuid,bigint,text)",),
    ).fetchone()[0] is not None


def _projection_migration_sql() -> str:
    path = "backend/migrations/228_06_agent_runtime_media_projection.sql"
    result = subprocess.run(
        ["git", "show", f"8bea7ba6:{path}"], check=True,
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
        connection.execute(
            "DO $$ BEGIN IF to_regrole('everydayai_agent_model_gateway') IS NULL "
            "THEN CREATE ROLE everydayai_agent_model_gateway LOGIN; END IF; END $$",
        )
        connection.execute("SET ROLE everydayai_owner")
        for name in (
            "226_01_agent_runtime_action_provider_reconciliation.sql",
            "227_01_agent_runtime_production_closure.sql",
            "227_06_agent_runtime_tenant_kill_control.sql",
            "227_07_agent_runtime_kill_epoch_fence.sql",
            "227_24_agent_runtime_provider_cancel_handoff.sql",
        ):
            connection.execute(
                (ROOT / "migrations" / name).read_text(encoding="utf-8"),
            )


def _set_media_flags(database: str) -> None:
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_media_owner_readiness SET "
            "runtime_enabled=TRUE,provider_probe_passed=TRUE,"
            "production_ready=TRUE,state_version=state_version+1,"
            "updated_at=clock_timestamp() WHERE singleton",
        )


def _set_projection_readiness(
    database: str, *, ready: bool, ttl_seconds: int,
) -> dict[str, object]:
    with _connect(database, "everydayai_projection_worker") as connection:
        connection.execute(
            "SELECT set_config('app.access_kind','projection',false)",
        )
        return connection.execute(
            "SELECT record_agent_runtime_media_projection_readiness_v1(%s,%s,%s,%s)",
            ("projection-worker-test", "228_06-test", ready, ttl_seconds),
        ).fetchone()[0]


def _set_media_ready(database: str) -> None:
    _set_media_flags(database)
    readiness = _set_projection_readiness(
        database, ready=True, ttl_seconds=30,
    )
    assert readiness["ready"] is True


def _submit_prepared_media(
    database: str, task_id, kind: str,
) -> dict[str, object]:
    with psycopg.connect(database) as owner:
        owner.execute("SET ROLE everydayai_owner")
        task = owner.execute(
            "SELECT conversation_id,org_id,user_id,input_message_id,"
            "assistant_message_id,request_params,model_id FROM tasks WHERE id=%s",
            (task_id,),
        ).fetchone()
    conversation_id, org_id, user_id, input_id, output_id, request, model = task
    params = (
        conversation_id, org_id, user_id, "user", str(user_id), user_id,
        "everydayai-default", "v1", task_id, input_id, output_id, uuid4(),
        f"generate_{kind}", Jsonb(request), model, "kie", "v1",
        "catalog-v1", "policy-v1", f"projection-owner:{task_id}",
    )
    with _connect(database, "everydayai_runtime") as connection:
        _settings(connection, "everydayai_runtime", org=org_id, user=user_id)
        placeholders = ",".join(["%s"] * len(params))
        return connection.execute(
            f"SELECT submit_agent_runtime_media_action_v1({placeholders})",
            params,
        ).fetchone()[0]


def _assert_legacy_media_owner(
    database: str, task_id, *, runtime_actions: int, legacy: bool = True,
) -> None:
    with psycopg.connect(database) as connection:
        action_count, runtime_owned, binding_count = connection.execute(
            "SELECT (SELECT count(*) FROM agent_actions "
            "WHERE policy_snapshot->>'task_id'=%s),"
            "COALESCE((delivery_context->>'runtime')::boolean,false),"
            "(SELECT count(*) FROM agent_runtime_prepared_media_action_bindings "
            "WHERE task_id=tasks.id) "
            "FROM tasks WHERE id=%s",
            (str(task_id), task_id),
        ).fetchone()
    assert action_count == runtime_actions
    assert runtime_owned is (not legacy)
    assert binding_count == (0 if legacy else 1)
    with _connect(database, "everydayai_worker") as connection:
        _settings(connection, "everydayai_worker")
        [discovered] = connection.execute(
            "SELECT worker_discover_media_tasks(100)",
        ).fetchone()
    assert (str(task_id) in str(discovered)) is legacy


def _create_legacy_prepared_task(
    database: str, attempt, *, kind: str, model: str,
):
    task_id = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        input_id, output_id, conversation_id, org_id, user_id = connection.execute(
            "SELECT task.input_message_id,task.assistant_message_id,"
            "task.conversation_id,task.org_id,task.user_id FROM tasks task "
            "JOIN agent_actions action ON action.session_id=(SELECT session_id "
            "FROM agent_actions WHERE id=%s) WHERE task.type='chat' LIMIT 1",
            (attempt.action_id,),
        ).fetchone()
        request = {
            "prompt": "prepared media", "model": model,
            "aspect_ratio": "1:1" if kind == "image" else "landscape",
            "resolution": "1K" if kind == "image" else None,
            "n_frames": "10" if kind == "video" else None,
        }
        connection.execute(
            "INSERT INTO tasks(id,user_id,org_id,conversation_id,type,status,"
            "model_id,request_params,assistant_message_id,input_message_id,"
            "delivery_context) VALUES(%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s)",
            (task_id, user_id, org_id, conversation_id, kind, model,
             Jsonb(request), output_id, input_id,
             Jsonb({"actor": True, "runtime": False})),
        )
    return task_id


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
