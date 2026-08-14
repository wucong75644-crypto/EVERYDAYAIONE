"""管理员资产 ZIP RPC 与 ScopedDatabaseClient 参数合同。"""

from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

from psycopg.types.json import Jsonb

from core.db_scope import (
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
    _rpc_sql,
)
from core.local_db import LocalDBClient


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/209_platform_admin_user_assets_capability.sql"
)
RPC_NAME = "resolve_platform_admin_user_assets_download"


def _scoped_client() -> tuple[ScopedDatabaseClient, MagicMock]:
    pool = MagicMock()
    connection = MagicMock()
    cursor = MagicMock()
    cursor.description = None
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    pool.connection.return_value = connection_context
    connection.cursor.return_value = cursor_context
    connection.transaction.return_value = MagicMock()
    base = object.__new__(LocalDBClient)
    base._pool = pool
    scope = DatabaseScope(
        actor_user_id=str(uuid4()),
        org_id=None,
        access_kind=DatabaseAccessKind.RUNTIME,
        request_id="admin-assets-zip-contract",
    )
    return ScopedDatabaseClient(base, scope), cursor


def test_rpc_sql_binds_asset_id_list_as_named_jsonb_parameter() -> None:
    target_user_id = str(uuid4())
    asset_ids = [str(uuid4()), str(uuid4())]

    sql, params = _rpc_sql(
        RPC_NAME,
        {
            "p_actor_user_id": target_user_id,
            "p_asset_ids": asset_ids,
        },
    )

    assert sql == (
        f'SELECT public."{RPC_NAME}"('
        "p_actor_user_id := %s, p_asset_ids := %s)"
    )
    assert params[0] == target_user_id
    assert isinstance(params[1], Jsonb)
    assert params[1].obj == asset_ids


def test_scoped_client_executes_zip_rpc_with_jsonb_contract() -> None:
    client, cursor = _scoped_client()
    target_user_id = str(uuid4())
    asset_ids = [str(uuid4())]

    client.rpc(
        RPC_NAME,
        {
            "p_actor_user_id": target_user_id,
            "p_asset_ids": asset_ids,
        },
    ).execute()

    business_sql, params = cursor.execute.call_args_list[1].args
    assert (
        f'"{RPC_NAME}"(p_actor_user_id := %s, p_asset_ids := %s)'
        in business_sql
    )
    assert params[0] == target_user_id
    assert isinstance(params[1], Jsonb)
    assert params[1].obj == asset_ids


def test_migration_exposes_the_same_uuid_jsonb_signature() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert (
        "CREATE FUNCTION resolve_platform_admin_user_assets_download(\n"
        "    p_actor_user_id UUID,\n"
        "    p_asset_ids JSONB\n"
        ")"
    ) in sql
    assert "resolve_platform_admin_user_assets_download(\n    UUID, JSONB\n)" in sql
    assert "resolve_platform_admin_user_assets_download(\n    UUID, UUID[]\n)" not in sql
