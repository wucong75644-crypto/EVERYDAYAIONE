"""Real PostgreSQL Sandbox Job reconciliation and scope contracts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_sandbox_job_postgres_external import (
    _claim,
    _create,
    _execute,
    _receipt,
    _receipt_hash,
    _seed_dispatch,
    _worker_rpc,
    dedicated_database,
)


pytestmark = pytest.mark.external
__all__ = ["dedicated_database"]


def test_unknown_partial_contract_reconcile_and_sensitive_rejection() -> None:
    ids = _seed_dispatch()
    job = _create(ids)["job"]
    claimed = _claim()
    partial = _receipt(partial=True)["partial_effects"]
    unknown = _worker_rpc(
        "SELECT record_sandbox_job_unknown(%s,%s,%s,%s,%s::jsonb,"
        "%s::jsonb,clock_timestamp()+interval '3 days') AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"], json.dumps({"kind": "OUTPUT_UNPROVEN"}),
            json.dumps(partial),
        ),
    )
    assert unknown["outcome"] == "unknown"
    assert unknown["job"]["cleanup_status"] == "pending"
    assert (
        datetime.fromisoformat(unknown["job"]["cleanup_deadline_at"])
        <= datetime.fromisoformat(unknown["job"]["partial_effects_recorded_at"])
        + timedelta(hours=24)
    )
    reconciled = _worker_rpc(
        "SELECT claim_sandbox_job_reconciliation(%s,%s,'scanner-1',60) AS value",
        (job["id"], unknown["job"]["state_version"]),
    )
    still = _worker_rpc(
        "SELECT resolve_sandbox_job_reconciliation(%s,%s,%s,'still_unknown',"
        "'STILL_UNKNOWN',%s,%s::jsonb) AS value",
        (
            job["id"], reconciled["job"]["reconciliation_token"],
            reconciled["job"]["state_version"], "3" * 64,
            json.dumps(_receipt()),
        ),
    )
    assert still["outcome"] == "still_unknown"

    ids = _seed_dispatch()
    job = _create(ids)["job"]
    claimed = _claim()
    unsafe = _receipt()
    unsafe["stderr_summary"] = "/Users/example/secret.py"
    rejected = _worker_rpc(
        "SELECT finish_sandbox_job(%s,%s,%s,%s,'failed','EXECUTION_FAILED',"
        "%s,%s::jsonb) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"], _receipt_hash(unsafe), json.dumps(unsafe),
        ),
    )
    assert rejected["outcome"] == "malformed_receipt"
    unsafe["stderr_summary"] = "x" * 8193
    rejected = _worker_rpc(
        "SELECT finish_sandbox_job(%s,%s,%s,%s,'failed','EXECUTION_FAILED',"
        "%s,%s::jsonb) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"], _receipt_hash(unsafe), json.dumps(unsafe),
        ),
    )
    assert rejected["outcome"] == "malformed_receipt"


def test_runtime_scope_mismatch_cannot_read_or_reuse_job() -> None:
    ids = _seed_dispatch()
    job = _create(ids)["job"]
    wrong_user = str(uuid4())
    with pytest.raises(psycopg.Error, match="AGENT_SANDBOX_SCOPE_MISMATCH"):
        _execute(
            "SELECT get_sandbox_job(%s) AS value", (job["id"],),
            role="everydayai_runtime", user_id=wrong_user,
        )
    with pytest.raises(psycopg.Error, match="AGENT_SANDBOX_SCOPE_MISMATCH"):
        _execute(
            "SELECT create_or_get_sandbox_job("
            "%s,%s,%s,0,0,%s,%s,'sandbox.python',1,'python-v1',"
            "%s,%s,%s::jsonb,%s::jsonb) AS value",
            (
                ids["action"], ids["attempt"], ids["intent"],
                ids["external_key"], "b" * 64,
                f"ws-scope:user:{ids['user']}", "d" * 64,
                json.dumps({"schema_revision": 1, "items": []}),
                json.dumps({"timeout_seconds": 120}),
            ),
            role="everydayai_runtime", user_id=wrong_user,
        )
