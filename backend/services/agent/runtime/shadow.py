"""Side-effect-free comparison of frozen runtime facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, kw_only=True)
class ShadowMismatch:
    category: str
    expected: str
    actual: str
    details: Mapping[str, object]


def compare_runtime_facts(
    *, definition_hash: str, expected_definition_hash: str,
    toolset_hash: str, expected_toolset_hash: str,
    policy_hash: str, expected_policy_hash: str,
    arguments_hash: str, expected_arguments_hash: str,
    executor_type: str, expected_executor_type: str,
    projection: Mapping[str, object], expected_projection: Mapping[str, object],
) -> tuple[ShadowMismatch, ...]:
    checks = (
        ("definition", expected_definition_hash, definition_hash),
        ("toolset", expected_toolset_hash, toolset_hash),
        ("policy", expected_policy_hash, policy_hash),
        ("arguments", expected_arguments_hash, arguments_hash),
        ("executor", expected_executor_type, executor_type),
        ("projection", expected_projection, projection),
    )
    result: list[ShadowMismatch] = []
    for category, expected, actual in checks:
        if expected != actual:
            result.append(ShadowMismatch(
                category=category, expected=str(expected), actual=str(actual),
                details={"side_effects": False, "model_calls": 0,
                         "provider_submissions": 0},
            ))
    return tuple(result)


__all__ = ["ShadowMismatch", "compare_runtime_facts"]
