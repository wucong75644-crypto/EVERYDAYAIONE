"""Typed transport boundary for WeCom App HTTP message delivery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Optional, Protocol, TypeVar


SEND_MSG_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
APP_OUTBOUND_CAPACITY = 1024
APP_CREDENTIAL_TIMEOUT = 10.0
APP_HTTP_TIMEOUT = 10.0
PROVIDER_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
PROVIDER_MESSAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
MSGTYPE_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")
REQUEST_ID_HEADER = "X-Request-ID"
_SENSITIVE_PAYLOAD_KEYS = frozenset({"access_token", "agent_secret", "secret"})
_PARTIAL_USER_FIELDS = frozenset({"invaliduser", "unlicenseduser"})
_PARTIAL_ROUTE_FIELDS = frozenset({"invalidparty", "invalidtag"})
_PARTIAL_FIELDS = _PARTIAL_USER_FIELDS | _PARTIAL_ROUTE_FIELDS
_T = TypeVar("_T")


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
    CREDENTIAL_TIMEOUT = "credential_timeout"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_PARTIAL_REJECTED = "provider_partial_rejected"
    TRANSPORT_AMBIGUOUS = "transport_ambiguous"
    POST_TIMEOUT = "post_timeout"
    HTTP_STATUS_AMBIGUOUS = "http_status_ambiguous"
    RESPONSE_AMBIGUOUS = "response_ambiguous"


@dataclass(frozen=True)
class WecomAppOutboundReceipt:
    provider_request_id: str
    status: WecomAppOutboundStatus
    errcode: Optional[int] = None
    provider_message_id: Optional[str] = None
    error_class: Optional[WecomAppOutboundErrorClass] = None


class AppHttpResponse(Protocol):
    status_code: int

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


class _DeadlineExceeded(Exception):
    pass


async def _await_with_absolute_deadline(
    awaitable: Awaitable[_T],
    timeout: float,
) -> _T:
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        task.cancel()
        task.add_done_callback(_harvest_task)
        raise
    if task not in done:
        task.cancel()
        task.add_done_callback(_harvest_task)
        raise _DeadlineExceeded
    return task.result()


def _harvest_task(task: asyncio.Future[Any]) -> None:
    if not task.cancelled():
        task.exception()


@dataclass
class _RequestEntry:
    request_hash: str
    future: asyncio.Future[WecomAppOutboundReceipt]
    result: Optional[WecomAppOutboundReceipt] = None


@dataclass(frozen=True)
class _RequestSnapshot:
    target: str
    payload: dict[str, Any]
    request_hash: str


class WecomAppOutbound:
    """Send App HTTP requests once with caller-owned local correlation identity.

    WeCom's App send API has no caller-owned idempotency field. The stable ID is
    an HTTP correlation header, not provider idempotency. Credential timeouts fail
    closed via an instance tombstone; DB owners decide cross-instance retries.
    """

    def __init__(
        self,
        *,
        token_provider: Optional[AppAccessTokenProvider],
        http_client: Optional[AppHttpClient],
        capacity: int = APP_OUTBOUND_CAPACITY,
        credential_timeout: float = APP_CREDENTIAL_TIMEOUT,
        post_timeout: float = APP_HTTP_TIMEOUT,
    ) -> None:
        self._token_provider = token_provider
        self._http_client = http_client
        self._capacity = max(1, capacity)
        self._credential_timeout = credential_timeout
        self._post_timeout = post_timeout
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
        snapshot = _snapshot_request(target, payload)
        if snapshot is None or not _valid_request(
            provider_request_id, snapshot.target, snapshot.payload,
        ):
            return _receipt(
                provider_request_id,
                WecomAppOutboundStatus.NOT_STARTED,
                WecomAppOutboundErrorClass.INVALID_REQUEST,
            )
        entry, owner, immediate = await self._reserve(
            provider_request_id, snapshot.request_hash,
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
            provider_request_id, snapshot.payload, entry,
        )

    def _transport_ready(self) -> bool:
        if self._token_provider is None or self._http_client is None:
            return False
        if not callable(self._token_provider):
            return False
        if not callable(getattr(self._http_client, "post", None)):
            return False
        return bool(
            getattr(self._http_client, "is_closed", False) is not True
            and _valid_timeout(self._credential_timeout)
            and _valid_timeout(self._post_timeout)
        )

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
            if len(self._requests) >= self._capacity:
                return None, False, None
            entry = _RequestEntry(
                request_hash=request_hash,
                future=asyncio.get_running_loop().create_future(),
            )
            self._requests[provider_request_id] = entry
            return entry, True, None

    async def _execute(
        self,
        provider_request_id: str,
        payload: Mapping[str, Any],
        entry: _RequestEntry,
    ) -> WecomAppOutboundReceipt:
        request_started = False
        try:
            try:
                token = await _await_with_absolute_deadline(
                    self._token_provider(),  # type: ignore[misc]
                    self._credential_timeout,
                )
            except _DeadlineExceeded:
                result = _receipt(
                    provider_request_id,
                    WecomAppOutboundStatus.NOT_STARTED,
                    WecomAppOutboundErrorClass.CREDENTIAL_TIMEOUT,
                )
            except Exception:
                result = _receipt(
                    provider_request_id,
                    WecomAppOutboundStatus.NOT_STARTED,
                    WecomAppOutboundErrorClass.CREDENTIAL_UNAVAILABLE,
                )
            else:
                if not isinstance(token, str) or not token.strip():
                    result = _receipt(
                        provider_request_id,
                        WecomAppOutboundStatus.NOT_STARTED,
                        WecomAppOutboundErrorClass.CREDENTIAL_UNAVAILABLE,
                    )
                else:
                    request_started = True
                    try:
                        response = await _await_with_absolute_deadline(
                            self._http_client.post(  # type: ignore[union-attr]
                                SEND_MSG_URL,
                                params={"access_token": token},
                                json=payload,
                                headers={REQUEST_ID_HEADER: provider_request_id},
                            ),
                            self._post_timeout,
                        )
                    except _DeadlineExceeded:
                        result = _receipt(
                            provider_request_id,
                            WecomAppOutboundStatus.UNKNOWN,
                            WecomAppOutboundErrorClass.POST_TIMEOUT,
                        )
                    except Exception:
                        result = _receipt(
                            provider_request_id,
                            WecomAppOutboundStatus.UNKNOWN,
                            WecomAppOutboundErrorClass.TRANSPORT_AMBIGUOUS,
                        )
                    else:
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
            if result.status is WecomAppOutboundStatus.NOT_STARTED and (
                result.error_class is not WecomAppOutboundErrorClass.CREDENTIAL_TIMEOUT
            ):
                self._requests.pop(provider_request_id)
            else:
                entry.result = result
                self._requests.move_to_end(provider_request_id)
            if not entry.future.done():
                entry.future.set_result(result)


def _snapshot_request(
    target: str,
    payload: Mapping[str, Any],
) -> Optional[_RequestSnapshot]:
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
        if len(encoded) > 1_000_000:
            return None
        snapshot = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("payload"), dict):
        return None
    return _RequestSnapshot(
        target=snapshot.get("target"),
        payload=snapshot["payload"],
        request_hash=hashlib.sha256(encoded).hexdigest(),
    )


def _valid_request(
    provider_request_id: str,
    target: str,
    payload: Mapping[str, Any],
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
    ):
        return False
    if _contains_sensitive_key(payload):
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


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        if any(
            isinstance(key, str) and key.lower() in _SENSITIVE_PAYLOAD_KEYS
            for key in value
        ):
            return True
        return any(_contains_sensitive_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _valid_timeout(value: object) -> bool:
    return bool(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 < value <= 300
    )


def _response_receipt(
    provider_request_id: str,
    response: AppHttpResponse,
) -> WecomAppOutboundReceipt:
    status_code = getattr(response, "status_code", None)
    if (
        not isinstance(status_code, int)
        or isinstance(status_code, bool)
        or not 200 <= status_code < 300
    ):
        return _receipt(
            provider_request_id,
            WecomAppOutboundStatus.UNKNOWN,
            WecomAppOutboundErrorClass.HTTP_STATUS_AMBIGUOUS,
        )
    try:
        data = response.json()
    except Exception:
        return _ambiguous_response(provider_request_id)
    if not isinstance(data, dict):
        return _ambiguous_response(provider_request_id)
    errcode = data.get("errcode")
    if not isinstance(errcode, int) or isinstance(errcode, bool):
        return _ambiguous_response(provider_request_id)
    partial_fields = _validated_partial_fields(data)
    if partial_fields is None:
        return _ambiguous_response(provider_request_id)
    if errcode != 0:
        return WecomAppOutboundReceipt(
            provider_request_id=provider_request_id,
            status=WecomAppOutboundStatus.REJECTED,
            errcode=errcode,
            error_class=WecomAppOutboundErrorClass.PROVIDER_REJECTED,
        )
    if partial_fields & _PARTIAL_ROUTE_FIELDS:
        return _ambiguous_response(provider_request_id)
    if partial_fields & _PARTIAL_USER_FIELDS:
        return WecomAppOutboundReceipt(
            provider_request_id=provider_request_id,
            status=WecomAppOutboundStatus.REJECTED,
            errcode=0,
            error_class=WecomAppOutboundErrorClass.PROVIDER_PARTIAL_REJECTED,
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


def _validated_partial_fields(
    data: Mapping[str, Any],
) -> Optional[frozenset[str]]:
    present = {field: data[field] for field in _PARTIAL_FIELDS if field in data}
    if not all(isinstance(value, str) for value in present.values()):
        return None
    return frozenset(field for field, value in present.items() if value)


def _ambiguous_response(provider_request_id: str) -> WecomAppOutboundReceipt:
    return _receipt(provider_request_id, WecomAppOutboundStatus.UNKNOWN, WecomAppOutboundErrorClass.RESPONSE_AMBIGUOUS)


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
