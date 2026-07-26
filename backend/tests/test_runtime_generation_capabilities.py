"""Deployment gate for the Web runtime generation capability chain."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts.verify_runtime_generation_capabilities import (
    REQUIRED_FUNCTIONS,
    REQUIRED_SEQUENCE,
    RuntimeGenerationCapabilityError,
    verify_capabilities,
)


def _connection(
    *,
    role: str = "everydayai_runtime",
    missing_functions: list[str] | None = None,
    sequence_allowed: bool = True,
) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchone.side_effect = [(role,), (sequence_allowed,)]
    cursor.fetchall.return_value = [
        (signature,) for signature in (missing_functions or [])
    ]
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    return connection


def test_complete_runtime_generation_capability_chain_passes() -> None:
    verify_capabilities(_connection())


def test_wrong_database_role_fails_closed() -> None:
    with pytest.raises(
        RuntimeGenerationCapabilityError,
        match="RUNTIME_GENERATION_ROLE_INVALID",
    ):
        verify_capabilities(_connection(role="everydayai"))


def test_missing_helper_execution_fails_closed() -> None:
    with pytest.raises(
        RuntimeGenerationCapabilityError,
        match="RUNTIME_GENERATION_FUNCTION_CAPABILITY_MISSING",
    ):
        verify_capabilities(
            _connection(missing_functions=[REQUIRED_FUNCTIONS[1]])
        )


def test_missing_queue_sequence_usage_fails_closed() -> None:
    with pytest.raises(
        RuntimeGenerationCapabilityError,
        match="RUNTIME_GENERATION_SEQUENCE_CAPABILITY_MISSING",
    ):
        verify_capabilities(_connection(sequence_allowed=False))


def test_gate_checks_the_exact_owned_task_sequence() -> None:
    assert REQUIRED_SEQUENCE == "public.task_queue_sequence_seq"
