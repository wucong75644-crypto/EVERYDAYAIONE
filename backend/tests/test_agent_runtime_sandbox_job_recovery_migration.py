from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "migrations"
    / "222_03_agent_runtime_sandbox_job_recovery_rpcs.sql"
)
ROLLBACK = (
    ROOT / "migrations" / "rollback"
    / "222_03_agent_runtime_sandbox_job_recovery_rpcs_rollback.sql"
)


def test_recovery_migration_has_scanners_readbacks_and_lock_helper() -> None:
    sql = MIGRATION.read_text()
    assert "CREATE FUNCTION get_sandbox_job_by_binding(" in sql
    assert "CREATE FUNCTION claim_next_recoverable_sandbox_job(" in sql
    assert "CREATE FUNCTION claim_next_sandbox_job_reconciliation(" in sql
    assert "CREATE FUNCTION get_owned_sandbox_job(" in sql
    assert sql.count("_lock_agent_sandbox_job(v_candidate)") == 3
    assert "v_job := _lock_agent_sandbox_job(p_job_id)" in sql
    assert "REVOKE EXECUTE ON FUNCTION get_sandbox_job(UUID)" in sql
    assert "starting_at IS NULL" in sql
    assert "started_at IS NULL" in sql
    assert "fencing_token = fencing_token + 1" in sql
    assert "reconciliation_token = v_token" in sql
    assert "SET search_path = pg_catalog, public" in sql


def test_runtime_and_worker_permissions_are_disjoint() -> None:
    sql = MIGRATION.read_text()
    runtime_grant = sql.split(
        "GRANT EXECUTE ON FUNCTION get_sandbox_job_by_binding", 1
    )[1].split(";", 1)[0]
    worker_grant = sql.split(
        "GRANT EXECUTE ON FUNCTION\n"
        "    claim_next_recoverable_sandbox_job", 1
    )[1].split(";", 1)[0]
    assert "everydayai_runtime" in runtime_grant
    assert "everydayai_sandbox_worker" not in runtime_grant
    assert "everydayai_sandbox_worker" in worker_grant
    assert "everydayai_worker" not in worker_grant


def test_rollback_guards_only_nonterminal_jobs_and_drops_exact_rpcs() -> None:
    sql = ROLLBACK.read_text()
    assert "AGENT_SANDBOX_RECOVERY_ROLLBACK_HAS_ACTIVE_JOBS" in sql
    assert "status NOT IN (" in sql
    for name in (
        "get_sandbox_job_by_binding",
        "claim_next_recoverable_sandbox_job",
        "claim_next_sandbox_job_reconciliation",
        "get_owned_sandbox_job",
    ):
        assert f"DROP FUNCTION {name}" in sql
