"""Deployment gate for the Web runtime generation capability chain."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts.verify_runtime_generation_capabilities import (
    EXPECTED_OWNER,
    PRIVATE_FUNCTIONS,
    PRIVATE_SEQUENCE,
    PUBLIC_FUNCTION,
    RuntimeGenerationCapabilityError,
    verify_capabilities,
)


def _connection(
    *,
    role: str = "everydayai_runtime",
    facade: tuple[str, bool, bool] | None = None,
    exposed_private: list[str] | None = None,
    sequence_exists: bool = True,
    sequence_exposed: bool = False,
) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        (role,),
        facade or (EXPECTED_OWNER, True, True),
        (sequence_exists, sequence_exposed),
    ]
    exposed = set(exposed_private or [])
    cursor.fetchall.return_value = [
        (signature, True, signature in exposed)
        for signature in PRIVATE_FUNCTIONS
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


def test_invalid_public_facade_fails_closed() -> None:
    with pytest.raises(
        RuntimeGenerationCapabilityError,
        match="RUNTIME_GENERATION_FACADE_INVALID",
    ):
        verify_capabilities(_connection(facade=(EXPECTED_OWNER, False, True)))


def test_exposed_private_helper_fails_closed() -> None:
    with pytest.raises(
        RuntimeGenerationCapabilityError,
        match="RUNTIME_GENERATION_PRIVATE_FUNCTION_EXPOSED",
    ):
        verify_capabilities(_connection(exposed_private=[PRIVATE_FUNCTIONS[1]]))


def test_exposed_queue_sequence_fails_closed() -> None:
    with pytest.raises(
        RuntimeGenerationCapabilityError,
        match="RUNTIME_GENERATION_PRIVATE_SEQUENCE_EXPOSED",
    ):
        verify_capabilities(_connection(sequence_exposed=True))


def test_gate_checks_exact_public_and_private_objects() -> None:
    assert PUBLIC_FUNCTION.startswith("public.prepare_generation(")
    assert PRIVATE_FUNCTIONS[0].startswith("public._prepare_generation_owner(")
    assert PRIVATE_SEQUENCE == "public.task_queue_sequence_seq"
