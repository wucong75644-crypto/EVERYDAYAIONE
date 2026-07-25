from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TRANSFER = (
    ROOT / "deploy/transfer-memory-runtime-ownership.sh"
).read_text()
ROLLBACK = (
    ROOT / "deploy/rollback-memory-runtime-ownership.sh"
).read_text()

TABLES = (
    "memory_pipeline_state",
    "memory_session_logs",
    "memory_consolidation_runs",
    "memory_atoms",
)
FUNCTIONS = (
    "commit_memory_session_flush",
    "commit_memory_consolidation",
)


def test_transfer_has_preflight_and_atomic_owner_change() -> None:
    assert "BEGIN;" in TRANSFER
    assert "COMMIT;" in TRANSFER
    assert "MEMORY_RUNTIME_REQUIRED_ROLE_MISSING" in TRANSFER
    assert "MEMORY_RUNTIME_TABLE_MISSING" in TRANSFER
    assert "MEMORY_RUNTIME_OWNER_UNEXPECTED" in TRANSFER
    for table in TABLES:
        assert (
            f"ALTER TABLE public.{table} OWNER TO everydayai_owner"
            in TRANSFER
        )
    for function in FUNCTIONS:
        assert (
            f"ALTER FUNCTION public.{function}(" in TRANSFER
        )


def test_rollback_requires_guard_and_force_rls_off() -> None:
    assert "ALLOW_TENANT_DB_OWNERSHIP_ROLLBACK" in ROLLBACK
    assert "DISABLE_FORCE_RLS_BEFORE_OWNERSHIP_ROLLBACK" in ROLLBACK
    for table in TABLES:
        assert (
            f"ALTER TABLE public.{table} OWNER TO ${{legacy_owner}}"
            in ROLLBACK
        )
    for function in FUNCTIONS:
        assert f"ALTER FUNCTION public.{function}(" in ROLLBACK
