"""Real PostgreSQL Sandbox Job late-write and queued-cancel contracts."""

from __future__ import annotations

import json
import os

import pytest

from tests.test_agent_runtime_sandbox_job_postgres_external import (
    _claim,
    _create,
    _decoded,
    _execute,
    _receipt,
    _receipt_hash,
    _seed_dispatch,
    _worker_rpc,
)

pytestmark = pytest.mark.external


@pytest.fixture(scope="module", autouse=True)
def dedicated_database() -> None:
    url = os.getenv("AR222_TEST_DATABASE_URL", "")
    if os.getenv("RUN_AR222_DB_TEST") != "1" or "ar222" not in url.lower():
        pytest.skip("dedicated AR222 database required")


def test_queued_cancel_is_terminal_and_runtime_readback_hides_worker_tokens() -> None:
    ids = _seed_dispatch()
    job = _create(ids)["job"]
    cancelled = _decoded(_execute(
        "SELECT request_sandbox_job_cancel(%s,%s) AS value",
        (job["id"], job["state_version"]),
        role="everydayai_runtime", user_id=str(ids["user"]),
    )[0]["value"])
    assert cancelled["outcome"] == "cancelled"
    assert cancelled["job"]["terminal_reason"] == "CANCELLED_BEFORE_START"
    cancel_receipt = _receipt()
    cancel_receipt["execution_outcome"] = "interrupted"
    assert cancelled["job"]["receipt_hash"] == _receipt_hash(cancel_receipt)
    assert cancelled["job"]["receipt_hash"] != cancelled["job"]["request_hash"]
    assert not {
        "claim_token", "reconciliation_token", "claim_worker_id",
    } & cancelled["job"].keys()
    assert _worker_rpc(
        "SELECT claim_next_sandbox_job('sandbox-2',60) AS value", (),
    )["outcome"] == "not_found"


def test_expired_execution_and_reconciliation_leases_reject_late_writes() -> None:
    ids = _seed_dispatch()
    job = _create(ids)["job"]
    claimed = _claim()
    late_receipt = _receipt()
    _execute(
        "SET ROLE everydayai_owner; UPDATE agent_sandbox_jobs "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE id=%s; RESET ROLE",
        (job["id"],),
    )
    assert _worker_rpc(
        "SELECT finish_sandbox_job(%s,%s,%s,%s,'failed',"
        "'EXECUTION_FAILED',%s,%s::jsonb) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"], _receipt_hash(late_receipt),
            json.dumps(late_receipt),
        ),
    )["outcome"] == "invalid_transition"
    _execute(
        "SET ROLE everydayai_owner; UPDATE agent_sandbox_jobs SET "
        "status='unknown',ambiguity_evidence='{\"kind\":\"LEASE_EXPIRED\"}',"
        "claim_worker_id=NULL,claim_token=NULL,lease_expires_at=NULL,"
        "state_version=state_version+1 WHERE id=%s; RESET ROLE",
        (job["id"],),
    )
    current = _execute(
        "SELECT state_version FROM agent_sandbox_jobs WHERE id=%s",
        (job["id"],),
    )[0]
    reconciled = _worker_rpc(
        "SELECT claim_sandbox_job_reconciliation(%s,%s,'scanner-1',60) AS value",
        (job["id"], current["state_version"]),
    )["job"]
    malformed_receipt: dict[str, object] = {}
    malformed = _worker_rpc(
        "SELECT resolve_sandbox_job_reconciliation(%s,%s,%s,'failed',"
        "'EXECUTION_FAILED',%s,'{}'::jsonb) AS value",
        (
            job["id"], reconciled["reconciliation_token"],
            reconciled["state_version"], _receipt_hash(malformed_receipt),
        ),
    )
    assert malformed["outcome"] == "terminal_guard_failed"
    _execute(
        "SET ROLE everydayai_owner; UPDATE agent_sandbox_jobs SET "
        "reconciliation_lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE id=%s; RESET ROLE",
        (job["id"],),
    )
    cleanup = _worker_rpc(
        "SELECT record_sandbox_job_cleanup(%s,%s,%s,'completed',"
        "%s::jsonb) AS value",
        (
            job["id"], reconciled["reconciliation_token"],
            reconciled["state_version"],
            json.dumps({"kind": "CLEANUP_CONFIRMED"}),
        ),
    )
    assert cleanup["outcome"] == "invalid_transition"


