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


class CallbackInboxRepository(Protocol):
    async def callback(self, *, provider: str, event_id: str, correlation: str, payload_hash: str,
                       payload_redacted: Mapping[str, object], action_id: str, attempt_id: str) -> object: ...


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
    """唯一 Callback ingress: verify raw bytes, then persist through a port."""

    def __init__(self, verifier: CallbackSignatureVerifier, repository: CallbackInboxRepository) -> None:
        self._verifier = verifier
        self._repository = repository

    async def ingest(
        self, provider: str, event_id: str, correlation: str, payload: Mapping[str, object],
        *, body: bytes, signature: str, timestamp: str,
        action_id: str, attempt_id: str,
    ) -> CallbackEvent:
        if not self._verifier.verify(provider, body, signature, timestamp):
            raise PermissionError("CALLBACK_SIGNATURE_INVALID")
        event = _event(provider, event_id, correlation, payload)
        await self._repository.callback(
            provider=provider, event_id=event_id, correlation=correlation,
            payload_hash=event.payload_hash, payload_redacted=event.payload_redacted,
            action_id=action_id, attempt_id=attempt_id,
        )
        return event

    def record(self, *args: object, **kwargs: object) -> CallbackEvent:
        raise RuntimeError("CALLBACK_USE_INGEST_WITH_RAW_SIGNATURE")


def _event(
    provider: str, event_id: str, correlation: str,
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
    return event


def _redact(value: object, key: str = "") -> object:
    if _SENSITIVE.search(key):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


__all__ = ["CallbackEvent", "CallbackInbox", "CallbackInboxRepository", "CallbackSignatureVerifier", "HMACCallbackVerifier"]
