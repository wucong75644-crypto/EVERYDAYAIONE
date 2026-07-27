"""Migration 209 real PostgreSQL login-role capability matrix."""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from core.db_scope import SET_DATABASE_SCOPE_SQL
from testing.tenant_role_matrix import (
    TenantMatrixConfigError,
    TenantRoleMatrixConfig,
)


pytestmark = pytest.mark.external
SIGNATURE = (
    "list_platform_admin_user_assets("
    "uuid,text,text,integer,timestamp with time zone,uuid)"
)
DOWNLOAD_SIGNATURE = (
    "resolve_platform_admin_user_assets_download(uuid,jsonb)"
)


@pytest.fixture(scope="module")
def matrix_config() -> TenantRoleMatrixConfig:
    try:
        return TenantRoleMatrixConfig.from_mapping(os.environ)
    except TenantMatrixConfigError as exc:
        pytest.skip(str(exc))


@pytest.fixture()
def asset_facts(matrix_config: TenantRoleMatrixConfig) -> dict[str, str]:
    admin_id, inactive_id, user_id = (str(uuid4()) for _ in range(3))
    asset_id, asset_id_2, other_asset_id, deleted_asset_id = (
        str(uuid4()) for _ in range(4)
    )
    ref_id, ref_id_2, other_ref_id, deleted_ref_id = (
        str(uuid4()) for _ in range(4)
    )
    phone_suffix = uuid4().hex[:9]
    for identity, phone in (
        (admin_id, f"15{phone_suffix}"),
        (inactive_id, f"16{phone_suffix}"),
        (user_id, f"17{phone_suffix}"),
    ):
        with psycopg.connect(matrix_config.runtime_url) as runtime:
            with runtime.transaction():
                runtime.execute(
                    SET_DATABASE_SCOPE_SQL,
                    ("", "", "runtime", "admin-assets-matrix"),
                )
                runtime.execute(
                    "SELECT register_web_identity(%s, %s, %s, NULL, %s, "
                    "NOW() + INTERVAL '1 day')",
                    (identity, phone, "资产矩阵用户", uuid4().hex + uuid4().hex),
                )
    with psycopg.connect(matrix_config.admin_url) as admin:
        with admin.transaction():
            admin.execute(
                "UPDATE users SET role = 'super_admin' "
                "WHERE id IN (%s, %s)",
                (admin_id, inactive_id),
            )
            admin.execute(
                "UPDATE users SET status = 'inactive' WHERE id = %s",
                (inactive_id,),
            )
            admin.executemany(
                "INSERT INTO user_assets("
                "id, storage_scope, storage_owner_key, storage_provider, "
                "storage_key, media_type, status, original_url, "
                "download_url, name"
                ") VALUES (%s, 'user', %s, 'workspace', %s, "
                "'image', %s, %s, %s, %s)",
                [
                    (
                        current_id,
                        owner_id,
                        f"matrix/{current_id}.png",
                        status,
                        f"https://assets.test/{current_id}.png",
                        f"https://assets.test/{current_id}.png",
                        name,
                    )
                    for current_id, owner_id, status, name in (
                        (asset_id, user_id, "ready", "matrix-1.png"),
                        (asset_id_2, user_id, "ready", "matrix-2.png"),
                        (
                            other_asset_id,
                            admin_id,
                            "ready",
                            "other.png",
                        ),
                        (
                            deleted_asset_id,
                            user_id,
                            "deleted",
                            "deleted.png",
                        ),
                    )
                ],
            )
            admin.executemany(
                "INSERT INTO user_asset_refs("
                "id, ref_key, asset_id, actor_user_id, source_type, "
                "source_kind, ref_kind"
                ") VALUES (%s, %s, %s, %s, 'generated', "
                "'image_task', 'task')",
                [
                    (current_ref, f"matrix:{current_ref}", current_id, owner_id)
                    for current_ref, current_id, owner_id in (
                        (ref_id, asset_id, user_id),
                        (ref_id_2, asset_id_2, user_id),
                        (other_ref_id, other_asset_id, admin_id),
                        (deleted_ref_id, deleted_asset_id, user_id),
                    )
                ],
            )
    yield {
        "admin_id": admin_id,
        "inactive_id": inactive_id,
        "user_id": user_id,
        "asset_id": asset_id,
        "asset_id_2": asset_id_2,
        "other_asset_id": other_asset_id,
        "deleted_asset_id": deleted_asset_id,
    }
    with psycopg.connect(matrix_config.admin_url) as admin:
        with admin.transaction():
            admin.execute(
                "DELETE FROM user_assets WHERE id = ANY(%s::UUID[])",
                ([
                    asset_id,
                    asset_id_2,
                    other_asset_id,
                    deleted_asset_id,
                ],),
            )
            admin.execute(
                "DELETE FROM users WHERE id IN (%s, %s, %s)",
                (admin_id, inactive_id, user_id),
            )


