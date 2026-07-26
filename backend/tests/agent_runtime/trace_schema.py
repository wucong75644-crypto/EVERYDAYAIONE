"""Validation and loading for test-only Agent Runtime trace bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "agent_runtime"
REQUIRED_TOP_LEVEL = {
    "schema_version",
    "scenario",
    "clock",
    "session_scope",
    "command",
    "identities",
    "initial_state",
    "trace",
    "runtime_events",
    "projection_records",
    "references",
    "expected",
}
SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "password",
    "prompt",
    "raw_content",
    "refresh_token",
    "secret",
}
TERMINAL_STATES = {"completed", "failed", "cancelled", None}
OUTCOMES = {
    "accepted",
    "completed",
    "event_id_conflict",
    "existing",
    "failed",
    "gap",
    "lease_lost",
    "rejected",
    "replay_required",
    "sequence_conflict",
    "unknown",
}
TRACE_STEP_FIELDS = {
    "fence": {"op", "revision"},
    "transition": {
        "op", "aggregate_id", "from", "to", "fencing_revision"
    },
    "terminal": {"op", "identity", "state", "fencing_revision"},
    "side_effect": {"op", "identity", "effect", "fencing_revision"},
    "reject": {"op", "code", "presented_scope"},
    "disconnect": {"op", "outcome"},
    "restart": {"op", "outcome"},
    "lease_lost": {"op", "outcome"},
    "placeholder": {"op", "outcome"},
}


def load_trace_bundle(path: Path) -> dict[str, Any]:
    """Load and validate one fixture without mutating it."""
    bundle = json.loads(path.read_text(encoding="utf-8"))
    validate_trace_bundle(bundle)
    return bundle


def load_trace_manifest() -> tuple[dict[str, Any], ...]:
    """Load every fixture named by the manifest after set-equality checks."""
    manifest_path = FIXTURE_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = manifest.get("fixtures")
    if not isinstance(names, list) or not names:
        raise ValueError("TRACE_MANIFEST_FIXTURES_REQUIRED")
    if len(names) != len(set(names)):
        raise ValueError("TRACE_MANIFEST_DUPLICATE_FIXTURE")
    disk_names = {
        path.name for path in FIXTURE_DIR.glob("*.json")
        if path.name != manifest_path.name
    }
    if set(names) != disk_names:
        raise ValueError("TRACE_MANIFEST_SET_MISMATCH")
    return tuple(load_trace_bundle(FIXTURE_DIR / name) for name in names)


def validate_trace_bundle(bundle: dict[str, Any]) -> None:
    """Fail closed when a bundle cannot express a deterministic replay."""
    if not isinstance(bundle, dict):
        raise ValueError("TRACE_BUNDLE_OBJECT_REQUIRED")
    if set(bundle) != REQUIRED_TOP_LEVEL:
        raise ValueError("TRACE_BUNDLE_TOP_LEVEL_FIELDS_INVALID")
    if bundle["schema_version"] != 1:
        raise ValueError("TRACE_SCHEMA_VERSION_UNSUPPORTED")
    _require_text(bundle, "scenario")
    _validate_clock(bundle["clock"])
    _validate_scope(bundle["session_scope"])
    _validate_command(bundle["command"])
    _validate_identities(bundle["identities"])
    if not isinstance(bundle["initial_state"], dict):
        raise ValueError("TRACE_INITIAL_STATE_INVALID")
    _validate_trace_steps(bundle["trace"])
    _validate_events(bundle["runtime_events"])
    _validate_projection_records(bundle["projection_records"])
    _validate_references(bundle["references"])
    _validate_expected(bundle["expected"])
    _reject_sensitive_keys(bundle)


def _validate_clock(clock: Any) -> None:
    if not isinstance(clock, dict) or set(clock) != {"started_at", "ticks_ms"}:
        raise ValueError("TRACE_CLOCK_INVALID")
    _require_text(clock, "started_at")
    ticks = clock["ticks_ms"]
    if not isinstance(ticks, list) or any(
        not isinstance(tick, int) or tick < 0 for tick in ticks
    ):
        raise ValueError("TRACE_CLOCK_TICKS_INVALID")


def _validate_scope(scope: Any) -> None:
    required = {"session_id", "user_id", "org_id", "scope_kind", "scope_id"}
    if not isinstance(scope, dict) or set(scope) != required:
        raise ValueError("TRACE_SCOPE_FIELDS_INVALID")
    _require_text(scope, "session_id")
    _validate_scope_identity({
        field: scope[field]
        for field in ("user_id", "org_id", "scope_kind", "scope_id")
    })


def _validate_scope_identity(scope: Any) -> None:
    required = {"user_id", "org_id", "scope_kind", "scope_id"}
    if not isinstance(scope, dict) or set(scope) != required:
        raise ValueError("TRACE_SCOPE_IDENTITY_FIELDS_INVALID")
    kind = scope["scope_kind"]
    if kind not in {"user", "channel", "system"}:
        raise ValueError("TRACE_SCOPE_KIND_INVALID")
    for field in ("user_id", "org_id"):
        value = scope[field]
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            raise ValueError(f"TRACE_SCOPE_IDENTITY_INVALID:{field}")
    _require_nonblank_text(scope, "scope_id")
    if kind == "user" and scope["user_id"] is None:
        raise ValueError("TRACE_USER_SCOPE_USER_REQUIRED")
    if kind == "channel" and scope["org_id"] is None:
        raise ValueError("TRACE_CHANNEL_SCOPE_ORG_REQUIRED")


def _validate_command(command: Any) -> None:
    required = {
        "command_id", "idempotency_key", "command_type", "request_hash"
    }
    if not isinstance(command, dict) or set(command) != required:
        raise ValueError("TRACE_COMMAND_FIELDS_INVALID")
    for field in required:
        _require_text(command, field)


def _validate_identities(identities: Any) -> None:
    if not isinstance(identities, dict) or set(identities) != {
        "runs", "model_steps", "actions"
    }:
        raise ValueError("TRACE_IDENTITIES_INVALID")
    all_ids: list[str] = []
    for values in identities.values():
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise ValueError("TRACE_IDENTITY_LIST_INVALID")
        all_ids.extend(values)
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("TRACE_IDENTITY_DUPLICATE")


def _validate_records(records: Any, code: str) -> None:
    if not isinstance(records, list) or any(
        not isinstance(record, dict) for record in records
    ):
        raise ValueError(f"{code}_LIST_INVALID")


def _validate_trace_steps(steps: Any) -> None:
    _validate_records(steps, "TRACE_STEP")
    for step in steps:
        operation = step.get("op")
        if operation == "command":
            _validate_command_step(step)
            continue
        fields = TRACE_STEP_FIELDS.get(operation)
        if fields is None:
            raise ValueError(f"TRACE_OPERATION_UNSUPPORTED:{operation}")
        if set(step) != fields:
            raise ValueError(f"TRACE_STEP_FIELDS_INVALID:{operation}")
        if operation == "reject":
            _validate_scope_identity(step["presented_scope"])


def _validate_command_step(step: dict[str, Any]) -> None:
    if set(step) == {"op", "command_ref"}:
        if step["command_ref"] != "top_level":
            raise ValueError("TRACE_COMMAND_REFERENCE_INVALID")
        return
    required = {
        "op", "command_id", "idempotency_key", "command_type",
        "request_hash", "scope",
    }
    if set(step) != required:
        raise ValueError("TRACE_COMMAND_STEP_FIELDS_INVALID")
    for field in (
        "command_id", "idempotency_key", "command_type", "request_hash"
    ):
        _require_text(step, field)
    _validate_scope_identity(step["scope"])


def _validate_projection_records(records: Any) -> None:
    _validate_records(records, "PROJECTION_RECORD")
    for record in records:
        if set(record) != {"record_id", "sequence", "key", "value"}:
            raise ValueError("PROJECTION_RECORD_FIELDS_INVALID")
        for field in ("record_id", "key"):
            _require_text(record, field)
        if not isinstance(record["sequence"], int) or record["sequence"] < 1:
            raise ValueError("PROJECTION_RECORD_SEQUENCE_INVALID")


def _validate_events(events: Any) -> None:
    _validate_records(events, "RUNTIME_EVENT")
    for event in events:
        required = {
            "event_id", "sequence", "event_type", "aggregate",
            "state", "fencing_revision",
        }
        if set(event) != required:
            raise ValueError("RUNTIME_EVENT_FIELDS_INVALID")
        _require_text(event, "event_id")
        _require_text(event, "event_type")
        if not isinstance(event["sequence"], int) or event["sequence"] < 1:
            raise ValueError("RUNTIME_EVENT_SEQUENCE_INVALID")
        if not isinstance(event["fencing_revision"], int):
            raise ValueError("RUNTIME_EVENT_FENCING_INVALID")
        aggregate = event["aggregate"]
        if not isinstance(aggregate, dict) or set(aggregate) != {"type", "id"}:
            raise ValueError("RUNTIME_EVENT_AGGREGATE_INVALID")


def _validate_references(references: Any) -> None:
    if not isinstance(references, dict) or set(references) != {
        "artifacts", "usage"
    }:
        raise ValueError("TRACE_REFERENCES_INVALID")
    _validate_records(references["artifacts"], "ARTIFACT_REFERENCE")
    _validate_records(references["usage"], "USAGE_REFERENCE")
    for artifact in references["artifacts"]:
        _require_text(artifact, "artifact_id")
        _require_text(artifact, "content_hash")
    for usage in references["usage"]:
        _require_text(usage, "usage_id")


def _validate_expected(expected: Any) -> None:
    required = {
        "terminal_state", "outcome", "rejection_code",
        "missing_sequences", "projection", "side_effects",
    }
    if not isinstance(expected, dict) or set(expected) != required:
        raise ValueError("TRACE_EXPECTED_FIELDS_INVALID")
    if expected["terminal_state"] not in TERMINAL_STATES:
        raise ValueError("TRACE_EXPECTED_TERMINAL_INVALID")
    if expected["outcome"] not in OUTCOMES:
        raise ValueError("TRACE_EXPECTED_OUTCOME_INVALID")
    if not isinstance(expected["missing_sequences"], list):
        raise ValueError("TRACE_EXPECTED_MISSING_INVALID")
    if not isinstance(expected["projection"], dict):
        raise ValueError("TRACE_EXPECTED_PROJECTION_INVALID")
    if not isinstance(expected["side_effects"], list):
        raise ValueError("TRACE_EXPECTED_SIDE_EFFECTS_INVALID")


def _reject_sensitive_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.lower() in SENSITIVE_KEYS:
                raise ValueError(f"TRACE_SENSITIVE_FIELD:{key}")
            _reject_sensitive_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_sensitive_keys(nested)


def _require_text(value: dict[str, Any], field: str) -> None:
    if not isinstance(value.get(field), str) or not value[field]:
        raise ValueError(f"TRACE_TEXT_REQUIRED:{field}")


def _require_nonblank_text(value: dict[str, Any], field: str) -> None:
    text = value.get(field)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"TRACE_NONBLANK_TEXT_REQUIRED:{field}")
