"""Migration 161 behavior against an explicitly isolated PostgreSQL database."""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from services.configuration.legacy_import import (
    LegacyImportItem,
    LegacyImportPlan,
)
from services.configuration.legacy_import_executor import (
    LegacyImportExecutionError,
    apply_legacy_import,
    required_confirmation,
)


pytestmark = pytest.mark.external


def _urls() -> tuple[str, str]:
    admin_url = os.getenv("CONFIG_IMPORT_TEST_ADMIN_URL")
    migrator_url = os.getenv("CONFIG_IMPORT_TEST_MIGRATOR_URL")
    if not admin_url or not migrator_url:
        pytest.skip("isolated configuration import database URLs required")
    return admin_url, migrator_url


def _plan(import_id: str, org_id: str) -> LegacyImportPlan:
    return LegacyImportPlan(
        import_id,
        (
            LegacyImportItem(
                org_id,
                "v1",
                "wecom.corp_id",
                "corp-real-test",
                None,
            ),
            LegacyImportItem(
                org_id,
                "v1",
                "wecom.oauth_agent_secret",
                None,
                {
                    "payload_ciphertext": "ciphertext",
                    "wrapped_dek": "wrapped-dek",
                    "kek_version": "test-v1",
                },
            ),
        ),
    )


def test_real_migrator_gate_atomic_import_and_audit() -> None:
    admin_url, migrator_url = _urls()
    org_id = str(uuid4())
    import_id = str(uuid4())
    plan = _plan(import_id, org_id)
    with psycopg.connect(admin_url) as admin:
        admin.execute(
            "INSERT INTO organizations(id) VALUES (%s)",
            (org_id,),
        )
        admin.commit()
    try:
        with psycopg.connect(migrator_url) as migrator:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(
                    "SELECT import_legacy_configuration_batch(%s, %s)",
                    (str(uuid4()), []),
                )
        with psycopg.connect(migrator_url) as migrator:
            result = apply_legacy_import(
                migrator,
                plan,
                confirmation=required_confirmation(import_id),
            )
        assert dict(result) == {
            "import_id": import_id,
            "imported_count": 2,
            "version": 1,
        }

        with psycopg.connect(admin_url) as admin:
            counts = admin.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM configuration_entries "
                "WHERE org_id = %s), "
                "(SELECT COUNT(*) FROM secret_records WHERE org_id = %s), "
                "(SELECT COUNT(*) FROM configuration_import_audit_log "
                "WHERE org_id = %s)",
                (org_id, org_id, org_id),
            ).fetchone()
        assert counts == (2, 1, 2)

        second_plan = _plan(str(uuid4()), org_id)
        with psycopg.connect(migrator_url) as migrator:
            with pytest.raises(
                LegacyImportExecutionError,
                match="LEGACY_IMPORT_DATABASE_FAILED",
            ):
                apply_legacy_import(
                    migrator,
                    second_plan,
                    confirmation=required_confirmation(second_plan.import_id),
                )
        with psycopg.connect(admin_url) as admin:
            unchanged = admin.execute(
                "SELECT COUNT(*) FROM configuration_import_audit_log "
                "WHERE org_id = %s",
                (org_id,),
            ).fetchone()[0]
        assert unchanged == 2
    finally:
        with psycopg.connect(admin_url) as admin:
            admin.execute(
                "DELETE FROM configuration_import_audit_log "
                "WHERE org_id = %s",
                (org_id,),
            )
            admin.execute(
                "DELETE FROM configuration_entries WHERE org_id = %s",
                (org_id,),
            )
            admin.execute(
                "DELETE FROM secret_records WHERE org_id = %s",
                (org_id,),
            )
            admin.execute(
                "DELETE FROM organizations WHERE id = %s",
                (org_id,),
            )
