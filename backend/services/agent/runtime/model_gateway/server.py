"""Local isolated model gateway server and peer credential boundary."""

from __future__ import annotations

import asyncio
import os
import socket
import stat
import struct
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from .protocol import (
    MAX_RESPONSE_BYTES, REQUEST_FRAME_LIMIT, RESPONSE_FRAME_LIMIT, VERSION,
    GatewayProtocolError, encode_frame, read_frame, validate_request, validate_response, write_frame,
)


class PeerCredentialVerifier(Protocol):
    def verify(self, writer: asyncio.StreamWriter) -> bool: ...


class LinuxPeerCredentialVerifier:
    def __init__(self, allowed_uid: int) -> None:
        self._allowed_uid = allowed_uid

    def verify(self, writer: asyncio.StreamWriter) -> bool:
        peer_socket = writer.get_extra_info("socket")
        if peer_socket is None or not hasattr(socket, "SO_PEERCRED"):
            return False
        try:
            credentials = peer_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _, uid, _ = struct.unpack("3i", credentials)
        except (OSError, struct.error):
            return False
        return uid == self._allowed_uid


Handler = Callable[[dict[str, Any]], AsyncIterator[Mapping[str, Any]]]


class FakeModelGatewayServer:
    """Isolated fake server. It cannot be installed as production composition."""

    production_ready = False
    isolated_only = True

    def __init__(self, socket_path: str, handler: Handler,
                 peer_verifier: PeerCredentialVerifier, *, socket_mode: int = 0o660,
                 expected_uid: int | None = None, expected_gid: int | None = None,
                 response_limit: int = MAX_RESPONSE_BYTES) -> None:
        self._path = Path(socket_path)
        self._handler = handler
        self._peer_verifier = peer_verifier
        self._socket_mode = socket_mode
        self._expected_uid = expected_uid
        self._expected_gid = expected_gid
        if response_limit <= 0:
            raise GatewayProtocolError("GATEWAY_INVALID_RESPONSE_LIMIT")
        self._response_limit = min(response_limit, MAX_RESPONSE_BYTES)
        self._server: asyncio.AbstractServer | None = None
        self._owned_identity: tuple[int, int] | None = None

    async def start(self) -> None:
        self._prepare_socket_path()
        try:
            self._server = await asyncio.start_unix_server(self._serve, path=str(self._path))
            initial = self._path.lstat()
            if not stat.S_ISSOCK(initial.st_mode):
                raise GatewayProtocolError("GATEWAY_SOCKET_SECURITY_INVALID")
            self._owned_identity = (initial.st_dev, initial.st_ino)
            os.chmod(self._path, self._socket_mode)
            self._verify_socket_security()
        except GatewayProtocolError:
            await self.close()
            raise
        except OSError:
            await self.close()
            raise GatewayProtocolError("GATEWAY_SOCKET_SECURITY_INVALID") from None

    def _verify_socket_security(self) -> None:
        info = self._path.stat()
        identity_ok = (
            self._owned_identity == (info.st_dev, info.st_ino)
            and
            (self._expected_uid is None or info.st_uid == self._expected_uid)
            and (self._expected_gid is None or info.st_gid == self._expected_gid)
        )
        if (not stat.S_ISSOCK(info.st_mode)
                or stat.S_IMODE(info.st_mode) != self._socket_mode or not identity_ok):
            raise GatewayProtocolError("GATEWAY_SOCKET_SECURITY_INVALID")

    def _prepare_socket_path(self) -> None:
        self._verify_parent_path()
        try:
            info = self._path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode):
            raise GatewayProtocolError("GATEWAY_SOCKET_PATH_UNSAFE")
        self._path.unlink()

    def _verify_parent_path(self) -> None:
        if not self._path.is_absolute():
            raise GatewayProtocolError("GATEWAY_SOCKET_PARENT_UNSAFE")
        current = Path(self._path.anchor)
        for part in self._path.parent.parts[1:]:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                raise GatewayProtocolError("GATEWAY_SOCKET_PARENT_UNSAFE") from None
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise GatewayProtocolError("GATEWAY_SOCKET_PARENT_UNSAFE")

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        try:
            info = self._path.lstat()
        except FileNotFoundError:
            return
        if self._owned_identity == (info.st_dev, info.st_ino) and stat.S_ISSOCK(info.st_mode):
            self._path.unlink()

    async def __aenter__(self) -> "FakeModelGatewayServer":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_id = "00000000-0000-0000-0000-000000000000"
        sequence = 0
        terminal = False
        response_bytes = 0
        try:
            if not self._peer_verifier.verify(writer):
                raise GatewayProtocolError("GATEWAY_PEER_REJECTED")
            request = validate_request(await read_frame(reader, limit=REQUEST_FRAME_LIMIT))
            request_id = request["request_id"]
            async for raw_response in self._handler(request):
                if terminal:
                    raise GatewayProtocolError("GATEWAY_FRAME_AFTER_TERMINAL")
                response = dict(raw_response)
                response.update(version=VERSION, request_id=request_id, sequence=sequence)
                validated = validate_response(
                    response, request_id=request_id, expected_sequence=sequence,
                )
                frame_size = len(encode_frame(validated, limit=RESPONSE_FRAME_LIMIT)) - 4
                if response_bytes + frame_size > self._response_limit:
                    raise GatewayProtocolError("GATEWAY_RESPONSE_TOO_LARGE")
                response_bytes += frame_size
                await write_frame(writer, validated, limit=RESPONSE_FRAME_LIMIT)
                terminal = response.get("type") in {"completed", "failed", "unknown"}
                sequence += 1
            if not terminal:
                raise GatewayProtocolError("GATEWAY_TERMINAL_RESPONSE_REQUIRED")
        except GatewayProtocolError as error:
            if not terminal:
                await self._safe_error(
                    writer, request_id, sequence, error.code,
                    response_bytes=response_bytes, response_limit=self._response_limit,
                )
        except Exception:
            if not terminal:
                await self._safe_error(
                    writer, request_id, sequence, "GATEWAY_INTERNAL_ERROR",
                    response_bytes=response_bytes, response_limit=self._response_limit,
                )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass

    @staticmethod
    async def _safe_error(writer: asyncio.StreamWriter, request_id: str,
                          sequence: int, code: str, *, response_bytes: int,
                          response_limit: int) -> None:
        response = {
            "version": VERSION, "request_id": request_id, "sequence": sequence,
            "type": "failed", "error_code": code, "retry_class": "terminal",
            "summary": "gateway request rejected",
        }
        try:
            error_size = len(encode_frame(response, limit=RESPONSE_FRAME_LIMIT)) - 4
            if response_bytes + error_size > response_limit:
                return
            await write_frame(writer, response)
        except GatewayProtocolError:
            return
