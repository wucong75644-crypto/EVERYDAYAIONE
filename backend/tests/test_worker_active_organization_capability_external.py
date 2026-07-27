"""迁移 209 的真实 PostgreSQL Worker 登录角色合同。"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
import pytest

from core.db_scope import SET_DATABASE_SCOPE_SQL
from core.local_db import LocalDBClient
from services.background_task_worker import BackgroundTaskWorker
from testing.tenant_role_matrix import (
    TenantMatrixConfigError,
    TenantRoleMatrixConfig,
)


pytestmark = pytest.mark.external

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "backend/migrations/209_worker_active_organization_capability.sql"
)
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback/"
    "209_worker_active_organization_capability_rollback.sql"
)


def _matrix_config() -> TenantRoleMatrixConfig:
    try:
        return TenantRoleMatrixConfig.from_mapping(os.environ)
    except TenantMatrixConfigError as exc:
        pytest.skip(str(exc))


def _apply_and_seed(
    config: TenantRoleMatrixConfig,
    active_id: str,
    suspended_id: str,
) -> None:
    with psycopg.connect(config.admin_url) as admin:
        owner = admin.execute("SELECT id FROM users LIMIT 1").fetchone()
        if owner is None:
            pytest.skip("TENANT_TEST_DATABASE_REQUIRES_ONE_USER")
        admin.execute(MIGRATION.read_text(encoding="utf-8"))
        suffix = uuid4().hex
        admin.execute(
            "INSERT INTO organizations(id, name, owner_id, status) "
            "VALUES (%s, %s, %s, 'active'), "
            "(%s, %s, %s, 'suspended')",
            (
                active_id, f"ar01-active-{suffix}", owner[0],
                suspended_id, f"ar01-suspended-{suffix}", owner[0],
            ),
        )
        admin.commit()


def _verify_worker_contract(
    config: TenantRoleMatrixConfig,
    active_id: str,
    suspended_id: str,
) -> None:
    with psycopg.connect(config.worker_url) as worker:
        with worker.transaction():
            worker.execute(
                SET_DATABASE_SCOPE_SQL,
                ("", "", "worker", "ar-01-role-contract"),
            )
            payload = worker.execute(
                "SELECT worker_list_active_organization_ids()",
            ).fetchone()[0]
            assert payload["outcome"] == "listed"
            assert active_id in payload["organization_ids"]
            assert suspended_id not in payload["organization_ids"]

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute("SELECT id FROM organizations LIMIT 1")
        worker.rollback()

        with worker.transaction():
            worker.execute(
                SET_DATABASE_SCOPE_SQL,
                ("", active_id, "worker", "ar-01-wrong-scope"),
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                worker.execute(
                    "SELECT worker_list_active_organization_ids()",
                )


async def _verify_application_entry(
    config: TenantRoleMatrixConfig,
    active_id: str,
    suspended_id: str,
) -> None:
    db = LocalDBClient(config.worker_url, min_size=1, max_size=1)
    try:
        with patch(
            "services.background_task_worker.get_settings",
            return_value=MagicMock(
                callback_base_url=None,
                poll_interval_seconds=0,
            ),
        ):
            worker = BackgroundTaskWorker(db)
        organization_ids = await worker._get_active_org_ids()
    finally:
        db.close()

    assert active_id in organization_ids
    assert suspended_id not in organization_ids


def _verify_runtime_denial(config: TenantRoleMatrixConfig) -> None:
    with psycopg.connect(config.runtime_url) as runtime:
        with runtime.transaction():
            runtime.execute(
                SET_DATABASE_SCOPE_SQL,
                ("", "", "runtime", "ar-01-runtime-denial"),
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(
                    "SELECT worker_list_active_organization_ids()",
                )


def _rollback_and_cleanup(
    config: TenantRoleMatrixConfig,
    active_id: str,
    suspended_id: str,
) -> None:
    with psycopg.connect(config.admin_url) as admin:
        admin.execute(
            "DELETE FROM organizations WHERE id IN (%s, %s)",
            (active_id, suspended_id),
        )
        admin.execute(ROLLBACK.read_text(encoding="utf-8"))
        admin.commit()


def _verify_rollback(config: TenantRoleMatrixConfig) -> None:
    with psycopg.connect(config.worker_url) as worker:
        with pytest.raises(psycopg.errors.UndefinedFunction):
            worker.execute("SELECT worker_list_active_organization_ids()")
        worker.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute("SELECT id FROM organizations LIMIT 1")


@pytest.mark.asyncio
async def test_worker_rpc_role_scope_table_denial_and_rollback() -> None:
    config = _matrix_config()
    active_id = str(uuid4())
    suspended_id = str(uuid4())

    _apply_and_seed(config, active_id, suspended_id)
    try:
        await _verify_application_entry(config, active_id, suspended_id)
        _verify_worker_contract(config, active_id, suspended_id)
        _verify_runtime_denial(config)
    finally:
        _rollback_and_cleanup(config, active_id, suspended_id)
    _verify_rollback(config)
