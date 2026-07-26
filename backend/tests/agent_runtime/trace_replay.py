"""Pure, deterministic replay for test-only Runtime trace protocols."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from tests.agent_runtime.trace_schema import validate_trace_bundle


def replay_trace(bundle: dict[str, Any]) -> dict[str, Any]:
    """Replay state, ownership, events, and projection without external IO."""
    validate_trace_bundle(bundle)
    state = deepcopy(bundle["initial_state"])
    ledger = {
        "terminals": {},
        "side_effects": {},
        "commands": set(),
        "rejections": [],
    }
    fencing_revision = 0
    outcome = "accepted"
    for step in bundle["trace"]:
        fencing_revision, outcome = _replay_step(
            step,
            state,
            fencing_revision,
            ledger,
        )

    event_result = replay_events(bundle["runtime_events"])
    projection = replay_projection(bundle["projection_records"])
    event_outcome = event_result["outcome"]
    if event_outcome != "completed":
        outcome = event_outcome
    terminal_state = _terminal_state(ledger, state)
    if outcome == "accepted" and terminal_state is not None:
        outcome = terminal_state if terminal_state == "failed" else "completed"
    return {
        "state": state,
        "terminal_state": terminal_state,
        "outcome": outcome,
        "rejections": ledger["rejections"],
        "side_effects": sorted(ledger["side_effects"]),
        "event_result": event_result,
        "projection": projection,
    }


def replay_events(
    events: list[dict[str, Any]],
    *,
    after_sequence: int = 0,
) -> dict[str, Any]:
    """Apply contiguous events, ignore duplicates, and expose gaps/reorder."""
    last_sequence = after_sequence
    seen: set[str] = set()
    applied: list[str] = []
    missing: set[int] = set()
    future_seen = False
    for event in events:
        event_id = event["event_id"]
        if event_id in seen:
            continue
        seen.add(event_id)
        sequence = event["sequence"]
        expected = last_sequence + 1
        if sequence == expected:
            last_sequence = sequence
            applied.append(event_id)
            missing.discard(sequence)
        elif sequence > expected:
            missing.update(range(expected, sequence))
            future_seen = True
        elif sequence <= last_sequence:
            future_seen = True
    outcome = "completed"
    if missing:
        outcome = "gap" if not _gap_arrived_later(events, missing) else "replay_required"
    elif future_seen:
        outcome = "replay_required"
    return {
        "outcome": outcome,
        "last_sequence": last_sequence,
        "missing_sequences": sorted(missing),
        "applied_event_ids": applied,
        "duplicate_count": len(events) - len(seen),
    }


def replay_projection(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce projection records with stable last-write-by-sequence semantics."""
    projection: dict[str, Any] = {}
    seen: set[str] = set()
    ordered = sorted(records, key=lambda item: (item["sequence"], item["record_id"]))
    for record in ordered:
        if record["record_id"] in seen:
            continue
        seen.add(record["record_id"])
        key = str(record["key"])
        projection[key] = deepcopy(record["value"])
    return projection


def _apply_fence(step: dict[str, Any], current: int) -> int:
    revision = step.get("revision")
    if not isinstance(revision, int) or revision <= current:
        raise ValueError("TRACE_FENCING_REVISION_INVALID")
    return revision


def _replay_step(
    step: dict[str, Any],
    state: dict[str, Any],
    fencing_revision: int,
    ledger: dict[str, Any],
) -> tuple[int, str]:
    operation = step.get("op")
    handler = _STEP_HANDLERS.get(str(operation))
    if handler is None:
        raise ValueError(f"TRACE_OPERATION_UNSUPPORTED:{operation}")
    return handler(step, state, fencing_revision, ledger)


def _step_fence(
    step: dict[str, Any],
    _state: dict[str, Any],
    fencing_revision: int,
    _ledger: dict[str, Any],
) -> tuple[int, str]:
    return _apply_fence(step, fencing_revision), "accepted"


