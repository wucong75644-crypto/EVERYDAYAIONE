"""Migration 166 WeCom Worker discovery capability contracts."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "migrations/166_wecom_worker_discovery.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT / "migrations/rollback/166_wecom_worker_discovery_rollback.sql"
).read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;",
        SQL,
        re.DOTALL,
    )
    assert match
    return match.group(0)


def test_discovery_requires_exact_actorless_worker_scope() -> None:
    assertion = _function("_assert_wecom_worker_discovery_scope")

    assert "session_user <> 'everydayai_worker'" in assertion
    assert "app.access_kind" in assertion
    assert "IS DISTINCT FROM 'worker'" in assertion
    assert "app.actor_user_id" in assertion
    assert "app.org_id" in assertion
    assert "app.request_id" in assertion
    assert "WECOM_WORKER_DISCOVERY_SCOPE_REQUIRED" in assertion


def test_discovery_returns_only_non_secret_target_metadata() -> None:
    discovery = _function("discover_wecom_bot_targets")

    assert "SECURITY DEFINER" in discovery
    assert "'org_id'" in discovery
    assert "'credential_version'" in discovery
    assert "organization.status = 'active'" in discovery
    assert "bot_secret.status = 'active'" in discovery
    assert "bot_secret.expires_at > NOW()" in discovery
    for forbidden in (
        "payload_ciphertext",
        "wrapped_dek",
        "kek_version",
        "'bot_id'",
        "'bot_secret'",
    ):
        assert forbidden not in discovery


def test_only_worker_receives_discovery_execute() -> None:
    grants = SQL[SQL.index("REVOKE ALL ON FUNCTION"):]

    assert "GRANT EXECUTE ON FUNCTION discover_wecom_bot_targets()" in grants
    assert "TO everydayai_worker;" in grants
    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime;" in grants
    assert "GRANT SELECT" not in grants


def test_rollback_removes_only_new_capabilities() -> None:
    assert "DROP FUNCTION IF EXISTS discover_wecom_bot_targets();" in ROLLBACK
    assert (
        "DROP FUNCTION IF EXISTS _assert_wecom_worker_discovery_scope();"
        in ROLLBACK
    )
    assert "DROP TABLE" not in ROLLBACK