def _call(
    database_url: str,
    actor_id: str,
    user_id: str,
    *,
    source_type: str = "generated",
    media_type: str | None = "image",
    cursor_created_at: str | None = None,
    cursor_id: str | None = None,
) -> dict:
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            connection.execute(
                SET_DATABASE_SCOPE_SQL,
                (actor_id, "", "runtime", "admin-assets-matrix"),
            )
            return connection.execute(
                "SELECT list_platform_admin_user_assets("
                "%s, %s, %s, 24, %s, %s)",
                (
                    user_id, source_type, media_type,
                    cursor_created_at, cursor_id,
                ),
            ).fetchone()[0]


def _call_download(
    database_url: str,
    actor_id: str,
    user_id: str,
    asset_ids: object,
) -> list[dict]:
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            connection.execute(
                SET_DATABASE_SCOPE_SQL,
                (actor_id, "", "runtime", "admin-assets-download-matrix"),
            )
            return connection.execute(
                "SELECT resolve_platform_admin_user_assets_download("
                "%s, %s::JSONB)",
                (user_id, Jsonb(asset_ids)),
            ).fetchone()[0]


def test_active_super_admin_can_read_cross_user_assets(
    matrix_config: TenantRoleMatrixConfig,
    asset_facts: dict[str, str],
) -> None:
    payload = _call(
        matrix_config.runtime_url,
        asset_facts["admin_id"],
        asset_facts["user_id"],
    )

    assert payload["total"] == 1
    assert payload["items"][0]["id"] == asset_facts["asset_id"]


def test_active_super_admin_resolves_minimal_download_fields_in_order(
    matrix_config: TenantRoleMatrixConfig,
    asset_facts: dict[str, str],
) -> None:
    single = _call_download(
        matrix_config.runtime_url,
        asset_facts["admin_id"],
        asset_facts["user_id"],
        [asset_facts["asset_id"]],
    )
    requested = [asset_facts["asset_id_2"], asset_facts["asset_id"]]

    payload = _call_download(
        matrix_config.runtime_url,
        asset_facts["admin_id"],
        asset_facts["user_id"],
        requested,
    )

    assert [str(item["id"]) for item in single] == [asset_facts["asset_id"]]
    assert [str(item["id"]) for item in payload] == requested
    assert all(set(item) == {"id", "download_url", "name"} for item in payload)