def test_partial_reconciliation_requires_persisted_cleanup_proof() -> None:
    ids = _seed_dispatch()
    job = _create(ids)["job"]
    claimed = _claim()
    receipt = _receipt(partial=True)
    unknown = _worker_rpc(
        "SELECT record_sandbox_job_unknown(%s,%s,%s,%s,%s::jsonb,"
        "%s::jsonb,NULL) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"],
            json.dumps({"kind": "PARTIAL_OUTPUT_UNPROVEN"}),
            json.dumps(receipt["partial_effects"]),
        ),
    )["job"]
    reconciled = _worker_rpc(
        "SELECT claim_sandbox_job_reconciliation(%s,%s,'scanner-2',60) AS value",
        (job["id"], unknown["state_version"]),
    )["job"]
    reconciled = _worker_rpc(
        "SELECT renew_sandbox_job_reconciliation(%s,%s,%s,60) AS value",
        (
            job["id"], reconciled["reconciliation_token"],
            reconciled["state_version"],
        ),
    )["job"]
    direct = _worker_rpc(
        "SELECT resolve_sandbox_job_reconciliation(%s,%s,%s,'failed',"
        "'EXECUTION_FAILED',%s,%s::jsonb) AS value",
        (
            job["id"], reconciled["reconciliation_token"],
            reconciled["state_version"], _receipt_hash(receipt),
            json.dumps(receipt),
        ),
    )
    assert direct["outcome"] == "terminal_guard_failed"
    mismatched = _receipt()
    mismatch = _worker_rpc(
        "SELECT resolve_sandbox_job_reconciliation(%s,%s,%s,'failed',"
        "'EXECUTION_FAILED',%s,%s::jsonb) AS value",
        (
            job["id"], reconciled["reconciliation_token"],
            reconciled["state_version"], _receipt_hash(mismatched),
            json.dumps(mismatched),
        ),
    )
    assert mismatch["outcome"] == "terminal_guard_failed"
    cleaned = _worker_rpc(
        "SELECT record_sandbox_job_cleanup(%s,%s,%s,'completed',"
        "%s::jsonb) AS value",
        (
            job["id"], reconciled["reconciliation_token"],
            reconciled["state_version"],
            json.dumps({"kind": "CLEANUP_CONFIRMED"}),
        ),
    )["job"]
    wrong_proof = dict(receipt)
    wrong_proof["cleanup_evidence"] = {"kind": "DIFFERENT_CLEANUP_PROOF"}
    rejected_proof = _worker_rpc(
        "SELECT resolve_sandbox_job_reconciliation(%s,%s,%s,'failed',"
        "'EXECUTION_FAILED',%s,%s::jsonb) AS value",
        (
            job["id"], reconciled["reconciliation_token"],
            cleaned["state_version"], _receipt_hash(wrong_proof),
            json.dumps(wrong_proof),
        ),
    )
    assert rejected_proof["outcome"] == "terminal_guard_failed"
    resolved = _worker_rpc(
        "SELECT resolve_sandbox_job_reconciliation(%s,%s,%s,'failed',"
        "'EXECUTION_FAILED',%s,%s::jsonb) AS value",
        (
            job["id"], reconciled["reconciliation_token"],
            cleaned["state_version"], _receipt_hash(receipt),
            json.dumps(receipt),
        ),
    )
    assert resolved["outcome"] == "failed"
    assert resolved["job"]["partial_effects"] == receipt["partial_effects"]
    assert resolved["job"]["cleanup_evidence"]["kind"] == "CLEANUP_CONFIRMED"
    persisted_hash = _execute(
        """
        SELECT _agent_sandbox_receipt_hash(jsonb_build_object(
          'receipt_revision',receipt_revision,
          'execution_outcome',execution_outcome,
          'stdout_summary',stdout_summary,
          'stdout_original_length',stdout_original_length,
          'stdout_sha256',stdout_sha256,'stdout_truncated',stdout_truncated,
          'stderr_summary',stderr_summary,
          'stderr_original_length',stderr_original_length,
          'stderr_sha256',stderr_sha256,'stderr_truncated',stderr_truncated,
          'artifact_manifest',artifact_manifest,'partial_effects',partial_effects,
          'materialization_status',materialization_status,
          'cleanup_status',cleanup_status,'cleanup_evidence',cleanup_evidence
        )) AS value FROM agent_sandbox_jobs WHERE id=%s
        """,
        (job["id"],),
    )[0]["value"]
    assert persisted_hash == resolved["job"]["receipt_hash"]


def test_receipt_hash_mismatch_is_rejected_before_terminal_write() -> None:
    ids = _seed_dispatch()
    job = _create(ids)["job"]
    claimed = _claim()
    receipt = _receipt()
    result = _worker_rpc(
        "SELECT finish_sandbox_job(%s,%s,%s,%s,'failed',"
        "'EXECUTION_FAILED',%s,%s::jsonb) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"], "0" * 64, json.dumps(receipt),
        ),
    )
    assert result["outcome"] == "receipt_hash_conflict"
