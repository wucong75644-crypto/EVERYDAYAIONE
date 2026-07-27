"""Migration runner discovers split migration 218 in frozen lexical order."""

from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = [
    "218_01_agent_runtime_action_foundation.sql",
    "218_02_agent_runtime_action_tool_terminal.sql",
    "218_03_agent_runtime_action_lifecycle.sql",
    "218_04_agent_runtime_action_reconciliation.sql",
]


def test_discover_migrations_preserves_ar12_order() -> None:
    discovered = discover_migrations(ROOT / "migrations")
    selected = [item for item in discovered if item.identity in EXPECTED]
    assert [item.identity for item in selected] == EXPECTED
    assert [item.rollback_identity for item in reversed(selected)] == [
        f"{Path(name).stem}_rollback.sql" for name in reversed(EXPECTED)
    ]