def _step_transition(
    step: dict[str, Any],
    state: dict[str, Any],
    fencing_revision: int,
    ledger: dict[str, Any],
) -> tuple[int, str]:
    return (
        fencing_revision,
        _apply_transition(step, state, fencing_revision, ledger),
    )


def _step_terminal(
    step: dict[str, Any],
    _state: dict[str, Any],
    fencing_revision: int,
    ledger: dict[str, Any],
) -> tuple[int, str]:
    return (
        fencing_revision,
        _claim_once("terminals", step, fencing_revision, ledger),
    )


def _step_side_effect(
    step: dict[str, Any],
    _state: dict[str, Any],
    fencing_revision: int,
    ledger: dict[str, Any],
) -> tuple[int, str]:
    return (
        fencing_revision,
        _claim_once("side_effects", step, fencing_revision, ledger),
    )


def _step_command(
    step: dict[str, Any],
    _state: dict[str, Any],
    fencing_revision: int,
    ledger: dict[str, Any],
) -> tuple[int, str]:
    return fencing_revision, _apply_command(step, ledger)


def _step_reject(
    step: dict[str, Any],
    _state: dict[str, Any],
    fencing_revision: int,
    ledger: dict[str, Any],
) -> tuple[int, str]:
    ledger["rejections"].append(str(step["code"]))
    return fencing_revision, "rejected"


def _step_transient(
    step: dict[str, Any],
    _state: dict[str, Any],
    fencing_revision: int,
    _ledger: dict[str, Any],
) -> tuple[int, str]:
    return fencing_revision, str(step.get("outcome") or step["op"])


def _apply_transition(
    step: dict[str, Any],
    state: dict[str, Any],
    fencing_revision: int,
    ledger: dict[str, Any],
) -> str:
    rejection = _fencing_rejection(step, fencing_revision)
    if rejection:
        ledger["rejections"].append(rejection)
        return "rejected"
    aggregate_id = str(step["aggregate_id"])
    current = state.get(aggregate_id)
    if current != step.get("from"):
        ledger["rejections"].append("state_conflict")
        return "rejected"
    state[aggregate_id] = step["to"]
    return "accepted"


def _claim_once(
    bucket: str,
    step: dict[str, Any],
    fencing_revision: int,
    ledger: dict[str, Any],
) -> str:
    rejection = _fencing_rejection(step, fencing_revision)
    if rejection:
        ledger["rejections"].append(rejection)
        return "rejected"
    identity = str(step["identity"])
    value = str(step.get("state") or step.get("effect"))
    existing = ledger[bucket].get(identity)
    if existing is not None:
        ledger["rejections"].append(f"duplicate_{bucket[:-1]}")
        return "rejected"
    ledger[bucket][identity] = value
    return "accepted"


def _apply_command(step: dict[str, Any], ledger: dict[str, Any]) -> str:
    command_id = str(step["command_id"])
    if command_id in ledger["commands"]:
        ledger["rejections"].append("duplicate_command")
        return "rejected"
    ledger["commands"].add(command_id)
    return "accepted"


def _fencing_rejection(
    step: dict[str, Any],
    current_revision: int,
) -> str | None:
    supplied = step.get("fencing_revision")
    if supplied is not None and supplied != current_revision:
        return "stale_fencing_token"
    return None


def _terminal_state(
    ledger: dict[str, Any],
    state: dict[str, Any],
) -> str | None:
    if ledger["terminals"]:
        return next(reversed(ledger["terminals"].values()))
    for value in reversed(tuple(state.values())):
        if value in {"completed", "failed", "cancelled"}:
            return value
    return None


def _gap_arrived_later(
    events: list[dict[str, Any]],
    missing: set[int],
) -> bool:
    return any(event["sequence"] in missing for event in events)


_STEP_HANDLERS = {
    "fence": _step_fence,
    "transition": _step_transition,
    "terminal": _step_terminal,
    "side_effect": _step_side_effect,
    "command": _step_command,
    "reject": _step_reject,
    "disconnect": _step_transient,
    "restart": _step_transient,
    "lease_lost": _step_transient,
    "placeholder": _step_transient,
}
