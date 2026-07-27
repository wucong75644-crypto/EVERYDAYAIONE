from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "migrations"


def _key(path: Path) -> tuple[int, str]:
    return int(path.name.split("_", 1)[0]), path.name


def test_ar16_migration_identity_and_lexical_order() -> None:
    apply_names = sorted(
        (path for path in ROOT.glob("220_*.sql") if "/rollback/" not in str(path)),
        key=_key,
    )
    ar16 = [path.name for path in apply_names if path.name.startswith("220_2")]
    assert ar16 == [
        "220_21_agent_runtime_authorization_foundation.sql",
        "220_22_agent_runtime_authorization_rpcs.sql",
        "220_23_agent_runtime_accepted_cancel_override.sql",
        "220_24_agent_runtime_authorization_dispatch_gate.sql",
        "220_25_agent_runtime_authorization_recovery.sql",
    ]

    rollback_names = sorted(
        (ROOT / "rollback").glob("220_2*_rollback.sql"),
        key=_key,
        reverse=True,
    )
    assert [path.name for path in rollback_names] == [
        "220_25_agent_runtime_authorization_recovery_rollback.sql",
        "220_24_agent_runtime_authorization_dispatch_gate_rollback.sql",
        "220_23_agent_runtime_accepted_cancel_override_rollback.sql",
        "220_22_agent_runtime_authorization_rpcs_rollback.sql",
        "220_21_agent_runtime_authorization_foundation_rollback.sql",
    ]
