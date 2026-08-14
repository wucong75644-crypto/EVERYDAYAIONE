"""Migration 158 unified configuration control-plane foundation contract."""

from __future__ import annotations

import json
from pathlib import Path
import re

from services.configuration.definitions import (
    CONFIG_DEFINITIONS,
    DEFINITION_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/158_configuration_control_plane_foundation.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/158_configuration_control_plane_foundation_rollback.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
INCREMENTAL_SQLS = tuple(
    (ROOT / "migrations" / name).read_text(encoding="utf-8")
    for name in (
        "201_wecom_callback_inbox.sql",
        "227_50_agent_runtime_scheduled_wecom_configuration_facade.sql",
    )
)
ROLLBACK_SQL = ROLLBACK.read_text(encoding="utf-8")
PROTECTED_TABLES = (
    "secret_records",
    "configuration_entries",
    "configuration_policies",
)


def _snapshot_rows() -> dict[str, tuple[dict[str, object], str]]:
    rows = re.findall(
        r"\(\s*'v1',\s*'([^']+)',\s*'(\{.*?\})'::JSONB,\s*"
        r"'([0-9a-f]{64})',\s*TRUE\s*\)",
        SQL,
        re.DOTALL,
    )
    snapshot = {
        key: (json.loads(contract_json), contract_hash)
        for key, contract_json, contract_hash in rows
    }
    for incremental_sql in INCREMENTAL_SQLS:
        updates = re.findall(
            r"UPDATE (?:public\.)?configuration_definitions\s+"
            r"SET contract_json\s*=\s*'(\{.*?\})'::JSONB,\s*"
            r"contract_hash\s*=\s*'([0-9a-f]{64})'\s+"
            r"WHERE .*?config_key\s*=\s*'([^']+)'.*?;",
            incremental_sql,
            re.DOTALL,
        )
        for contract_json, contract_hash, key in updates:
            snapshot[key] = (json.loads(contract_json), contract_hash)
        if "INSERT INTO configuration_definitions(" not in incremental_sql:
            continue
        insert_sql = incremental_sql.split(
            "INSERT INTO configuration_definitions(", 1,
        )[1].split("INSERT INTO configuration_bundle_definitions(", 1)[0]
        inserts = re.findall(
            r"\(\s*'v1',\s*'([^']+)',\s*'(\{.*?\})'::JSONB,\s*"
            r"'([0-9a-f]{64})',\s*TRUE\s*\)",
            insert_sql,
            re.DOTALL,
        )
        snapshot.update({
            key: (json.loads(contract_json), contract_hash)
            for key, contract_json, contract_hash in inserts
        })
    return snapshot


def test_database_snapshot_exactly_matches_code_registry() -> None:
    snapshot = _snapshot_rows()
    assert set(snapshot) == set(CONFIG_DEFINITIONS)
    for key, definition in CONFIG_DEFINITIONS.items():
        contract, contract_hash = snapshot[key]
        assert contract == definition.contract()
        assert contract_hash == definition.contract_hash()
    assert f"'{DEFINITION_VERSION}'" in SQL


def test_definition_projection_rejects_drift_and_multiple_active_versions() -> None:
    assert "PRIMARY KEY (definition_version, config_key)" in SQL
    assert "contract_json->>'key' = config_key" in SQL
    assert "contract_hash ~ '^[0-9a-f]{64}$'" in SQL
    assert "CREATE UNIQUE INDEX uq_configuration_definition_active_key" in SQL
    assert "WHERE active" in SQL


def test_configuration_entries_enforce_scope_value_and_definition() -> None:
    assert "CREATE TABLE configuration_entries" in SQL
    assert "configuration_entry_definition_fk" in SQL
    assert "configuration_entry_scope_check" in SQL
    assert "(value_json IS NULL) <> (secret_id IS NULL)" in SQL
    assert (
        "UNIQUE NULLS NOT DISTINCT "
        "(scope_kind, org_id, user_id, config_key)"
    ) in SQL


def test_secret_records_use_envelope_fields_and_one_active_scope_name() -> None:
    assert "CREATE TABLE secret_records" in SQL
    for field in (
        "payload_ciphertext TEXT NOT NULL",
        "wrapped_dek TEXT NOT NULL",
        "kek_version VARCHAR(64) NOT NULL",
        "payload_version BIGINT NOT NULL",
        "rotated_from UUID REFERENCES secret_records",
    ):
        assert field in SQL
    assert "uq_secret_record_active_scope_name" in SQL
    assert "NULLS NOT DISTINCT" in SQL
    assert "WHERE status = 'active'" in SQL
    assert not re.search(r"^\s*plaintext\s+", SQL, re.MULTILINE | re.IGNORECASE)


def test_policy_is_per_org_key_and_bound_to_definition_version() -> None:
    assert "CREATE TABLE configuration_policies" in SQL
    assert "PRIMARY KEY (org_id, config_key)" in SQL
    assert "configuration_policy_definition_fk" in SQL
    assert "allow_user_override BOOLEAN NOT NULL" in SQL
    assert "locked BOOLEAN NOT NULL" in SQL
    assert "updated_by UUID NOT NULL REFERENCES users" in SQL


def test_protected_tables_are_force_rls_owner_only_from_creation() -> None:
    for table in PROTECTED_TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in SQL
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in SQL
        assert f"CREATE POLICY {table}_owner_only ON {table}" in SQL
    assert SQL.count("TO everydayai_owner") == len(PROTECTED_TABLES)
    assert "GRANT " not in SQL


def test_service_roles_have_no_direct_table_access() -> None:
    revoke = re.search(
        r"REVOKE ALL ON TABLE configuration_definitions,.*?"
        r"FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, "
        r"everydayai_worker;",
        SQL,
        re.DOTALL,
    )
    assert revoke


def test_rollback_drops_dependents_before_registry_projection() -> None:
    positions = [
        ROLLBACK_SQL.index(f"DROP TABLE IF EXISTS {table}")
        for table in (
            "configuration_policies",
            "configuration_entries",
            "secret_records",
            "configuration_definitions",
        )
    ]
    assert positions == sorted(positions)


def test_retired_legacy_159_is_not_discoverable() -> None:
    assert not (ROOT / "migrations/159_org_erp_token_capabilities.sql").exists()
    assert not (
        ROOT
        / "migrations/rollback/159_org_erp_token_capabilities_rollback.sql"
    ).exists()
