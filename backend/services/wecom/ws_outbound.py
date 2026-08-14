"""企业微信 WebSocket 出站消息能力。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from loguru import logger

from schemas.wecom import WecomCommand


OUTBOUND_ACK_TIMEOUT = 10.0
OUTBOUND_WRITE_TIMEOUT = 5.0
OUTBOUND_PENDING_CAPACITY = 128
OUTBOUND_RESULT_CAPACITY = 1024
OUTBOUND_RESULT_TTL = 300.0
PROVIDER_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")


class WecomOutboundStatus(str, Enum):
    NOT_STARTED = "not_started"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class WecomOutboundErrorClass(str, Enum):
    INVALID_REQUEST = "invalid_request"
    IDENTITY_CONFLICT = "identity_conflict"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    UNAVAILABLE = "unavailable"
    PROVIDER_REJECTED = "provider_rejected"
    ACK_TIMEOUT = "ack_timeout"
    DISCONNECTED = "disconnected"
    CANCELLED = "cancelled"
    TRANSPORT_INTERRUPTED = "transport_interrupted"


@dataclass(frozen=True)
class WecomOutboundAckResult:
    provider_request_id: str
    status: WecomOutboundStatus
    errcode: Optional[int] = None
    error_class: Optional[WecomOutboundErrorClass] = None


@dataclass
class _OutboundRequest:
    params_hash: str
    future: Optional[asyncio.Future]
    write_started: bool
    result: Optional[WecomOutboundAckResult]
    updated_at: float


def _request_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


class WecomOutboundMixin:
    def _init_typed_outbound(self) -> None:
        self._outbound_requests: OrderedDict[str, _OutboundRequest] = OrderedDict()

    def lookup_outbound_result(
        self, provider_request_id: str,
    ) -> Optional[WecomOutboundAckResult]:
        """Return one cached result without waiting or refreshing its lifetime."""
        if not _valid_provider_request_id(provider_request_id):
            return None
        self._prune_outbound_requests(time.monotonic())
        entry = self._outbound_requests.get(provider_request_id)
        return entry.result if entry is not None else None

    async def send_proactive_typed(
        self,
        provider_request_id: str,
        chatid: str,
        msgtype: str,
        content: dict,
    ) -> WecomOutboundAckResult:
        """Send once with caller-owned identity and return a proof-based ACK result."""
        params_hash = _params_hash(chatid, msgtype, content)
        if not _valid_request(provider_request_id, chatid, msgtype, params_hash):
            return _result(
                provider_request_id,
                WecomOutboundStatus.NOT_STARTED,
                WecomOutboundErrorClass.INVALID_REQUEST,
            )

        now = time.monotonic()
        self._prune_outbound_requests(now)
        existing = self._outbound_requests.get(provider_request_id)
        if existing:
            self._outbound_requests.move_to_end(provider_request_id)
            if existing.params_hash != params_hash:
                return _result(
                    provider_request_id,
                    WecomOutboundStatus.NOT_STARTED,
                    WecomOutboundErrorClass.IDENTITY_CONFLICT,
                )
            return await self._read_outbound_request(existing)

        ws = self._ws if self.is_connected else None
        if ws is None:
            return _result(
                provider_request_id,
                WecomOutboundStatus.NOT_STARTED,
                WecomOutboundErrorClass.UNAVAILABLE,
            )
        if not self._reserve_outbound_capacity():
            return _result(
                provider_request_id,
                WecomOutboundStatus.NOT_STARTED,
                WecomOutboundErrorClass.CAPACITY_EXCEEDED,
            )

        entry = _OutboundRequest(
            params_hash=params_hash,
            future=asyncio.get_running_loop().create_future(),
            write_started=False,
            result=None,
            updated_at=now,
        )
        self._outbound_requests[provider_request_id] = entry
        future = entry.future

        message = {
            "cmd": WecomCommand.SEND_MSG,
            "headers": {"req_id": provider_request_id},
            "body": {"chatid": chatid, "msgtype": msgtype, msgtype: content},
        }
        entry.write_started = True
        entry.updated_at = time.monotonic()
        try:
            await asyncio.wait_for(
                ws.send(json.dumps(message)), timeout=OUTBOUND_WRITE_TIMEOUT,
            )
        except asyncio.CancelledError:
            self._settle_outbound(
                provider_request_id,
                WecomOutboundStatus.UNKNOWN,
                WecomOutboundErrorClass.CANCELLED,
            )
            await self._force_close()
            raise
        except Exception:
            result = self._settle_outbound(
                provider_request_id,
                WecomOutboundStatus.UNKNOWN,
                WecomOutboundErrorClass.TRANSPORT_INTERRUPTED,
            )
            await self._force_close()
            return result

        if entry.result is not None:
            return entry.result
        if future is None:
            raise RuntimeError("typed outbound request has no future")
        try:
            return await asyncio.wait_for(
                asyncio.shield(future), timeout=OUTBOUND_ACK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return self._settle_outbound(
                provider_request_id,
                WecomOutboundStatus.UNKNOWN,
                WecomOutboundErrorClass.ACK_TIMEOUT,
            )
        except asyncio.CancelledError:
            self._settle_outbound(
                provider_request_id,
                WecomOutboundStatus.UNKNOWN,
                WecomOutboundErrorClass.CANCELLED,
            )
            raise

    async def _read_outbound_request(
        self, entry: _OutboundRequest,
    ) -> WecomOutboundAckResult:
        if entry.result is not None:
            return entry.result
        future = entry.future
        if future is None:
            raise RuntimeError("typed outbound request has no result")
        return await asyncio.shield(future)

    def _route_typed_outbound_ack(self, data: dict) -> bool:
        req_id = data.get("headers", {}).get("req_id")
        if not isinstance(req_id, str):
            return False
        entry = self._outbound_requests.get(req_id)
        if not entry or not entry.write_started:
            return False
        errcode = data.get("errcode")
        if errcode is None and isinstance(data.get("body"), dict):
            errcode = data["body"].get("errcode")
        if not isinstance(errcode, int) or isinstance(errcode, bool):
            return False
        if errcode == 0:
            self._settle_outbound(req_id, WecomOutboundStatus.ACKNOWLEDGED)
        else:
            self._settle_outbound(
                req_id,
                WecomOutboundStatus.REJECTED,
                WecomOutboundErrorClass.PROVIDER_REJECTED,
                errcode=errcode,
            )
        return True

    def _mark_typed_outbound_disconnected(self) -> None:
        for req_id, entry in list(self._outbound_requests.items()):
            if entry.write_started and entry.result is None:
                self._settle_outbound(
                    req_id,
                    WecomOutboundStatus.UNKNOWN,
                    WecomOutboundErrorClass.DISCONNECTED,
                )

    def _settle_outbound(
        self,
        req_id: str,
        status: WecomOutboundStatus,
        error_class: Optional[WecomOutboundErrorClass] = None,
        *,
        errcode: Optional[int] = None,
    ) -> WecomOutboundAckResult:
        entry = self._outbound_requests[req_id]
        if entry.result is not None:
            is_late_proof = (
                entry.result.status == WecomOutboundStatus.UNKNOWN
                and status in {
                    WecomOutboundStatus.ACKNOWLEDGED,
                    WecomOutboundStatus.REJECTED,
                }
            )
            if not is_late_proof:
                return entry.result
        result = WecomOutboundAckResult(req_id, status, errcode, error_class)
        entry.result = result
        entry.updated_at = time.monotonic()
        future = entry.future
        entry.future = None
        self._outbound_requests.move_to_end(req_id)
        if future is not None and not future.done():
            future.set_result(result)
        return result

    def _reserve_outbound_capacity(self) -> bool:
        pending = sum(
            entry.result is None for entry in self._outbound_requests.values()
        )
        if pending >= OUTBOUND_PENDING_CAPACITY:
            return False
        while len(self._outbound_requests) >= OUTBOUND_RESULT_CAPACITY:
            completed_id = next(
                (
                    req_id
                    for req_id, entry in self._outbound_requests.items()
                    if entry.result is not None
                ),
                None,
            )
            if completed_id is None:
                return False
            self._outbound_requests.pop(completed_id)
        return True

    def _prune_outbound_requests(self, now: float) -> None:
        expired = [
            req_id
            for req_id, entry in self._outbound_requests.items()
            if entry.result is not None
            and now - entry.updated_at >= OUTBOUND_RESULT_TTL
        ]
        for req_id in expired:
            self._outbound_requests.pop(req_id, None)

    async def send_reply(self, req_id: str, msgtype: str, content: dict) -> None:
        await self._safe_send({
            "cmd": WecomCommand.RESPOND_MSG,
            "headers": {"req_id": req_id},
            "body": {"msgtype": msgtype, msgtype: content},
        })

    async def send_stream_chunk(
        self,
        req_id: str,
        stream_id: str,
        content: str,
        finish: bool = False,
        feedback_id: Optional[str] = None,
        msg_items: Optional[list] = None,
    ) -> None:
        stream: Dict[str, Any] = {
            "id": stream_id, "finish": finish, "content": content,
        }
        if feedback_id:
            stream["feedback"] = {"id": feedback_id}
        if msg_items and finish:
            stream["msg_item"] = msg_items
        await self._safe_send({
            "cmd": WecomCommand.RESPOND_MSG,
            "headers": {"req_id": req_id},
            "body": {"msgtype": "stream", "stream": stream},
        })

    async def send_template_card(self, req_id: str, card: dict) -> None:
        await self._safe_send({
            "cmd": WecomCommand.RESPOND_MSG,
            "headers": {"req_id": req_id},
            "body": {"msgtype": "template_card", "template_card": card},
        })

    async def send_update_card(self, req_id: str, card: dict) -> None:
        await self._safe_send({
            "cmd": WecomCommand.RESPOND_UPDATE,
            "headers": {"req_id": req_id},
            "body": {
                "response_type": "update_template_card",
                "template_card": card,
            },
        })

    async def send_msg(
        self,
        chatid: str,
        msgtype: str,
        content: dict,
        chattype: str = "single",
    ) -> bool:
        if not self.is_connected:
            return False
        req_id = _request_id("send_msg")
        await self._safe_send({
            "cmd": WecomCommand.SEND_MSG,
            "headers": {"req_id": req_id},
            "body": {
                "chatid": chatid, "chattype": chattype,
                "msgtype": msgtype, msgtype: content,
            },
        })
        return self.is_connected

    async def send_proactive(
        self, chatid: str, msgtype: str, content: dict,
    ) -> bool:
        if not self.is_connected:
            return False
        await self._safe_send({
            "cmd": WecomCommand.SEND_MSG,
            "headers": {"req_id": _request_id("scheduled")},
            "body": {"chatid": chatid, "msgtype": msgtype, msgtype: content},
        })
        return self.is_connected

    async def _safe_send(self, msg: dict) -> None:
        try:
            if self._ws and self.is_connected:
                await self._ws.send(json.dumps(msg))
        except Exception as error:
            logger.warning(f"Wecom WS send failed: {error}")
            await self._force_close()


def _params_hash(chatid: str, msgtype: str, content: dict) -> Optional[str]:
    if not isinstance(content, dict):
        return None
    try:
        encoded = json.dumps(
            {"chatid": chatid, "msgtype": msgtype, "content": content},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _valid_request(
    provider_request_id: str,
    chatid: str,
    msgtype: str,
    params_hash: Optional[str],
) -> bool:
    return bool(
        _valid_provider_request_id(provider_request_id)
        and isinstance(chatid, str)
        and chatid
        and isinstance(msgtype, str)
        and msgtype
        and params_hash
    )


def _valid_provider_request_id(provider_request_id: object) -> bool:
    return bool(
        isinstance(provider_request_id, str)
        and PROVIDER_REQUEST_ID_PATTERN.fullmatch(provider_request_id)
        and not provider_request_id.startswith("ping_")
    )


def _result(
    provider_request_id: str,
    status: WecomOutboundStatus,
    error_class: WecomOutboundErrorClass,
) -> WecomOutboundAckResult:
    return WecomOutboundAckResult(
        provider_request_id=provider_request_id,
        status=status,
        error_class=error_class,
    )
