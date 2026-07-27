"""Tests for owner, fencing, and scope assertions."""

from __future__ import annotations

from copy import deepcopy

from tests.agent_runtime.trace_assertions import (
    assert_fencing,
    assert_scope,
    assert_single_owner,
)
from tests.agent_runtime.trace_replay import replay_trace
from tests.agent_runtime.trace_schema import load_trace_manifest


def _by_scenario() -> dict[str, dict]:
    return {bundle["scenario"]: bundle for bundle in load_trace_manifest()}


def test_single_owner_detects_duplicate_terminal() -> None:
    bundle = _by_scenario()["user cancellation races completion"]

    assert_single_owner(replay_trace(bundle))


def test_single_owner_detects_duplicate_side_effect() -> None:
    bundle = deepcopy(_by_scenario()["synchronous readonly tool"])
    bundle["trace"].insert(-1, {
        "op": "side_effect",
        "identity": "effect-read",
        "effect": "readonly_result",
        "fencing_revision": 1,
    })

    assert_single_owner(replay_trace(bundle))


def test_fencing_detects_old_token_submission() -> None:
    bundle = deepcopy(_by_scenario()["ordinary text response"])
    bundle["trace"].extend([
        {"op": "fence", "revision": 2},
        {
            "op": "side_effect",
            "identity": "stale-effect",
            "effect": "must-not-commit",
            "fencing_revision": 1,
        },
    ])

    result = replay_trace(bundle)

    assert_fencing(result)
    assert "stale-effect" not in result["side_effects"]


def test_scope_assertions_cover_enterprise_personal_and_conflict() -> None:
    bundles = _by_scenario()

    assert_scope(bundles["enterprise employee scope"])
    assert_scope(bundles["personal null organization scope"])
    assert (
        bundles["personal null organization scope"]["session_scope"]["scope_id"]
        != bundles["personal null organization scope"]["session_scope"]["user_id"]
    )
    assert_scope(bundles["actorless system scope"])
    assert_scope(bundles["scope conflict rejected"])


def test_duplicate_command_returns_existing_without_rejection() -> None:
    result = replay_trace(_by_scenario()["duplicate command"])

    assert result["outcome"] == "existing"
    assert result["rejections"] == []
    assert list(result["command_records"]) == ["idem-duplicate"]
    assert result["side_effects"] == []


def test_idempotency_key_collision_rejects_changed_request() -> None:
    bundle = deepcopy(_by_scenario()["duplicate command"])
    bundle["trace"][1] = {
        "op": "command",
        "command_id": "command-collision",
        "idempotency_key": "idem-duplicate",
        "command_type": "message.submit",
        "request_hash": "sha256:changed-request",
        "scope": {
            "user_id": "user-test",
            "org_id": None,
            "scope_kind": "user",
            "scope_id": "user-test",
        },
    }

    result = replay_trace(bundle)

    assert result["outcome"] == "rejected"
    assert result["rejections"] == ["idempotency_conflict"]
    assert len(result["command_records"]) == 1


def test_idempotency_key_collision_rejects_changed_scope() -> None:
    bundle = deepcopy(_by_scenario()["duplicate command"])
    bundle["trace"][1] = {
        "op": "command",
        "command_id": "command-scope-collision",
        "idempotency_key": "idem-duplicate",
        "command_type": "message.submit",
        "request_hash": "sha256:duplicate-request",
        "scope": {
            "user_id": "user-test",
            "org_id": "org-other",
            "scope_kind": "user",
            "scope_id": "user-test",
        },
    }

    result = replay_trace(bundle)

    assert result["outcome"] == "rejected"
    assert result["rejections"] == ["idempotency_conflict"]
