from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar173_postgres_external import _worker_rpc
from tests.test_agent_runtime_media_real_event_normalization_postgres_external import (
    _event_outbox,
    _insert_provider_fact,
    _provider_request,
)


def finalize_after_cancel(database_url: str, fact, terminal: str) -> UUID:
    provider_hash, provider_key, submission_id = _provider_request(database_url, fact)
    task_ref = f"kie-image-after-cancel-{fact.action_id}"
    _insert_provider_fact(
        database_url, fact, submission_id, provider_key, "submitted",
        task_ref=task_ref,
    )
    accepted = {
        "provider": "kie", "provider_task_ref": task_ref,
        "evidence": {
            "provider_request_hash": provider_hash,
            "provider_idempotency_key": provider_key,
            "submission_id": str(submission_id), "state_version": 0,
            "provider_fact_state": "submitted",
        },
    }
    assert _worker_rpc(
        database_url, "record_agent_runtime_media_provider_submission_v1", (
            fact.attempt_id, fact.token, fact.request_hash, "kie", task_ref,
            "/api/v1/jobs/recordInfo", None, provider_key, provider_hash,
            datetime.now(timezone.utc) + timedelta(minutes=2), Jsonb(accepted),
        ),
    )["outcome"] == "accepted"
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
            UPDATE agent_runs SET status='cancelled',blocking_action_count=0,
                   execution_token=NULL,lease_expires_at=NULL,
                   completed_at=clock_timestamp(),state_version=state_version+1
             WHERE id=(SELECT run_id FROM agent_actions WHERE id=%s)
        """, (fact.action_id,))
    cancel_claim = _worker_rpc(
        database_url, "claim_next_agent_action_reconciliation", (
            "image-cancel", 120, 0,
        ),
    )
    cancel_fact = _worker_rpc(
        database_url, "request_agent_runtime_provider_cancel", (
            submission_id, fact.token, fact.request_hash, 0,
            "runtime_cancel_unproven",
        ),
    )
    unknown = {
        "provider": "kie", "provider_task_ref": task_ref,
        "status_locator": "/api/v1/jobs/recordInfo", "state": "unknown",
        "evidence": {
            "error_code": "CANCEL_UNPROVEN", "cancel_unproven": True,
            "submission_id": str(submission_id),
            "state_version": cancel_fact["state_version"],
            "provider_fact_state": "cancel_requested",
            "provider_request_hash": provider_hash,
            "provider_idempotency_key": provider_key,
        },
    }
    assert _worker_rpc(
        database_url, "record_agent_runtime_media_cancel_unproven_v1", (
            fact.attempt_id, cancel_claim["execution_token"],
            cancel_claim["state_version"], fact.request_hash,
            Jsonb(unknown), Jsonb(unknown),
            datetime.now(timezone.utc) + timedelta(minutes=2),
        ),
    )["outcome"] == "still_unknown"
    readback = _worker_rpc(
        database_url, "record_agent_runtime_provider_readback", (
            submission_id, fact.token, fact.request_hash,
            cancel_fact["state_version"], terminal, "e" * 64, task_ref,
            "/api/v1/jobs/recordInfo", Jsonb({"provider_state": terminal}),
        ),
    )
    final_claim = _worker_rpc(
        database_url, "claim_next_agent_action_reconciliation", (
            "image-cancel-final", 120, 0,
        ),
    )
    receipt = {
        **unknown, "state": terminal,
        "evidence": {
            **unknown["evidence"], "state_version": readback["state_version"],
            "provider_fact_state": (
                "readback_confirmed" if terminal == "completed" else "failed"
            ), "provider_state": terminal,
        },
    }
    source = "https://provider.example/late-image.png"
    result = {
        "status": "success" if terminal == "completed" else "error",
        "summary": f"{terminal} after cancel",
        "data": ({"result_urls": [source]} if terminal == "completed"
                 else {"error_code": "provider_failed"}),
        "artifact_ids": [], "usage": {}, "cost": {},
        "external_receipt": receipt,
        **({} if terminal == "completed" else {"error_code": "provider_failed"}),
    }
    finalized = _worker_rpc(
        database_url, "finalize_agent_runtime_media_after_cancel_v1", (
            fact.attempt_id, None, final_claim["execution_token"],
            final_claim["state_version"], fact.request_hash, terminal,
            Jsonb(receipt), Jsonb(result), None, 0, 0, "credits", "runtime",
            "f" * 64,
        ),
    )
    assert finalized["outcome"] == terminal
    event_type = (
        "action.completed_after_cancel" if terminal == "completed"
        else "action.failed_after_cancel"
    )
    return _event_outbox(database_url, fact.action_id, event_type, "web_runtime")[1]
