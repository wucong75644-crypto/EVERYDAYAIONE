"""Single-read-only-transaction adapter for the legacy export capability."""

from __future__ import annotations

from typing import Any, Mapping

from services.configuration.legacy_import_source import (
    LegacyImportSnapshot,
    LegacyImportSourceError,
    LegacyImportSourceReader,
)


SOURCE_READER_ROLE = "everydayai_config_import_reader"


def read_legacy_import_snapshot(
    connection: Any,
    *,
    global_encrypt_key: str | None,
) -> LegacyImportSnapshot:
    """Read and parse one export RPC result inside a read-only transaction."""
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SELECT session_user")
                role = _first_value(cursor.fetchone())
                if role != SOURCE_READER_ROLE:
                    raise LegacyImportSourceError(
                        "LEGACY_IMPORT_SOURCE_ROLE_REQUIRED"
                    )
                cursor.execute(
                    "SET LOCAL app.legacy_config_export = 'read'"
                )
                cursor.execute(
                    "SELECT export_legacy_configuration_snapshot()"
                )
                exported = _first_value(cursor.fetchone())
                if not isinstance(exported, Mapping):
                    raise LegacyImportSourceError(
                        "LEGACY_IMPORT_EXPORT_RESPONSE_INVALID"
                    )
                return LegacyImportSourceReader(
                    None,
                    global_encrypt_key=global_encrypt_key,
                ).read_export(exported)
    except LegacyImportSourceError:
        raise
    except Exception as error:
        raise LegacyImportSourceError(
            "LEGACY_IMPORT_EXPORT_DATABASE_FAILED"
        ) from error


def _first_value(row: object) -> object:
    if isinstance(row, Mapping):
        values = tuple(row.values())
    elif isinstance(row, (tuple, list)):
        values = tuple(row)
    else:
        raise LegacyImportSourceError(
            "LEGACY_IMPORT_EXPORT_RESPONSE_INVALID"
        )
    if len(values) != 1:
        raise LegacyImportSourceError(
            "LEGACY_IMPORT_EXPORT_RESPONSE_INVALID"
        )
    return values[0]
