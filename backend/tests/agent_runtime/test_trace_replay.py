"""Tests for deterministic event, state, and projection replay."""

from __future__ import annotations

from copy import deepcopy

import pytest

from tests.agent_runtime.trace_assertions import (
    assert_deterministic,
    assert_expected_outcome,
)
from tests.agent_runtime.trace_replay import (
    replay_events,
    replay_projection,
    replay_trace,
)
from tests.agent_runtime.trace_schema import load_trace_manifest


def _by_scenario() -> dict[str, dict]:
    return {bundle["scenario"]: bundle for bundle in load_trace_manifest()}


def test_every_fixture_is_deterministic_and_matches_expected_result() -> None:
    for bundle in load_trace_manifest():
        assert_deterministic(bundle)
        assert_expected_outcome(bundle, replay_trace(bundle))


def test_duplicate_event_is_ignored_by_event_identity() -> None:
    bundle = _by_scenario()["duplicate runtime event"]

    result = replay_events(bundle["runtime_events"])

    assert result["outcome"] == "completed"
    assert result["last_sequence"] == 1
    assert result["duplicate_count"] == 1


def test_same_event_identity_with_changed_content_is_conflict() -> None:
    event = deepcopy(_by_scenario()["duplicate runtime event"]["runtime_events"][0])
    changed = deepcopy(event)
    changed["state"] = "failed"

    for events in ([event, changed], [changed, event]):
        result = replay_events(events)

        assert result["outcome"] == "event_id_conflict"
        assert result["conflicts"] == ["event_id_conflict"]
        assert result["duplicate_count"] == 0


def test_same_sequence_with_different_event_identity_is_conflict() -> None:
    event = deepcopy(_by_scenario()["duplicate runtime event"]["runtime_events"][0])
    changed = deepcopy(event)
    changed["event_id"] = "event-sequence-conflict"

    for events in ([event, changed], [changed, event]):
        result = replay_events(events)

        assert result["outcome"] == "sequence_conflict"
        assert result["conflicts"] == ["sequence_conflict"]


def test_reordered_event_requires_replay() -> None:
    bundle = _by_scenario()["runtime event reorder"]

    result = replay_events(bundle["runtime_events"])

    assert result["outcome"] == "replay_required"
    assert result["last_sequence"] == 2
    assert result["missing_sequences"] == []


def test_event_gap_reports_exact_missing_sequence() -> None:
    bundle = _by_scenario()["runtime event gap"]

    result = replay_events(bundle["runtime_events"])

    assert result["outcome"] == "gap"
    assert result["last_sequence"] == 1
    assert result["missing_sequences"] == [2]


def test_projection_replay_is_order_and_duplicate_stable() -> None:
    records = _by_scenario()["projection replay"]["projection_records"]

    forward = replay_projection(records)
    reversed_result = replay_projection(list(reversed(records)))

    assert forward == reversed_result == {
        "action_status": "completed",
        "run_status": "completed",
    }


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"value": "failed"}, "PROJECTION_RECORD_CONFLICT"),
        (
            {"record_id": "projection-conflicting-id", "value": "failed"},
            "PROJECTION_SEQUENCE_CONFLICT",
        ),
    ],
)
def test_projection_conflicts_fail_closed_in_both_orders(
    changes,
    error,
) -> None:
    record = {
        "record_id": "projection-conflict",
        "sequence": 1,
        "key": "run_status",
        "value": "completed",
    }
    changed = deepcopy(record)
    changed.update(changes)

    for records in ([record, changed], [changed, record]):
        with pytest.raises(ValueError, match=error):
            replay_projection(records)


def test_identical_projection_record_is_idempotent() -> None:
    record = {
        "record_id": "projection-identical",
        "sequence": 1,
        "key": "run_status",
        "value": "completed",
    }

    assert replay_projection([record, deepcopy(record)]) == {
        "run_status": "completed"
    }
