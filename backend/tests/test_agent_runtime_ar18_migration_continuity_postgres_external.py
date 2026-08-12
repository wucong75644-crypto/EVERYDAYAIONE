"""Disposable PostgreSQL apply/rollback/reapply proof for AR-18."""

from __future__ import annotations

from pathlib import Path
import re

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]


def _migration_number(path: Path) -> int:
    match = re.match(r"227_(\d+)_", path.name)
    assert match, path.name
    return int(match.group(1))


def _lane() -> tuple[str, ...]:
    paths = sorted(
        (
            path
            for path in (ROOT / "migrations").glob("227_*.sql")
            if 21 <= _migration_number(path) <= 58
        ),
        key=lambda path: (_migration_number(path), path.name),
    )
    return tuple(path.name for path in paths)


def _rollbacks() -> tuple[str, ...]:
    return tuple(
        f"{Path(name).stem}_rollback.sql" for name in reversed(_lane())
    )


def _prepare_base_to_227_20(url: str) -> None:
    """Build the same disposable base used by AR-18 component tests."""
    with psycopg.connect(url) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS ltree")
        conn.execute(
            "DO $$ BEGIN IF to_regrole('everydayai_agent_model_gateway') "
            "IS NULL THEN CREATE ROLE everydayai_agent_model_gateway LOGIN NOINHERIT "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF; END $$"
        )
        conn.execute("SET ROLE everydayai_owner")
        for name in (
            "060_org_departments.sql",
            "061_org_positions.sql",
            "064_org_member_assignments.sql",
            "026_add_wecom_user_mappings.sql",
            "036_wecom_chat_targets.sql",
        ):
            conn.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))
        conn.execute(
            "ALTER TABLE wecom_user_mappings "
            "ADD COLUMN org_id UUID REFERENCES organizations(id)"
        )
        conn.execute(
            "ALTER TABLE wecom_chat_targets "
            "ADD COLUMN org_id UUID REFERENCES organizations(id)"
        )
        conn.execute((ROOT / "migrations/069_scheduled_tasks.sql").read_text())
        conn.execute((ROOT / "migrations/071_scheduled_task_schedule_type.sql").read_text())
        conn.execute(
            "CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY,"
            "org_id UUID,user_id UUID,relative_path TEXT NOT NULL,"
            "oss_object_key TEXT NOT NULL,purged BOOLEAN NOT NULL DEFAULT FALSE)"
        )
        conn.commit()

    _apply(url, "176_worker_scheduled_scanner.sql")
    for index in range(1, 20):
        path = next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql"))
        _apply(url, path.name)
    for name in (
        "158_configuration_control_plane_foundation.sql",
        "159_configuration_management_core.sql",
        "160_configuration_resolution_core.sql",
        "160_configuration_resolution_facades.sql",
        "201_wecom_callback_inbox.sql",
    ):
        _apply(url, name)
    for path in sorted(
        (ROOT / "migrations").glob("227_*.sql"),
        key=lambda item: (_migration_number(item), item.name),
    ):
        if 1 <= _migration_number(path) <= 20:
            _apply(url, path.name)


def _assert_security_contracts(url: str) -> None:
    tables = (
        "agent_runtime_task_cancel_intents",
        "agent_runtime_child_run_cancel_intents",
        "agent_runtime_scheduler_operation_intents",
        "agent_runtime_scheduler_operation_receipts",
        "agent_runtime_scheduler_cancel_gates",
        "agent_runtime_scheduled_execution_profiles",
        "agent_runtime_scheduled_run_bindings",
        "agent_runtime_scheduled_submission_control",
        "agent_runtime_scheduled_submission_intents",
        "agent_runtime_scheduled_finalization_intents",
        "agent_runtime_scheduled_delivery_intents",
        "agent_runtime_scheduled_wecom_deliveries",
        "agent_runtime_scheduled_wecom_prepared_recovery_requests",
        "agent_runtime_scheduled_wecom_outcome_requests",
        "agent_runtime_scheduled_wecom_reconcile_claim_requests",
        "agent_runtime_scheduled_wecom_reconcile_result_requests",
    )
    with psycopg.connect(url) as conn:
        for table in tables:
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                "WHERE oid=%s::regclass",
                (table,),
            ).fetchone() == (True, True), table
            for role in ("everydayai_worker", "everydayai_agent_runtime_worker"):
                assert conn.execute(
                    "SELECT has_table_privilege(%s,%s,'SELECT')", (role, table)
                ).fetchone()[0] is False, (role, table)

        assert conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint c "
            "JOIN pg_class r ON r.oid=c.conrelid "
            "WHERE r.relname='agent_action_attempts' "
            "AND pg_get_constraintdef(c.oid) ILIKE '%unknown%')"
        ).fetchone()[0] is True
        assert conn.execute(
            "SELECT COUNT(*) FROM pg_constraint c "
            "JOIN pg_class r ON r.oid=c.conrelid "
            "WHERE r.relname='agent_action_attempts' "
            "AND pg_get_constraintdef(c.oid) ILIKE '%accepted%'"
        ).fetchone()[0] >= 1
        for function_name in (
            "claim_next_agent_action_reconciliation",
            "resolve_agent_action_reconciliation",
            "record_agent_action_provider_submission",
        ):
            assert conn.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname=%s)",
                (function_name,),
            ).fetchone()[0] is True, function_name
        for function_name in (
            "claim_agent_action_reconciliation_v2",
            "resolve_agent_action_reconciliation_v2",
        ):
            assert conn.execute(
                "SELECT has_function_privilege(%s,p.oid,'EXECUTE') "
                "FROM pg_proc p WHERE p.proname=%s LIMIT 1",
                ("everydayai_agent_runtime_worker", function_name),
            ).fetchone()[0] is True


def test_ar18_continuous_apply_reverse_rollback_and_reapply(database: str) -> None:
    _prepare_base_to_227_20(database)
    lane = _lane()
    for name in lane:
        _apply(database, name)
    _assert_security_contracts(database)

    for name in _rollbacks():
        _rollback(database, name)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT to_regclass('agent_runtime_scheduled_execution_profiles')"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT to_regclass('agent_runtime_task_cancel_intents')"
        ).fetchone()[0] is None

    for name in lane:
        _apply(database, name)
    _assert_security_contracts(database)
