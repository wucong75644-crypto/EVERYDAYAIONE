"""165–180 Worker Control 只读门禁合同。"""

from pathlib import Path
import hashlib


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT / "deploy/preflight/worker-control.sh"
).read_text(encoding="utf-8")
MIGRATIONS = tuple(
    path
    for path in sorted((ROOT / "backend/migrations").glob("*.sql"))
    if 165 <= int(path.name.split("_", 1)[0]) <= 180
)


def test_preflight_is_read_only_and_pins_165_through_180() -> None:
    assert "SET TRANSACTION READ ONLY;" in SCRIPT
    assert "ROLLBACK;" in SCRIPT
    assert "COMMIT;" not in SCRIPT
    for number in range(165, 181):
        migration = next(
            path for path in MIGRATIONS
            if path.name.startswith(f"{number}_")
        )
        checksum = hashlib.sha256(migration.read_bytes()).hexdigest()
        assert migration.name in SCRIPT
        assert checksum in SCRIPT


def test_preflight_accepts_only_complete_worker_migration_waves() -> None:
    assert "expected_migrations[1:6]" in SCRIPT
    assert "expected_migrations[7:16]" in SCRIPT
    assert "worker_migration_count NOT IN (0, 10)" in SCRIPT
    assert "WORKER_CONTROL_MIGRATION_INVALID" in SCRIPT
    assert "WORKER_CONTROL_MIGRATION_PARTIAL" in SCRIPT


def test_preflight_checks_owner_acl_capability_and_force_rls() -> None:
    for table in (
        "error_logs",
        "knowledge_metrics",
        "scheduled_tasks",
        "scheduled_task_runs",
    ):
        assert f"'{table}'" in SCRIPT
    for contract in (
        "WORKER_CONTROL_OWNER_PARTIAL",
        "WORKER_CONTROL_OWNER_REQUIRED_BEFORE_MIGRATIONS",
        "WORKER_CONTROL_CAPABILITY_INCOMPLETE",
        "WORKER_CONTROL_DIRECT_TABLE_ACCESS_PRESENT",
        "WORKER_CONTROL_RUNTIME_ACL_INVALID",
        "WORKER_CONTROL_FORCE_RLS_INCOMPLETE",
    ):
        assert contract in SCRIPT
    assert "'everydayai_worker', procedure.oid, 'EXECUTE'" in SCRIPT
    assert "'public.scheduled_tasks'" in SCRIPT
    assert "'public.scheduled_task_runs'" in SCRIPT
