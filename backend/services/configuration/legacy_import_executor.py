"""Single-transaction executor for migration 161 legacy import plans."""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any, Mapping

from services.configuration.legacy_import import LegacyImportPlan


MIGRATOR_ROLE = "everydayai_migrator"


class LegacyImportExecutionError(RuntimeError):
    """Stable failure before or during the atomic import transaction."""


def required_confirmation(import_id: str) -> str:
    """Return the exact operator acknowledgement required for apply."""
    return f"APPLY:{import_id}"


def apply_legacy_import(
    connection: Any,
    plan: LegacyImportPlan,
    *,
    confirmation: str,
) -> Mapping[str, object]:
    """Apply one complete plan using SET LOCAL and RPC in one transaction."""
    if confirmation != required_confirmation(plan.import_id):
        raise LegacyImportExecutionError(
            "LEGACY_IMPORT_CONFIRMATION_REQUIRED"
        )
    items = [item.database_value() for item in plan.items]
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SELECT session_user")
                role_row = cursor.fetchone()
                role = _first_value(role_row)
                if role != MIGRATOR_ROLE:
                    raise LegacyImportExecutionError(
                        "LEGACY_IMPORT_MIGRATOR_ROLE_REQUIRED"
                    )
                cursor.execute(
                    "SET LOCAL app.legacy_config_import = 'apply'"
                )
                cursor.execute(
                    "SELECT import_legacy_configuration_batch("
                    "%s, %s::jsonb)",
                    (plan.import_id, json.dumps(items)),
                )
                result = _first_value(cursor.fetchone())
                return _validate_result(result, plan)
    except LegacyImportExecutionError:
        raise
    except Exception as error:
        raise LegacyImportExecutionError(
            "LEGACY_IMPORT_DATABASE_FAILED"
        ) from error


def _first_value(row: object) -> object:
    if isinstance(row, Mapping):
        values = tuple(row.values())
    elif isinstance(row, (tuple, list)):
        values = tuple(row)
    else:
        raise LegacyImportExecutionError(
            "LEGACY_IMPORT_DATABASE_RESPONSE_INVALID"
        )
    if len(values) != 1:
        raise LegacyImportExecutionError(
            "LEGACY_IMPORT_DATABASE_RESPONSE_INVALID"
        )
    return values[0]


def _validate_result(
    result: object,
    plan: LegacyImportPlan,
) -> Mapping[str, object]:
    if not isinstance(result, Mapping):
        raise LegacyImportExecutionError(
            "LEGACY_IMPORT_DATABASE_RESPONSE_INVALID"
        )
    expected = {
        "import_id": plan.import_id,
        "imported_count": plan.item_count,
        "version": 1,
    }
    if dict(result) != expected:
        raise LegacyImportExecutionError(
            "LEGACY_IMPORT_DATABASE_RESPONSE_INVALID"
        )
    return MappingProxyType(dict(result))
