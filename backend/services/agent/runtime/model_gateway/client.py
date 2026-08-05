"""Async isolated UDS client used by the BG1 harness only."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

from .protocol import (
    MAX_RESPONSE_BYTES,
    GatewayProtocolError,
    encode_frame,
    read_frame,
    validate_request,
    validate_response,
    write_frame,
)


class IsolatedModelGatewayClient:
    """One-request-per-connection client; never production-ready."""

    production_ready = False
    isolated_only = True

    def __init__(self, socket_path: str, *, connect_timeout: float = 2.0,
                 first_frame_timeout: float = 10.0,
                 response_limit: int = MAX_RESPONSE_BYTES) -> None:
        self._socket_path = socket_path
        self._connect_timeout = connect_timeout
        self._first_frame_timeout = first_frame_timeout
        self._response_limit = response_limit

    async def complete(self, request: Mapping[str, Any]) -> AsyncIterator[dict[str, Any]]:
        parsed = validate_request(dict(request))
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self._socket_path), self._connect_timeout,
            )
        except (TimeoutError, OSError):
            raise GatewayProtocolError("GATEWAY_CONNECT_FAILED") from None
        try:
            await write_frame(writer, parsed)
            sequence = 0
            total = 0
            terminal = False
            while not terminal:
                timeout = self._first_frame_timeout if sequence == 0 else parsed["deadline_ms"] / 1000
                try:
                    response = await asyncio.wait_for(read_frame(reader), timeout)
                except TimeoutError:
                    raise GatewayProtocolError("GATEWAY_RESPONSE_TIMEOUT") from None
                total += len(encode_frame(response)) - 4
                if total > self._response_limit:
                    raise GatewayProtocolError("GATEWAY_RESPONSE_TOO_LARGE")
                validated = validate_response(
                    response, request_id=parsed["request_id"], expected_sequence=sequence,
                )
                terminal = validated["type"] in {"completed", "failed", "unknown"}
                sequence += 1
                yield validated
            try:
                trailing = await asyncio.wait_for(reader.read(1), 0.1)
            except TimeoutError:
                trailing = b""
            if trailing:
                raise GatewayProtocolError("GATEWAY_FRAME_AFTER_TERMINAL")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass
