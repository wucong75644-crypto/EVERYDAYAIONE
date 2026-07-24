"""Single-transaction legacy import executor tests."""

from __future__ import annotations

from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from services.configuration.legacy_import import (
    LegacyImportItem,
    LegacyImportPlan,
)
from services.configuration.legacy_import_executor import (
    LegacyImportExecutionError,
    apply_legacy_import,
    required_confirmation,
)


IMPORT_ID = "00000000-0000-0000-0000-00000000a161"


def _plan() -> LegacyImportPlan:
    return LegacyImportPlan(
        import_id=IMPORT_ID,
        items=(
            LegacyImportItem(
                org_id="00000000-0000-0000-0000-000000000010",
                definition_version="v1",
                config_key="wecom.corp_id",
                value_json="corp-1",
                secret_envelope=None,
            ),
        ),
    )


def _connection(
    *,
    role: str = "everydayai_migrator",
    result: object | None = None,
):
    connection = MagicMock()
    connection.transaction.return_value = nullcontext()
    cursor = MagicMock()
    connection.cursor.return_value = nullcontext(cursor)
    cursor.fetchone.side_effect = [
        (role,),
        (result or {
            "import_id": IMPORT_ID,
            "imported_count": 1,
            "version": 1,
        },),
    ]
    return connection, cursor


def test_apply_uses_one_transaction_and_same_cursor_for_gate_and_rpc() -> None:
    connection, cursor = _connection()

    result = apply_legacy_import(
        connection,
        _plan(),
        confirmation=required_confirmation(IMPORT_ID),
    )

    connection.transaction.assert_called_once_with()
    connection.cursor.assert_called_once_with()
    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        "SELECT session_user",
        "SET LOCAL app.legacy_config_import = 'apply'",
        "SELECT import_legacy_configuration_batch(%s, %s::jsonb)",
    ]
    assert result["imported_count"] == 1
    payload = cursor.execute.call_args_list[2].args[1][1]
    assert '"config_key": "wecom.corp_id"' in payload


def test_confirmation_is_required_before_opening_transaction() -> None:
    connection, _ = _connection()

    with pytest.raises(
        LegacyImportExecutionError,
        match="LEGACY_IMPORT_CONFIRMATION_REQUIRED",
    ):
        apply_legacy_import(connection, _plan(), confirmation="")

    connection.transaction.assert_not_called()


def test_non_migrator_session_is_rejected_before_gate() -> None:
    connection, cursor = _connection(role="everydayai_runtime")

    with pytest.raises(
        LegacyImportExecutionError,
        match="LEGACY_IMPORT_MIGRATOR_ROLE_REQUIRED",
    ):
        apply_legacy_import(
            connection,
            _plan(),
            confirmation=required_confirmation(IMPORT_ID),
        )

    assert cursor.execute.call_count == 1


@pytest.mark.parametrize(
    "row",
    [None, (), ("one", "two"), {"one": 1, "two": 2}],
)
def test_malformed_cursor_rows_fail_closed(row: object) -> None:
    connection, cursor = _connection()
    cursor.fetchone.side_effect = [row]

    with pytest.raises(
        LegacyImportExecutionError,
        match="LEGACY_IMPORT_DATABASE_RESPONSE_INVALID",
    ):
        apply_legacy_import(
            connection,
            _plan(),
            confirmation=required_confirmation(IMPORT_ID),
        )


def test_database_error_is_normalized_without_details() -> None:
    connection, cursor = _connection()
    cursor.execute.side_effect = RuntimeError("database secret detail")

    with pytest.raises(
        LegacyImportExecutionError,
        match="^LEGACY_IMPORT_DATABASE_FAILED$",
    ):
        apply_legacy_import(
            connection,
            _plan(),
            confirmation=required_confirmation(IMPORT_ID),
        )


def test_result_must_exactly_match_plan() -> None:
    connection, _ = _connection(result={
        "import_id": IMPORT_ID,
        "imported_count": 2,
        "version": 1,
    })

    with pytest.raises(
        LegacyImportExecutionError,
        match="LEGACY_IMPORT_DATABASE_RESPONSE_INVALID",
    ):
        apply_legacy_import(
            connection,
            _plan(),
            confirmation=required_confirmation(IMPORT_ID),
        )


def test_result_must_be_an_object() -> None:
    connection, _ = _connection(result="not-an-object")

    with pytest.raises(
        LegacyImportExecutionError,
        match="LEGACY_IMPORT_DATABASE_RESPONSE_INVALID",
    ):
        apply_legacy_import(
            connection,
            _plan(),
            confirmation=required_confirmation(IMPORT_ID),
        )
