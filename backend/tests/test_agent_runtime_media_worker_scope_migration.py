from pathlib import Path
import re

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
IDENTITY = "228_08c_agent_runtime_media_worker_scope.sql"
ROLLBACK_IDENTITY = "228_08c_agent_runtime_media_worker_scope_rollback.sql"
MIGRATION = ROOT / "migrations" / IDENTITY
ROLLBACK = ROOT / "migrations/rollback" / ROLLBACK_IDENTITY
PREDECESSOR = ROOT / "migrations/228_05_agent_runtime_media_manifest_readback.sql"


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _function_statement(sql: str) -> str:
    start = sql.index("CREATE OR REPLACE FUNCTION worker_discover_media_tasks")
    end = sql.index("$$;", start) + 3
    return sql[start:end]


def _function_acl(sql: str) -> str:
    start = sql.index(
        "REVOKE ALL ON FUNCTION worker_discover_media_tasks(INTEGER)",
    )
    end = sql.index("TO everydayai_worker;", start) + len("TO everydayai_worker;")
    return sql[start:end]


def test_worker_scope_migration_has_exact_identity_and_rollback() -> None:
    matches = [
        item for item in discover_migrations(ROOT / "migrations")
        if item.identity == IDENTITY
    ]

    assert len(matches) == 1
    assert matches[0].rollback_identity == ROLLBACK_IDENTITY
    identities = [
        item.identity for item in discover_migrations(ROOT / "migrations")
    ]
    assert identities.index(IDENTITY) > identities.index(
        "228_07_agent_runtime_media_controls.sql",
    )


def test_worker_scope_migration_is_additive_and_fenced() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    compact = _compact(sql)

    assert "CREATE OR REPLACE FUNCTION worker_discover_media_tasks" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path=pg_catalog,public" in sql
    assert "session_user <> 'everydayai_worker'" in sql
    assert (
        "current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'worker'"
        in sql
    )
    assert "ERRCODE='42501'" in sql
    for marker in (
        "task.statusin('pending','running')",
        "task.typein('image','video')",
        "agent_runtime_media_action_bindingsbinding",
        "agent_runtime_prepared_media_action_bindingsbinding",
        "organization.status='active'",
        "orderbycoalesce(task.last_polled_at,task.created_at),task.id",
    ):
        assert marker in compact

    for forbidden in (
        "create table", "alter table", "drop table", "insert into", "update ",
        "delete from", "truncate ",
    ):
        assert forbidden not in sql.lower()


def test_worker_scope_migration_keeps_minimum_acl() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    acl = _function_acl(sql)

    assert "everydayai_worker" in acl
    assert "PUBLIC" in acl
    assert "everydayai_agent_runtime_worker" in acl
    assert acl.count("GRANT EXECUTE") == 1
    assert "TO everydayai_worker;" in acl
    assert "GRANT SELECT" not in sql
    assert "GRANT INSERT" not in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql
    assert "GRANT ALL" not in sql


def test_worker_scope_rollback_restores_228_05_definition_and_acl() -> None:
    predecessor = PREDECESSOR.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")

    assert _function_statement(rollback) == _function_statement(predecessor)
    assert _compact(_function_acl(rollback)) == _compact(_function_acl(predecessor))
    assert "app.access_kind" not in rollback
    assert "DROP FUNCTION" not in rollback
