"""Deployment contracts for the isolated Sync database role."""

from pathlib import Path


ROOT = Path(__file__).parent.parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def test_role_setup_requires_distinct_sync_password() -> None:
    script = _read("deploy/setup-tenant-db-roles.sh")
    assert "EVERYDAYAI_SYNC_PASSWORD" in script
    assert "CREATE ROLE everydayai_sync LOGIN" in script
    assert "ALTER ROLE everydayai_sync" in script
    assert "REVOKE everydayai_owner FROM everydayai_sync" in script


def test_sync_environment_uses_isolated_role_and_kek() -> None:
    template = _read("deploy/env-templates/sync.env.template")
    unit = _read("deploy/everydayai-sync.service")
    assert "everydayai_sync" in template
    assert "EnvironmentFile=/var/www/everydayai/backend/.env.sync" in unit
    assert "EnvironmentFile=/var/www/everydayai/backend/.env.kek" in unit


def test_backend_loads_kek_for_configuration_control_plane() -> None:
    unit = _read("deploy/everydayai-backend.service")
    assert "EnvironmentFile=/var/www/everydayai/backend/.env.runtime" in unit
    assert "EnvironmentFile=/var/www/everydayai/backend/.env.kek" in unit


def test_finalize_requires_all_sync_migrations_and_role_isolation() -> None:
    script = _read("deploy/finalize-tenant-db-role-cutover.sh")
    for number in (181, 182, 183, 184, 185):
        assert f"'{number}_" in script
    assert "'everydayai_sync'" in script


def test_sync_ownership_scripts_are_transactional_and_explicit() -> None:
    transfer = _read("deploy/transfer-sync-domain-ownership.sh")
    rollback = _read("deploy/rollback-sync-domain-ownership.sh")
    assert "BEGIN;" in transfer and "COMMIT;" in transfer
    assert "target_relations CONSTANT TEXT[]" in transfer
    assert "ALTER MATERIALIZED VIEW" in transfer
    assert "BEGIN;" in rollback and "COMMIT;" in rollback
