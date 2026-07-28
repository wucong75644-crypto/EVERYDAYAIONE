"""Static Sandbox Job migration, permissions, state, and rollback contracts."""

from pathlib import Path
import re

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = ROOT / "migrations/222_01_agent_runtime_sandbox_job_foundation.sql"
RPCS = ROOT / "migrations/222_02_agent_runtime_sandbox_job_rpcs.sql"
ROLLBACK_01 = ROOT / (
    "migrations/rollback/"
    "222_01_agent_runtime_sandbox_job_foundation_rollback.sql"
)
ROLLBACK_02 = ROOT / (
    "migrations/rollback/222_02_agent_runtime_sandbox_job_rpcs_rollback.sql"
)


def test_foundation_is_owner_only_force_rls() -> None:
    sql = FOUNDATION.read_text(encoding="utf-8")
    assert "CREATE TABLE agent_sandbox_jobs" in sql
    assert "ALTER TABLE agent_sandbox_jobs ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE agent_sandbox_jobs FORCE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY agent_sandbox_jobs_owner_all" in sql
    assert not re.search(
        r"GRANT\s+(SELECT|INSERT|UPDATE|DELETE|ALL)\s+ON",
        sql,
        re.IGNORECASE,
    )
    assert "everydayai_sandbox_worker" in sql


def test_foundation_freezes_sensitive_and_recovery_contracts() -> None:
    sql = FOUNDATION.read_text(encoding="utf-8")
    for field in (
        "external_idempotency_key", "dispatch_intent_id", "request_hash",
        "code_ref", "code_sha256", "fencing_token", "claim_token",
        "reconciliation_token", "partial_effects_recorded_at",
        "stdout_original_length", "stdout_sha256", "stdout_truncated",
        "stderr_original_length", "stderr_sha256", "stderr_truncated",
    ):
        assert field in sql
    assert "length(p_value) <= 8192" in sql
    assert "interval '24 hours'" in sql
    assert "status <> 'cancelled' OR cancel_confirmed_at IS NOT NULL" in sql
    assert "status <> 'unknown' OR ambiguity_evidence <> '{}'::JSONB" in sql
    assert "stdout_ref" not in sql
    assert "stderr_ref" not in sql


def test_rpc_surface_and_role_grants_are_narrow() -> None:
    sql = RPCS.read_text(encoding="utf-8")
    for function in (
        "create_or_get_sandbox_job",
        "get_sandbox_job",
        "claim_next_sandbox_job",
        "renew_sandbox_job_lease",
        "mark_sandbox_job_started",
        "recover_expired_sandbox_job",
        "request_sandbox_job_cancel",
        "record_sandbox_cancel_signal",
        "finish_sandbox_job",
        "record_sandbox_job_unknown",
        "claim_sandbox_job_reconciliation",
        "renew_sandbox_job_reconciliation",
        "resolve_sandbox_job_reconciliation",
        "record_sandbox_job_cleanup",
    ):
        assert f"CREATE FUNCTION {function}" in sql
        assert f"DROP FUNCTION {function}" in ROLLBACK_02.read_text()
    assert "TO everydayai_sandbox_worker" in sql
    assert "TO everydayai_runtime" in sql
    assert "TO everydayai_worker" not in sql
    assert "SET search_path = pg_catalog, public" in sql


def test_lock_order_and_unsafe_requeue_are_closed() -> None:
    sql = RPCS.read_text(encoding="utf-8")
    helper = sql[sql.index("CREATE FUNCTION _lock_agent_sandbox_job"):]
    positions = [
        helper.index("agent_runtime_sessions"),
        helper.index("agent_runs"),
        helper.index("agent_actions"),
        helper.index("agent_action_attempts"),
        helper.index("agent_action_dispatch_intents"),
        helper.index("agent_sandbox_jobs WHERE id=p_job_id FOR UPDATE"),
    ]
    assert positions == sorted(positions)
    assert "v_job.status='claimed' AND v_job.starting_at IS NULL" in sql
    assert "v_outcome:='unknown'" in sql
    assert "v_intent.recovery_mode<>'reconcile_only'" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "_agent_sandbox_receipt_is_valid(p_receipt)" in sql
    assert "_agent_sandbox_receipt_hash(p_receipt)" in sql
    assert "cleanup_evidence" in sql
    assert "v_job.lease_expires_at<=clock_timestamp()" in sql
    assert "v_job.reconciliation_lease_expires_at<=clock_timestamp()" in sql
    assert "_agent_sandbox_runtime_job" in sql


def test_rollbacks_and_migration_order_are_exact() -> None:
    assert "AGENT_SANDBOX_JOB_ROLLBACK_HAS_FACTS" in ROLLBACK_01.read_text()
    assert ROLLBACK_02.exists()
    discovered = discover_migrations(ROOT / "migrations")
    selected = [
        migration for migration in discovered
        if migration.identity.startswith("222_")
    ]
    assert [migration.identity for migration in selected] == [
        FOUNDATION.name, RPCS.name,
        "222_03_agent_runtime_sandbox_job_recovery_rpcs.sql",
    ]
    assert [migration.rollback_identity for migration in selected] == [
        ROLLBACK_01.name, ROLLBACK_02.name,
        "222_03_agent_runtime_sandbox_job_recovery_rpcs_rollback.sql",
    ]


def test_plpgsql_functions_stay_under_function_limit() -> None:
    pattern = re.compile(
        r"CREATE FUNCTION .*?LANGUAGE plpgsql.*?AS \$\$.*?\$\$;",
        re.IGNORECASE | re.DOTALL,
    )
    for function in pattern.findall(RPCS.read_text(encoding="utf-8")):
        assert len(function.splitlines()) <= 120
