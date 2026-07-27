"""Migration runner ordering contract for the four migration 217 identities."""

from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = [
    "217_01_agent_runtime_model_attempt_foundation.sql",
    "217_02_agent_runtime_model_attempt_credits.sql",
    "217_03_agent_runtime_model_attempt_lifecycle.sql",
    "217_04_agent_runtime_model_attempt_reconciliation.sql",
]


def test_discovery_applies_217_lexically_and_maps_rollbacks() -> None:
    discovered = discover_migrations(ROOT / "migrations")
    selected = [item for item in discovered if item.identity in EXPECTED]

    assert [item.identity for item in selected] == EXPECTED
    assert [item.rollback_identity for item in reversed(selected)] == [
        f"{Path(name).stem}_rollback.sql" for name in reversed(EXPECTED)
    ]
