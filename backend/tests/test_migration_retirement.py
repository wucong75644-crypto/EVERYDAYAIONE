"""Verify retired production migrations remain non-executable and auditable."""

from scripts.migration_runner import (
    discover_migrations,
    load_retired_ledger_checksums,
    validate_ledger,
)


def test_retired_runtime_migrations_are_not_discoverable() -> None:
    active = {item.identity: item for item in discover_migrations()}
    retired = load_retired_ledger_checksums()

    assert "218_suspended_organization_execution_fence.sql" not in active
    assert retired["218_suspended_organization_execution_fence.sql"] == (
        "3b1e97405f4299aa9db0be4842bc59d044b02b5ab385b325e64591885346c0ca"
    )
    assert any("agent_runtime" in identity for identity in retired)
    assert not set(active) & set(retired)


def test_migration_runner_accepts_applied_retired_history() -> None:
    active = discover_migrations()
    retired = load_retired_ledger_checksums()
    rows = {
        item.identity: {
            "checksum_sha256": item.checksum,
            "status": "applied",
        }
        for item in active
    }
    rows.update(
        {
            identity: {
                "checksum_sha256": checksum,
                "status": "applied",
            }
            for identity, checksum in retired.items()
        }
    )

    assert validate_ledger(active, rows, retired=retired) == []
