"""Reusable assertions for deterministic Runtime trace fixtures."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from tests.agent_runtime.trace_replay import replay_trace
from tests.agent_runtime.trace_schema import validate_trace_bundle


def assert_deterministic(bundle: dict[str, Any]) -> None:
    """Assert repeat replay and input immutability."""
    original = deepcopy(bundle)
    first = replay_trace(bundle)
    second = replay_trace(bundle)
    assert first == second
    assert bundle == original


def assert_single_owner(result: dict[str, Any]) -> None:
    """Require duplicate terminal/side-effect attempts to be observable."""
    duplicate_codes = {
        "duplicate_terminal",
        "duplicate_side_effect",
    }
    assert duplicate_codes.intersection(result["rejections"])


def assert_fencing(result: dict[str, Any]) -> None:
    """Require a stale owner submission to be rejected."""
    assert "stale_fencing_token" in result["rejections"]


def assert_scope(bundle: dict[str, Any]) -> None:
    """Validate that a presented Scope does not drift from the frozen Scope."""
    validate_trace_bundle(bundle)
    scope = bundle["session_scope"]
    expected = bundle["expected"]
    if expected["outcome"] == "rejected":
        assert expected["rejection_code"] == "scope_conflict"
        [rejection] = [
            step for step in bundle["trace"]
            if step.get("op") == "reject"
        ]
        assert rejection["code"] == "scope_conflict"
        frozen = {
            field: scope[field]
            for field in ("user_id", "org_id", "scope_kind", "scope_id")
        }
        assert rejection["presented_scope"] != frozen


def assert_expected_outcome(
    bundle: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Compare the stable observable result declared by a fixture."""
    expected = bundle["expected"]
    assert result["terminal_state"] == expected["terminal_state"]
    assert result["outcome"] == expected["outcome"]
    assert result["event_result"]["missing_sequences"] == (
        expected["missing_sequences"]
    )
    assert result["projection"] == expected["projection"]
    assert result["side_effects"] == sorted(expected["side_effects"])
    rejection = expected["rejection_code"]
    if rejection is not None:
        assert rejection in result["rejections"]