@pytest.mark.parametrize("actor_key", ["user_id", "inactive_id"])
def test_download_rejects_non_admin_and_inactive_super_admin(
    matrix_config: TenantRoleMatrixConfig,
    asset_facts: dict[str, str],
    actor_key: str,
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _call_download(
            matrix_config.runtime_url,
            asset_facts[actor_key],
            asset_facts["user_id"],
            [asset_facts["asset_id"]],
        )


@pytest.mark.parametrize(
    "mixed_id_key",
    ["other_asset_id", "deleted_asset_id", "missing_asset_id"],
)
def test_download_rejects_any_unresolved_or_cross_user_asset(
    matrix_config: TenantRoleMatrixConfig,
    asset_facts: dict[str, str],
    mixed_id_key: str,
) -> None:
    mixed_id = (
        str(uuid4())
        if mixed_id_key == "missing_asset_id"
        else asset_facts[mixed_id_key]
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_info:
        _call_download(
            matrix_config.runtime_url,
            asset_facts["admin_id"],
            asset_facts["user_id"],
            [asset_facts["asset_id"], mixed_id],
        )
    assert str(exc_info.value).startswith(
        "ADMIN_ASSET_DOWNLOAD_SCOPE_INVALID",
    )


@pytest.mark.parametrize("asset_ids", [[], ["duplicate", "duplicate"]])
def test_download_rejects_empty_and_duplicate_ids(
    matrix_config: TenantRoleMatrixConfig,
    asset_facts: dict[str, str],
    asset_ids: list[str],
) -> None:
    requested = (
        [asset_facts["asset_id"], asset_facts["asset_id"]]
        if asset_ids
        else []
    )
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _call_download(
            matrix_config.runtime_url,
            asset_facts["admin_id"],
            asset_facts["user_id"],
            requested,
        )


@pytest.mark.parametrize(
    "invalid_kind",
    [
        "json_null",
        "element_null",
        "empty_string",
        "invalid_uuid",
        "number",
        "boolean",
        "object",
        "nested_array",
        "top_number",
        "top_boolean",
        "top_object",
        "normalized_duplicate",
    ],
)
def test_download_rejects_invalid_jsonb_elements_and_shapes(
    matrix_config: TenantRoleMatrixConfig,
    asset_facts: dict[str, str],
    invalid_kind: str,
) -> None:
    asset_id = asset_facts["asset_id"]
    payloads: dict[str, object] = {
        "json_null": None,
        "element_null": [None],
        "empty_string": [""],
        "invalid_uuid": ["not-a-uuid"],
        "number": [1],
        "boolean": [True],
        "object": [{"id": asset_id}],
        "nested_array": [[asset_id]],
        "top_number": 1,
        "top_boolean": True,
        "top_object": {"id": asset_id},
        "normalized_duplicate": [asset_id, asset_id.upper()],
    }

    with pytest.raises(psycopg.errors.InvalidParameterValue) as exc_info:
        _call_download(
            matrix_config.runtime_url,
            asset_facts["admin_id"],
            asset_facts["user_id"],
            payloads[invalid_kind],
        )
    assert str(exc_info.value).startswith(
        "ADMIN_ASSET_DOWNLOAD_ARGUMENT_INVALID",
    )


def test_download_rejects_sql_null(
    matrix_config: TenantRoleMatrixConfig,
    asset_facts: dict[str, str],
) -> None:
    with psycopg.connect(matrix_config.runtime_url) as connection:
        with connection.transaction():
            connection.execute(
                SET_DATABASE_SCOPE_SQL,
                (
                    asset_facts["admin_id"],
                    "",
                    "runtime",
                    "admin-assets-download-matrix",
                ),
            )
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                connection.execute(
                    "SELECT resolve_platform_admin_user_assets_download("
                    "%s, NULL::JSONB)",
                    (asset_facts["user_id"],),
                )


def test_download_rejects_over_limit_and_runtime_table_reads(
    matrix_config: TenantRoleMatrixConfig,
    asset_facts: dict[str, str],
) -> None:
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _call_download(
            matrix_config.runtime_url,
            asset_facts["admin_id"],
            asset_facts["user_id"],
            [str(uuid4()) for _ in range(501)],
        )
    with psycopg.connect(matrix_config.runtime_url) as runtime:
        for table_name in ("user_assets", "user_asset_refs"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(f"SELECT * FROM {table_name} LIMIT 1")
            runtime.rollback()


@pytest.mark.parametrize("actor_key", ["user_id", "inactive_id"])
def test_non_admin_and_inactive_super_admin_are_rejected(
    matrix_config: TenantRoleMatrixConfig,
    asset_facts: dict[str, str],
    actor_key: str,
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _call(
            matrix_config.runtime_url,
            asset_facts[actor_key],
            asset_facts["user_id"],
        )


def test_other_services_and_public_have_no_execute(
    matrix_config: TenantRoleMatrixConfig,
    asset_facts: dict[str, str],
) -> None:
    for database_url in (matrix_config.wecom_url, matrix_config.worker_url):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _call(
                database_url,
                asset_facts["admin_id"],
                asset_facts["user_id"],
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _call_download(
                database_url,
                asset_facts["admin_id"],
                asset_facts["user_id"],
                [asset_facts["asset_id"]],
            )
    with psycopg.connect(matrix_config.admin_url) as sync:
        sync.execute("SET SESSION AUTHORIZATION everydayai_sync")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            sync.execute(
                "SELECT list_platform_admin_user_assets("
                "%s, 'generated', 'image', 24, NULL, NULL)",
                (asset_facts["user_id"],),
            )
        sync.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            sync.execute(
                "SELECT resolve_platform_admin_user_assets_download("
                "%s, %s::JSONB)",
                (
                    asset_facts["user_id"],
                    Jsonb([asset_facts["asset_id"]]),
                ),
            )
    with psycopg.connect(matrix_config.admin_url) as admin:
        privileges = admin.execute(
            "SELECT has_function_privilege('public', %s, 'EXECUTE'), "
            "has_function_privilege('everydayai_sync', %s, 'EXECUTE'), "
            "has_function_privilege('public', %s, 'EXECUTE'), "
            "has_function_privilege('everydayai_sync', %s, 'EXECUTE')",
            (
                SIGNATURE,
                SIGNATURE,
                DOWNLOAD_SIGNATURE,
                DOWNLOAD_SIGNATURE,
            ),
        ).fetchone()
    assert privileges == (False, False, False, False)


@pytest.mark.parametrize(
    ("source_type", "media_type", "cursor_created_at", "cursor_id"),
    [
        ("invalid", "image", None, None),
        ("generated", "audio", None, None),
        ("generated", "image", "2026-07-27T00:00:00+00:00", None),
    ],
)
def test_query_argument_boundaries_are_preserved(
    matrix_config: TenantRoleMatrixConfig,
    asset_facts: dict[str, str],
    source_type: str,
    media_type: str,
    cursor_created_at: str | None,
    cursor_id: str | None,
) -> None:
    with pytest.raises(psycopg.DatabaseError) as exc_info:
        _call(
            matrix_config.runtime_url,
            asset_facts["admin_id"],
            asset_facts["user_id"],
            source_type=source_type,
            media_type=media_type,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
        )
    assert str(exc_info.value).startswith("ADMIN_ASSET_")
