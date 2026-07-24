"""Read-only transaction adapter tests for the legacy export RPC."""

from __future__ import annotations

from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from services.configuration.legacy_import_source import (
    LegacyImportSourceError,
)
from services.configuration.legacy_import_source_executor import (
    read_legacy_import_snapshot,
)


def _connection(
    *,
    role: str = "everydayai_config_import_reader",
    exported: object | None = None,
):
    connection = MagicMock()
    connection.transaction.return_value = nullcontext()
    cursor = MagicMock()
    connection.cursor.return_value = nullcontext(cursor)
    cursor.fetchone.side_effect = [
        (role,),
        (exported or {
            "organizations": [],
            "org_configs": [],
            "external_credentials": [],
        },),
    ]
    return connection, cursor


def test_reader_uses_one_read_only_transaction_cursor_and_rpc() -> None:
    connection, cursor = _connection()

    snapshot = read_legacy_import_snapshot(
        connection,
        global_encrypt_key=None,
    )

    assert snapshot.organizations == ()
    connection.transaction.assert_called_once_with()
    connection.cursor.assert_called_once_with()
    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        "SET TRANSACTION READ ONLY",
        "SELECT session_user",
        "SET LOCAL app.legacy_config_export = 'read'",
        "SELECT export_legacy_configuration_snapshot()",
    ]


def test_wrong_source_role_stops_before_guc_and_rpc() -> None:
    connection, cursor = _connection(role="everydayai_migrator")

    with pytest.raises(
        LegacyImportSourceError,
        match="LEGACY_IMPORT_SOURCE_ROLE_REQUIRED",
    ):
        read_legacy_import_snapshot(
            connection,
            global_encrypt_key=None,
        )

    assert cursor.execute.call_count == 2


@pytest.mark.parametrize(
    "row",
    [None, (), ("one", "two"), {"one": 1, "two": 2}],
)
def test_malformed_cursor_rows_fail_closed(row: object) -> None:
    connection, cursor = _connection()
    cursor.fetchone.side_effect = [row]

    with pytest.raises(
        LegacyImportSourceError,
        match="LEGACY_IMPORT_EXPORT_RESPONSE_INVALID",
    ):
        read_legacy_import_snapshot(
            connection,
            global_encrypt_key=None,
        )


def test_export_result_must_be_an_object() -> None:
    connection, _ = _connection(exported="invalid")

    with pytest.raises(
        LegacyImportSourceError,
        match="LEGACY_IMPORT_EXPORT_RESPONSE_INVALID",
    ):
        read_legacy_import_snapshot(
            connection,
            global_encrypt_key=None,
        )


def test_database_error_is_normalized_without_details() -> None:
    connection, cursor = _connection()
    cursor.execute.side_effect = RuntimeError("database secret detail")

    with pytest.raises(
        LegacyImportSourceError,
        match="^LEGACY_IMPORT_EXPORT_DATABASE_FAILED$",
    ):
        read_legacy_import_snapshot(
            connection,
            global_encrypt_key=None,
        )
