"""Typed transport boundary for WeCom App HTTP message delivery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Optional, Protocol


SEND_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
APP_OUTBOUND_CAPACITY = 1024
PROVIDER_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
PROVIDER_MESSAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
MSGTYPE_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")
REQUEST_ID_HEADER = "X-Request-ID"
_SENSITIVE_PAYLOAD_KEYS = frozenset({"access_token", "agent_secret", "secret"})


class WecomAppOutboundStatus(str, Enum):
    NOT_STARTED = "not_started"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class WecomAppOutboundErrorClass(str, Enum):
    INVALID_REQUEST = "invalid_request"
    IDENTITY_CONFLICT = "identity_conflict"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    PROVIDER_REJECTED = "provider_rejected"
    TRANSPORT_AMBIGUOUS = "transport_ambiguous"
    RESPONSE_AMBIGUOUS = "response_ambiguous"


@dataclass(frozen=True)
class WecomAppOutboundReceipt:
    provider_request_id: str
    status: WecomAppOutboundStatus
    errcode: Optional[int] = None
    provider_message_id: Optional[str] = None
    error_class: Optional[WecomAppOutboundErrorClass] = None


class AppHttpResponse(Protocol):
    def json(self) -> Any: ...


class AppHttpClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        json: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> AppHttpResponse: ...


AppAccessTokenProvider = Callable[[], Awaitable[Optional[str]]]


@dataclass
class _RequestEntry:
    request_hash: str
    future: asyncio.Future[WecomAppOutboundReceipt]
    result: Optional[WecomAppOutboundReceipt] = None


class WecomAppOutbound:
    """Send App HTTP requests once with caller-owned local correlation identity.

    WeCom's App send API has no caller-owned idempotency field. The stable ID is
    therefore sent as an HTTP correlation header and retained only in this
    process; it is not evidence of provider-side idempotency.
    """

    def __init__(
        self,
        *,
        token_provider: Optional[AppAccessTokenProvider],
        http_client: Optional[AppHttpClient],
        capacity: int = APP_OUTBOUND_CAPACITY,
    ) -> None:
        self._token_provider = token_provider
        self._http_client = http_client
        self._capacity = max(1, capacity)
        self._requests: OrderedDict[str, _RequestEntry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def send_typed(
        self,
        *,
        provider_request_id: str,
        target: str,
        payload: Mapping[str, Any],
    ) -> WecomAppOutboundReceipt:
        """Perform at most one HTTP request and return a proof-based receipt."""
        request_hash = _request_hash(target, payload)
        if not _valid_request(provider_request_id, target, payload, request_hash):
            return _receipt(
                provider_request_id,
                WecomAppOutboundStatus.NOT_STARTED,
                WecomAppOutboundErrorClass.INVALID_REQUEST,
            )
        entry, owner, immediate = await self._reserve(
            provider_request_id, request_hash,
        )
        if immediate is not None:
            return immediate
        if entry is None:
            return _receipt(
                provider_request_id,
                WecomAppOutboundStatus.NOT_STARTED,
                WecomAppOutboundErrorClass.CAPACITY_EXCEEDED,
            )
        if not owner:
            return await asyncio.shield(entry.future)
        return await self._execute(
            provider_request_id, target, payload, entry,
        )

    def _transport_ready(self) -> bool:
        if self._token_provider is None or self._http_client is None:
            return False
        if not callable(self._token_provider):
            return False
        if not callable(getattr(self._http_client, "post", None)):
            return False
        return getattr(self._http_client, "is_closed", False) is not True

    async def _reserve(
        self,
        provider_request_id: str,
        request_hash: str,
    ) -> tuple[
        Optional[_RequestEntry], bool, Optional[WecomAppOutboundReceipt],
    ]:
        async with self._lock:
            existing = self._requests.get(provider_request_id)
            if existing is not None:
                self._requests.move_to_end(provider_request_id)
                if existing.request_hash != request_hash:
                    return None, False, _receipt(
                        provider_request_id,
                        WecomAppOutboundStatus.NOT_STARTED,
                        WecomAppOutboundErrorClass.IDENTITY_CONFLICT,
                    )
                if existing.result is not None:
                    return existing, False, existing.result
                return existing, False, None
            if not self._transport_ready():
                return None, False, _receipt(
                    provider_request_id,
                    WecomAppOutboundStatus.NOT_STARTED,
                    WecomAppOutboundErrorClass.TRANSPORT_UNAVAILABLE,
                )
            if not self._make_capacity():
                return None, False, None
            entry = _RequestEntry(
                request_hash=request_hash,
                future=asyncio.get_running_loop().create_future(),
            )
            self._requests[provider_request_id] = entry
            return entry, True, None

    def _make_capacity(self) -> bool:
        while len(self._requests) >= self._capacity:
            completed_key = next(
                (
                    key for key, entry in self._requests.items()
                    if entry.result is not None
                ),
                None,
            )
            if completed_key is None:
                return False
            self._requests.pop(completed_key)
        return True

    async def _execute(
        self,
        provider_request_id: str,
        target: str,
        payload: Mapping[str, Any],
        entry: _RequestEntry,
    ) -> WecomAppOutboundReceipt:
        request_started = False
        try:
            token = await self._token_provider()  # type: ignore[misc]
            if not isinstance(token, str) or not token.strip():
                result = _receipt(
                    provider_request_id,
                    WecomAppOutboundStatus.NOT_STARTED,
                    WecomAppOutboundErrorClass.CREDENTIAL_UNAVAILABLE,
                )
            else:
                request_started = True
                response = await self._http_client.post(  # type: ignore[union-attr]
                    SEND_MSG_URL,
                    params={"access_token": token},
                    json=payload,
                    headers={REQUEST_ID_HEADER: provider_request_id},
                )
                result = _response_receipt(provider_request_id, response)
        except asyncio.CancelledError:
            result = _receipt(
                provider_request_id,
                (
                    WecomAppOutboundStatus.UNKNOWN
                    if request_started else WecomAppOutboundStatus.NOT_STARTED
                ),
                (
                    WecomAppOutboundErrorClass.TRANSPORT_AMBIGUOUS
                    if request_started
                    else WecomAppOutboundErrorClass.CREDENTIAL_UNAVAILABLE
                ),
            )
            await self._settle(provider_request_id, entry, result)
            raise
        except Exception:
            result = _receipt(
                provider_request_id,
                (
                    WecomAppOutboundStatus.UNKNOWN
                    if request_started else WecomAppOutboundStatus.NOT_STARTED
                ),
                (
                    WecomAppOutboundErrorClass.TRANSPORT_AMBIGUOUS
                    if request_started
                    else WecomAppOutboundErrorClass.CREDENTIAL_UNAVAILABLE
                ),
            )
        await self._settle(provider_request_id, entry, result)
        return result

    async def _settle(
        self,
        provider_request_id: str,
        entry: _RequestEntry,
        result: WecomAppOutboundReceipt,
    ) -> None:
        async with self._lock:
            current = self._requests.get(provider_request_id)
            if current is not entry:
                return
            if result.status is WecomAppOutboundStatus.NOT_STARTED:
                self._requests.pop(provider_request_id)
            else:
                entry.result = result
                self._requests.move_to_end(provider_request_id)
            if not entry.future.done():
                entry.future.set_result(result)


def _request_hash(
    target: str,
    payload: Mapping[str, Any],
) -> Optional[str]:
    if not isinstance(target, str) or not isinstance(payload, dict):
        return None
    try:
        encoded = json.dumps(
            {"target": target, "payload": payload},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(encoded) > 1_000_000:
        return None
    return hashlib.sha256(encoded).hexdigest()


def _valid_request(
    provider_request_id: str,
    target: str,
    payload: Mapping[str, Any],
    request_hash: Optional[str],
) -> bool:
    if not (
        isinstance(provider_request_id, str)
        and PROVIDER_REQUEST_ID_PATTERN.fullmatch(provider_request_id)
        and isinstance(target, str)
        and target.strip() == target
        and target
        and len(target.encode("utf-8")) <= 32_768
        and all(ord(char) >= 32 for char in target)
        and isinstance(payload, dict)
        and request_hash
    ):
        return False
    if _SENSITIVE_PAYLOAD_KEYS.intersection(payload):
        return False
    msgtype = payload.get("msgtype")
    agent_id = payload.get("agentid")
    return bool(
        payload.get("touser") == target
        and isinstance(msgtype, str)
        and MSGTYPE_PATTERN.fullmatch(msgtype)
        and isinstance(agent_id, int)
        and not isinstance(agent_id, bool)
        and agent_id > 0
        and isinstance(payload.get(msgtype), dict)
        and payload[msgtype]
    )


def _response_receipt(
    provider_request_id: str,
    response: AppHttpResponse,
) -> WecomAppOutboundReceipt:
    try:
        data = response.json()
    except Exception:
        return _receipt(
            provider_request_id,
            WecomAppOutboundStatus.UNKNOWN,
            WecomAppOutboundErrorClass.RESPONSE_AMBIGUOUS,
        )
    if not isinstance(data, dict):
        return _receipt(
            provider_request_id,
            WecomAppOutboundStatus.UNKNOWN,
            WecomAppOutboundErrorClass.RESPONSE_AMBIGUOUS,
        )
    errcode = data.get("errcode")
    if not isinstance(errcode, int) or isinstance(errcode, bool):
        return _receipt(
            provider_request_id,
            WecomAppOutboundStatus.UNKNOWN,
            WecomAppOutboundErrorClass.RESPONSE_AMBIGUOUS,
        )
    if errcode != 0:
        return WecomAppOutboundReceipt(
            provider_request_id=provider_request_id,
            status=WecomAppOutboundStatus.REJECTED,
            errcode=errcode,
            error_class=WecomAppOutboundErrorClass.PROVIDER_REJECTED,
        )
    provider_message_id = data.get("msgid")
    safe_message_id = (
        provider_message_id
        if isinstance(provider_message_id, str)
        and PROVIDER_MESSAGE_ID_PATTERN.fullmatch(provider_message_id)
        else None
    )
    return WecomAppOutboundReceipt(
        provider_request_id=provider_request_id,
        status=WecomAppOutboundStatus.ACKNOWLEDGED,
        errcode=0,
        provider_message_id=safe_message_id,
    )


def _receipt(
    provider_request_id: str,
    status: WecomAppOutboundStatus,
    error_class: WecomAppOutboundErrorClass,
) -> WecomAppOutboundReceipt:
    return WecomAppOutboundReceipt(
        provider_request_id=provider_request_id,
        status=status,
        error_class=error_class,
    )
