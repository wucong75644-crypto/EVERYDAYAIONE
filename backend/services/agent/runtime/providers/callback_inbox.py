"""Redacted, idempotent provider callback inbox."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Mapping, Protocol


_SENSITIVE = re.compile(r"(?:secret|token|password|cookie|authorization|api[_-]?key)", re.I)


@dataclass(frozen=True, kw_only=True)
class CallbackEvent:
    provider: str
    provider_event_id: str
    callback_correlation: str
    payload_hash: str
    payload_redacted: Mapping[str, object]
    signature_valid: bool = True


class CallbackSignatureVerifier(Protocol):
    def verify(self, provider: str, body: bytes, signature: str, timestamp: str) -> bool: ...


@dataclass(frozen=True, kw_only=True)
class HMACCallbackVerifier:
    """Application-layer verifier; raw credentials never cross the DB port."""

    secrets_by_provider: Mapping[str, bytes]
    max_skew_seconds: int = 300

    def verify(self, provider: str, body: bytes, signature: str, timestamp: str) -> bool:
        import time
        try:
            if abs(int(time.time()) - int(timestamp)) > self.max_skew_seconds:
                return False
        except (TypeError, ValueError):
            return False
        secret = self.secrets_by_provider.get(provider)
        if secret is None:
            return False
        expected = hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


class CallbackInbox:
    def __init__(self, verifier: CallbackSignatureVerifier | None = None) -> None:
        self._events: dict[tuple[str, str, str], CallbackEvent] = {}
        self._verifier = verifier

    def record_verified(
        self, provider: str, event_id: str, correlation: str, payload: Mapping[str, object],
        *, body: bytes, signature: str, timestamp: str,
    ) -> CallbackEvent:
        if self._verifier is None or not self._verifier.verify(provider, body, signature, timestamp):
            raise PermissionError("CALLBACK_SIGNATURE_INVALID")
        return self._record(provider, event_id, correlation, payload)

    def record(
        self, provider: str, event_id: str, correlation: str,
        payload: Mapping[str, object], *, signature_valid: bool,
    ) -> CallbackEvent:
        if self._verifier is not None and not signature_valid:
            raise PermissionError("CALLBACK_SIGNATURE_INVALID")
        return self._record(provider, event_id, correlation, payload)

    def _record(
        self, provider: str, event_id: str, correlation: str,
        payload: Mapping[str, object],
    ) -> CallbackEvent:
        redacted = _redact(payload)
        payload_hash = hashlib.sha256(
            json.dumps(redacted, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        event = CallbackEvent(
            provider=provider, provider_event_id=event_id,
            callback_correlation=correlation, payload_hash=payload_hash,
            payload_redacted=redacted, signature_valid=True,
        )
        key = (provider, event_id, payload_hash)
        existing = self._events.get(key)
        if existing is not None:
            return existing
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


__all__ = ["CallbackEvent", "CallbackInbox", "CallbackSignatureVerifier", "HMACCallbackVerifier"]
