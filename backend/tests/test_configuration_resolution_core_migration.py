"""Migration 160 fixed Bundle registry and resolution core contracts."""

from __future__ import annotations

import json
from pathlib import Path
import re

from services.configuration.definitions import (
    BUNDLE_DEFINITIONS,
    DEFINITION_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/160_configuration_resolution_core.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/160_configuration_resolution_core_rollback.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
INCREMENTAL_SQL = (
    ROOT / "migrations/201_wecom_callback_inbox.sql"
).read_text(encoding="utf-8")
ROLLBACK_SQL = ROLLBACK.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;(?:\n|$)",
        SQL,
        re.DOTALL,
    )
    assert match, f"missing function {name}"
    return match.group(0)


def _snapshot_rows() -> dict[str, tuple[dict[str, object], str]]:
    rows = re.findall(
        r"\(\s*'v1',\s*'([^']+)',\s*'(\{.*?\})'::JSONB,\s*"
        r"'([0-9a-f]{64})',\s*TRUE\s*\)",
        SQL,
        re.DOTALL,
    )
    snapshot = {
        name: (json.loads(contract_json), contract_hash)
        for name, contract_json, contract_hash in rows
    }
    insert_sql = INCREMENTAL_SQL.split(
        "INSERT INTO configuration_bundle_definitions(", 1,
    )[1].split("CREATE OR REPLACE FUNCTION", 1)[0]
    inserts = re.findall(
        r"\(\s*'v1',\s*'([^']+)',\s*'(\{.*?\})'::JSONB,\s*"
        r"'([0-9a-f]{64})',\s*TRUE\s*\)",
        insert_sql,
        re.DOTALL,
    )
    snapshot.update({
        name: (json.loads(contract_json), contract_hash)
        for name, contract_json, contract_hash in inserts
    })
    return snapshot


def test_bundle_snapshot_exactly_matches_code_registry() -> None:
    snapshot = _snapshot_rows()
    assert set(snapshot) == set(BUNDLE_DEFINITIONS)
    for name, bundle in BUNDLE_DEFINITIONS.items():
        contract, contract_hash = snapshot[name]
        assert contract == bundle.contract()
        assert contract_hash == bundle.contract_hash()
    assert f"'{DEFINITION_VERSION}'" in SQL


def test_bundle_projection_has_drift_and_shape_constraints() -> None:
    assert "PRIMARY KEY (definition_version, bundle_name)" in SQL
    assert "contract_json->>'name' = bundle_name" in SQL
    assert "uq_configuration_bundle_active_name" in SQL
    assert "configuration_bundle_required_array" in SQL
    assert "configuration_bundle_optional_array" in SQL
    assert "configuration_bundle_consumers_array" in SQL


def test_effective_item_resolution_enforces_precedence_and_policy() -> None:
    body = _function_body("_resolve_effective_configuration_item")
    assert "v_user_allowed" in body
    assert "allow_user_override" in body
    assert "v_policy.locked" in body
    assert "SELECT entry.id, 1 AS priority" in body
    assert "SELECT entry.id, 2 AS priority" in body
    assert "SELECT entry.id, 3 AS priority" in body
    assert "fallback_policy" in body
    assert "CONFIG_BUNDLE_INCOMPLETE" in body


def test_secret_resolution_fails_closed_and_returns_only_envelope() -> None:
    body = _function_body("_project_configuration_entry")
    assert "v_secret.status <> 'active'" in body
    assert "v_secret.expires_at" in body
    assert "v_secret.payload_version <> v_entry.version" in body
    assert "CONFIG_SECRET_UNAVAILABLE" in body
    for field in (
        "payload_ciphertext",
        "wrapped_dek",
        "kek_version",
        "payload_version",
        "secret_name",
    ):
        assert field in body
    assert "plaintext" not in body


def test_bundle_core_resolves_fixed_contract_members_only() -> None:
    body = _function_body("_resolve_configuration_bundle")
    assert "configuration_bundle_definitions" in body
    assert "required_keys" in body
    assert "optional_keys" in body
    assert "_resolve_effective_configuration_item" in body
    assert "CONFIG_BUNDLE_UNKNOWN" in body
    assert "p_config_keys" not in body


def test_only_secret_free_registry_contract_is_granted() -> None:
    registry = _function_body("get_configuration_bundle_registry_contract")
    assert "definition_version" in registry
    assert "bundle_name" in registry
    assert "contract_hash" in registry
    assert "contract_json" not in registry
    grant_start = SQL.index("GRANT EXECUTE ON FUNCTION")
    grant = SQL[grant_start:SQL.index("REVOKE ALL ON FUNCTION", grant_start)]
    assert "get_configuration_bundle_registry_contract()" in grant
    assert "_resolve_configuration_bundle" not in grant


def test_core_rollback_drops_functions_before_bundle_projection() -> None:
    table_position = ROLLBACK_SQL.index(
        "DROP TABLE IF EXISTS configuration_bundle_definitions"
    )
    assert ROLLBACK_SQL.index(
        "DROP FUNCTION IF EXISTS _resolve_configuration_bundle"
    ) < table_position
    assert ROLLBACK_SQL.index(
        "DROP FUNCTION IF EXISTS _resolve_effective_configuration_item"
    ) < table_position
