"""Redacted, idempotent provider callback inbox."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping


_SENSITIVE = re.compile(r"(?:secret|token|password|cookie|authorization|api[_-]?key)", re.I)


@dataclass(frozen=True, kw_only=True)
class CallbackEvent:
    provider: str
    provider_event_id: str
    callback_correlation: str
    payload_hash: str
    payload_redacted: Mapping[str, object]
    signature_valid: bool


class CallbackInbox:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str, str], CallbackEvent] = {}

    def record(
        self, provider: str, event_id: str, correlation: str,
        payload: Mapping[str, object], *, signature_valid: bool,
    ) -> CallbackEvent:
        redacted = _redact(payload)
        payload_hash = hashlib.sha256(
            json.dumps(redacted, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        event = CallbackEvent(
            provider=provider, provider_event_id=event_id,
            callback_correlation=correlation, payload_hash=payload_hash,
            payload_redacted=redacted, signature_valid=signature_valid,
        )
        key = (provider, event_id, payload_hash)
        existing = self._events.get(key)
        if existing is not None:
            return existing
        if not signature_valid:
            raise PermissionError("CALLBACK_SIGNATURE_INVALID")
        self._events[key] = event
        return event


def _redact(value: object, key: str = "") -> object:
    if _SENSITIVE.search(key):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


__all__ = ["CallbackEvent", "CallbackInbox"]
