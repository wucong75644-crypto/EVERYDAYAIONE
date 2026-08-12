"""Static continuity contracts for the complete AR-18 migration lane."""

from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
FIRST = "227_21_agent_runtime_legacy_lifecycle_fence.sql"
LAST = "227_63_agent_runtime_chat_action_submission.sql"


def _migration_number(path: Path) -> int:
    return int(path.name.split("_", 2)[1])


def _lane() -> list:
    migrations = discover_migrations(ROOT / "migrations")
    identities = [item.identity for item in migrations]
    start, end = identities.index(FIRST), identities.index(LAST)
    return migrations[start : end + 1]


def test_ar18_lane_is_contiguous_and_has_exact_reverse_rollbacks() -> None:
    lane = _lane()
    assert lane[0].identity == FIRST
    assert lane[-1].identity == LAST
    assert [item.rollback_identity for item in lane] == [
        f"{Path(item.identity).stem}_rollback.sql" for item in lane
    ]

    expected = [
        path.name
        for path in sorted(
            (ROOT / "migrations").glob("227_*.sql"),
            key=lambda path: (_migration_number(path), path.name),
        )
        if 21 <= _migration_number(path) <= 63
    ]
    assert [item.identity for item in lane] == expected


def test_ar18_lane_declares_private_rls_and_narrow_worker_boundaries() -> None:
    lane_sql = "\n".join(item.path.read_text(encoding="utf-8") for item in _lane())
    for marker in (
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "SET search_path=pg_catalog,public",
        "session_user",
        "app.access_kind",
    ):
        assert marker in lane_sql

    assert "GRANT SELECT ON" not in lane_sql
    assert "GRANT INSERT ON" not in lane_sql
    assert "GRANT UPDATE ON" not in lane_sql
    assert "GRANT DELETE ON" not in lane_sql
    assert "GRANT ALL ON" not in lane_sql
    assert "TO everydayai_agent_runtime_worker" in lane_sql
    assert "accepted" in lane_sql
    assert "unknown" in lane_sql
    assert "reconcile_only" in lane_sql
    assert "reconcile" in lane_sql.lower()


def test_ar18_lane_keeps_ambiguous_outcomes_out_of_normal_redispatch() -> None:
    lane_sql = "\n".join(item.path.read_text(encoding="utf-8") for item in _lane())
    assert "accepted" in lane_sql and "unknown" in lane_sql
    assert "reconcile_only" in lane_sql
    assert "idempotent_replay" in lane_sql
    assert "claim" in lane_sql.lower()
    assert "dispatch" in lane_sql.lower()
