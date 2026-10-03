#!/usr/bin/env python3
"""Focused tests for the production migration executor safety boundary."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_migration_executor", SOURCE / "deploy/verify_migration_executor.py"
)
assert SPEC is not None and SPEC.loader is not None
executor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(executor)


class MigrationExecutorTests(unittest.TestCase):
    def test_same_database_instance_requires_matching_database_oid_and_start_time(self):
        identity = ("everydayai_runtime_current_20260818", 16385, 1780361120)
        executor.verify_same_database_instance(identity, identity)
        for changed in (
            ("other_database", 16385, 1780361120),
            (identity[0], 16386, identity[2]),
            (identity[0], identity[1], identity[2] + 1),
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(
                ValueError, "MIGRATION_DATABASE_IDENTITY_MISMATCH"
            ):
                executor.verify_same_database_instance(identity, changed)

    def test_local_admin_connection_treats_database_name_as_one_argument(self):
        database_name = "prod db;$(must-not-run)"
        seen = {}

        def fake_run(command, **kwargs):
            seen["command"] = command
            seen["kwargs"] = kwargs
            return type("Result", (), {"stdout": json.dumps([database_name, 123, 456])})()

        self.assertEqual(
            executor.read_local_identity(database_name, run=fake_run),
            (database_name, 123, 456),
        )
        command = seen["command"]
        self.assertEqual(command[command.index("-d") + 1], database_name)
        self.assertIn("sudo", command)
        self.assertEqual(seen["kwargs"], {"check": True, "capture_output": True, "text": True})

    def test_role_preflight_covers_existing_migration_owners(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append((command, kwargs))
            return type("Result", (), {"stdout": ""})()

        executor.verify_migration_roles("target-db", run=fake_run)
        command, kwargs = seen[0]
        self.assertEqual(command[:4], ["sudo", "-n", "-u", "postgres"])
        self.assertEqual(command[command.index("-d") + 1], "target-db")
        self.assertIn("SET ROLE everydayai_owner; RESET ROLE;", command[-1])
        self.assertIn("SET ROLE everydayai; RESET ROLE;", command[-1])
        self.assertTrue(kwargs["check"])

    def test_existing_object_owner_preflight_matches_production_contract(self):
        seen = []
        expected = dict(executor.EXPECTED_EXISTING_OBJECT_OWNERS)

        def fake_run(command, **kwargs):
            seen.append(command)
            return type("Result", (), {"stdout": json.dumps(expected) + "\n"})()

        executor.verify_existing_object_owners("target-db", run=fake_run)
        self.assertEqual(seen[0][seen[0].index("-d") + 1], "target-db")
        self.assertIn("public.configuration_definitions", seen[0][-1])
        with self.assertRaisesRegex(ValueError, "MIGRATION_EXISTING_OBJECT_OWNER_MISMATCH"):
            executor.verify_existing_object_owners(
                "target-db",
                run=lambda *args, **kwargs: type("Result", (), {"stdout": "{}"})(),
            )

    def test_release_preflight_runs_before_frontend_sync_and_migrations_run_as_owner(self):
        script = (SOURCE / "deploy/deploy.sh").read_text()
        verify = script.index("< deploy/verify_migration_executor.py")
        sync_frontend = script.index("        sync_frontend\n", verify)
        migration_as_owner = script.index('sudo -n -u postgres psql -d "$migration_database"')
        migration_transaction = script.index("SET LOCAL ROLE everydayai_owner;", migration_as_owner)
        migration_file = script.index('-f "$migration_path"', migration_transaction)
        reset_and_record = script.index("RESET ROLE; INSERT INTO public.deployment_schema_migrations", migration_file)
        self.assertLess(verify, sync_frontend)
        self.assertLess(migration_as_owner, migration_transaction)
        self.assertLess(migration_transaction, migration_file)
        self.assertLess(migration_file, reset_and_record)
        audit_migration = (SOURCE / "backend/migrations/267_mcp_tool_audit_columns.sql").read_text()
        organization_migration = (
            SOURCE / "backend/migrations/266_mcp_org_connector_governance.sql"
        ).read_text()
        self.assertIn("SET LOCAL ROLE everydayai;", organization_migration)
        self.assertIn(
            "ALTER TABLE organization_mcp_connectors OWNER TO everydayai_owner;",
            organization_migration,
        )
        self.assertIn("SET LOCAL ROLE everydayai;", audit_migration)


if __name__ == "__main__":
    unittest.main()
