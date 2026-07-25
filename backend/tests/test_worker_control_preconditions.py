"""Worker Control 迁移部署前置检查。"""

from unittest.mock import MagicMock

import pytest

from scripts.verify_worker_control_preconditions import (
    WorkerControlPreconditionError,
    verify_preconditions,
)


def _connection(
    tables: list[tuple[str, str]],
    sequences: list[tuple[str, str]],
    grants: list[tuple[str, str, str]] | None = None,
) -> MagicMock:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [tables, sequences, grants or []]
    return connection


def test_preconditions_accept_exact_owner_for_tables_and_sequences() -> None:
    owner = "everydayai_owner"
    connection = _connection(
        [
            ("error_logs", owner),
            ("knowledge_metrics", owner),
            ("scheduled_tasks", owner),
            ("scheduled_task_runs", owner),
        ],
        [("error_logs_id_seq", owner)],
    )

    verify_preconditions(connection)

    connection.rollback.assert_called_once()


def test_preconditions_report_missing_and_legacy_owned_objects() -> None:
    connection = _connection(
        [
            ("error_logs", "everydayai"),
            ("knowledge_metrics", "everydayai_owner"),
            ("scheduled_tasks", "everydayai_owner"),
        ],
        [("error_logs_id_seq", "everydayai")],
    )

    with pytest.raises(
        WorkerControlPreconditionError,
        match="WORKER_CONTROL_OWNERSHIP_INCOMPLETE",
    ) as error:
        verify_preconditions(connection)

    assert "error_logs=everydayai" in str(error.value)
    assert "scheduled_task_runs=missing" in str(error.value)
    assert "error_logs_id_seq=everydayai" in str(error.value)


def test_preconditions_reject_service_role_direct_table_grants() -> None:
    owner = "everydayai_owner"
    connection = _connection(
        [
            ("error_logs", owner),
            ("knowledge_metrics", owner),
            ("scheduled_tasks", owner),
            ("scheduled_task_runs", owner),
        ],
        [("error_logs_id_seq", owner)],
        [("everydayai_worker", "error_logs", "INSERT")],
    )

    with pytest.raises(
        WorkerControlPreconditionError,
        match="everydayai_worker.error_logs=INSERT",
    ):
        verify_preconditions(connection)
