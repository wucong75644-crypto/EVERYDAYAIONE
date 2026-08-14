"""Static contract for migration 227_21 Runtime-owned legacy lifecycle fence."""

from pathlib import Path
import re

from scripts.migration_runner import discover_migrations

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_21_agent_runtime_legacy_lifecycle_fence.sql"
ROLLBACK = ROOT / "migrations/rollback/227_21_agent_runtime_legacy_lifecycle_fence_rollback.sql"


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _body(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?AS \$\$(.*?)\$\$;",
        sql,
        re.DOTALL,
    )
    assert match, name
    return match.group(1)


def test_identity_is_additive_and_has_rollback_pair() -> None:
    assert MIGRATION.is_file()
    assert ROLLBACK.is_file()
    discovered = {
        item.identity: item for item in discover_migrations(ROOT / "migrations")
    }
    assert discovered[MIGRATION.name].rollback_identity == ROLLBACK.name


def test_runtime_tasks_are_excluded_and_direct_settlement_fails_closed() -> None:
    sql = _sql(MIGRATION)
    claim = _body(sql, "worker_claim_orphan_tasks")
    discovery = _body(sql, "worker_discover_legacy_active_tasks")
    for body in (claim, discovery):
        assert "delivery_context ? 'runtime'" in body
        assert "delivery_context -> 'runtime' = 'false'::JSONB" in body
    assert "'delivery_context', claimed.delivery_context" in claim
    for name in ("worker_complete_orphan_task", "worker_fail_orphan_task"):
        body = _body(sql, name)
        assert "ORPHAN_RECOVERY_RUNTIME_TASK_FORBIDDEN" in body
        assert "IS DISTINCT FROM 'false'::JSONB" in body
        assert "ERRCODE = '42501'" in body
    stale = _body(sql, "worker_fail_legacy_stale_task")
    assert "MEDIA_WORKER_RUNTIME_TASK_FORBIDDEN" in stale
    assert "IS DISTINCT FROM 'false'::JSONB" in stale
    assert "ERRCODE = '42501'" in stale


def test_actor_fences_security_definer_search_path_and_worker_acl_remain() -> None:
    sql = _sql(MIGRATION)
    names = (
        "worker_claim_orphan_tasks", "worker_complete_orphan_task",
        "worker_fail_orphan_task", "worker_discover_legacy_active_tasks",
        "worker_fail_legacy_stale_task",
    )
    for name in names:
        declaration = re.search(
            rf"CREATE OR REPLACE FUNCTION {name}\b.*?AS \$\$", sql, re.DOTALL,
        )
        assert declaration
        assert "SECURITY DEFINER" in declaration.group(0)
        assert "SET search_path = pg_catalog, public" in declaration.group(0)
        assert "actor" in _body(sql, name).lower()
    assert "TO everydayai_worker;" in sql
    for role in (
        "PUBLIC", "everydayai_runtime", "everydayai_wecom_runtime",
        "everydayai_agent_runtime_worker", "everydayai_agent_model_gateway",
    ):
        assert role in sql


def test_rollback_guard_precedes_exact_legacy_contract_restore() -> None:
    sql = _sql(ROLLBACK)
    first_replace = sql.index("CREATE OR REPLACE FUNCTION")
    assert sql.index("ROLLBACK_ACTIVE_RUNTIME_TASKS") < first_replace
    assert "status NOT IN ('completed', 'failed', 'cancelled')" in sql[:first_replace]
    assert "delivery_context ? 'runtime'" in sql[:first_replace]
    assert "IS DISTINCT FROM 'false'::JSONB" in sql[:first_replace]
    restored = sql[first_replace:]
    assert "RUNTIME_TASK_FORBIDDEN" not in restored
    assert "'delivery_context', claimed.delivery_context" not in restored
    assert restored.count("CREATE OR REPLACE FUNCTION") == 5
    assert "TO everydayai_worker;" in restored


def test_migration_does_not_expand_into_later_ar18_batches() -> None:
    sql = _sql(MIGRATION).lower()
    for forbidden in (
        "cancel_agent", "scheduler", "submit_media", "callback",
        "production_ready", "feature_flag",
    ):
        assert forbidden not in sql
