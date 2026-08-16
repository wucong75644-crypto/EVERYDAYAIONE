"""Shared contracts for professional Runtime Executors.

This module deliberately contains no database, workspace, Redis, Secret or
provider access.  Executors receive immutable action facts and narrow
capabilities from the Runtime dispatch path.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Mapping

from services.agent.runtime.domain import ActionAttempt, RuntimeScope


_INTERNAL_REQUEST_KEYS = frozenset({
    "_dispatch_context", "external_idempotency_key",
})
_SENSITIVE_KEY = re.compile(
    r"(?:secret|token|password|credential|api[_-]?key|authorization|cookie|"
    r"private[_-]?key|storage[_-]?ref|internal[_-]?path)", re.I,
)
_ABSOLUTE_PATH = re.compile(r"(?:^|[\\/])(?:Users|home|var|tmp|mnt|private)[\\/]")
_ARTIFACT_REF = re.compile(r"^artifact:[A-Za-z0-9_-]{1,128}$")


def canonical_json(value: object) -> str:
    """Encode only finite, JSON-native values with stable key ordering."""
    _require_json_value(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def canonical_request_hash(request: Mapping[str, object]) -> str:
    """Hash public request facts for callers that own request canonicalization."""
    public = {
        key: value for key, value in request.items()
        if key not in _INTERNAL_REQUEST_KEYS and not key.startswith("_")
    }
    return hashlib.sha256(canonical_json(public).encode("utf-8")).hexdigest()


@dataclass(frozen=True, kw_only=True)
class ActionSnapshot:
    """Immutable action input shared by every professional Executor."""

    action_id: str
    attempt_id: str
    scope: RuntimeScope
    request: Mapping[str, object]
    request_hash: str
    fencing_token: str
    executor_type: str
    executor_revision: int
    dispatch_context: Mapping[str, object]

    @classmethod
    def from_attempt(
        cls, attempt: ActionAttempt, request: Mapping[str, object], *,
        executor_type: str, executor_revision: int,
    ) -> "ActionSnapshot":
        public_request = dict(request)
        raw_context = public_request.get("_dispatch_context")
        dispatch_context = (
            dict(raw_context) if isinstance(raw_context, Mapping) else {}
        )
        public_request.pop("external_idempotency_key", None)
        public_request.pop("_dispatch_context", None)
        # The durable Action hash covers the persisted lifecycle envelope;
        # it is not a second hash of the provider argument object.
        return cls(
            action_id=str(attempt.action_id), attempt_id=str(attempt.attempt_id),
            scope=attempt.scope, request=public_request,
            request_hash=attempt.request_hash, executor_type=executor_type,
            executor_revision=executor_revision,
            fencing_token=str(attempt.lease.fencing_token),
            dispatch_context=dispatch_context,
        )


@dataclass(frozen=True, kw_only=True)
class ResultPolicy:
    """Per-executor output contract; large facts require an Artifact ref."""

    max_inline_bytes: int = 16_384
    max_summary_chars: int = 1_200

    def __post_init__(self) -> None:
        if self.max_inline_bytes < 256 or self.max_summary_chars < 64:
            raise ValueError("result bounds are too small")


def safe_result(value: object, policy: ResultPolicy) -> dict[str, object]:
    """Return bounded, redacted JSON facts or fail closed."""
    redacted = _redact(value)
    encoded = canonical_json(redacted).encode("utf-8")
    if len(encoded) > policy.max_inline_bytes:
        artifact_ref = (
            redacted.get("artifact_ref")
            if isinstance(redacted, Mapping) else None
        )
        if not isinstance(artifact_ref, str) or not _ARTIFACT_REF.fullmatch(artifact_ref):
            raise ValueError("EXECUTOR_RESULT_TOO_LARGE")
        redacted = {
            "artifact_ref": artifact_ref,
            "byte_size": len(encoded),
            "content_hash": hashlib.sha256(encoded).hexdigest(),
        }
    if not isinstance(redacted, Mapping):
        raise ValueError("EXECUTOR_RESULT_OBJECT_REQUIRED")
    return dict(redacted)


def bounded_summary(value: Mapping[str, object], policy: ResultPolicy) -> str:
    summary = value.get("summary", "")
    if not isinstance(summary, str):
        raise ValueError("EXECUTOR_RESULT_SUMMARY_INVALID")
    return summary[:policy.max_summary_chars]


def _require_json_value(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("EXECUTOR_JSON_NON_FINITE")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("EXECUTOR_JSON_KEY_INVALID")
            _require_json_value(item)
        return
    if isinstance(value, list):
        for item in value:
            _require_json_value(item)
        return
    raise ValueError("EXECUTOR_JSON_VALUE_INVALID")


def _redact(value: object, *, key: str = "") -> object:
    key_text = key if isinstance(key, str) else ""
    if _SENSITIVE_KEY.search(key_text):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {k: _redact(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and _ABSOLUTE_PATH.search(value):
        return "[redacted-path]"
    return value
