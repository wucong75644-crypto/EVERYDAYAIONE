"""Real PostgreSQL Sandbox Job role and rollback contracts (run last)."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
import pytest


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("AR222_TEST_DATABASE_URL", "")


@pytest.fixture(scope="module", autouse=True)
def dedicated_database() -> None:
    if os.getenv("RUN_AR222_DB_TEST") != "1" or not DATABASE_URL:
        pytest.skip("RUN_AR222_DB_TEST=1 and AR222_TEST_DATABASE_URL required")
    if "ar222" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR222 database name required")


def _query(sql: str) -> dict[str, object]:
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as connection:
        return dict(connection.execute(sql).fetchone())


def test_role_matrix_and_force_rls() -> None:
    privileges = _query("""
        SELECT
          has_table_privilege('everydayai_sandbox_worker',
                              'agent_sandbox_jobs','SELECT') AS table_select,
          has_function_privilege('everydayai_sandbox_worker',
              'claim_next_sandbox_job(text,integer)','EXECUTE') AS sandbox_claim,
          has_function_privilege('everydayai_worker',
              'claim_next_sandbox_job(text,integer)','EXECUTE') AS worker_claim,
          has_function_privilege('everydayai_runtime',
              'create_or_get_sandbox_job(uuid,uuid,uuid,bigint,bigint,text,text,text,integer,text,text,text,jsonb,jsonb)',
              'EXECUTE') AS runtime_create,
          has_function_privilege('everydayai_runtime',
              'finish_sandbox_job(uuid,uuid,bigint,bigint,text,text,text,jsonb)',
              'EXECUTE') AS runtime_finish,
          has_function_privilege('everydayai_wecom_runtime',
              'get_sandbox_job(uuid)','EXECUTE') AS wecom_get,
          has_function_privilege('everydayai_sync',
              'get_sandbox_job(uuid)','EXECUTE') AS sync_get,
          has_function_privilege('public',
              'get_sandbox_job(uuid)','EXECUTE') AS public_get,
          relrowsecurity, relforcerowsecurity
        FROM pg_class WHERE oid='agent_sandbox_jobs'::regclass
    """)
    assert privileges == {
        "table_select": False,
        "sandbox_claim": True,
        "worker_claim": False,
        "runtime_create": True,
        "runtime_finish": False,
        "wecom_get": False,
        "sync_get": False,
        "public_get": False,
        "relrowsecurity": True,
        "relforcerowsecurity": True,
    }


def test_manifest_evidence_and_summary_validators_fail_closed() -> None:
    values = _query("""
        SELECT
          _agent_sandbox_manifest_is_valid(
            '{"schema_revision":1,"items":[{"artifact_ref":"artifact:a",
              "content_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              "size_bytes":1,"media_type":"text/plain","secret_value":"x"}]}',
            'input') AS manifest,
          _agent_sandbox_evidence_is_valid(
            '{"kind":"CLEANUP_CONFIRMED","detail":"raw"}') AS evidence,
          _agent_sandbox_summary_is_safe(
            'eyJaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaa') AS summary
    """)
    assert values == {"manifest": False, "evidence": False, "summary": False}


def test_z_clean_reverse_rollback_and_reapply() -> None:
    rollback_01 = ROOT / (
        "migrations/rollback/"
        "222_01_agent_runtime_sandbox_job_foundation_rollback.sql"
    )
    rollback_02 = ROOT / (
        "migrations/rollback/222_02_agent_runtime_sandbox_job_rpcs_rollback.sql"
    )
    rollback_03 = ROOT / (
        "migrations/rollback/"
        "222_03_agent_runtime_sandbox_job_recovery_rpcs_rollback.sql"
    )
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(rollback_03.read_text(encoding="utf-8"))
        connection.execute(rollback_02.read_text(encoding="utf-8"))
        connection.execute(rollback_01.read_text(encoding="utf-8"))
        connection.execute(
            (ROOT / "migrations/222_01_agent_runtime_sandbox_job_foundation.sql")
            .read_text(encoding="utf-8")
        )
        connection.execute(
            (ROOT / "migrations/222_02_agent_runtime_sandbox_job_rpcs.sql")
            .read_text(encoding="utf-8")
        )
        connection.execute(
            (
                ROOT
                / "migrations/222_03_agent_runtime_sandbox_job_recovery_rpcs.sql"
            ).read_text(encoding="utf-8")
        )
