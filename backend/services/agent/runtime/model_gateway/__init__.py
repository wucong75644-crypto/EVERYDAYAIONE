"""Local-only protocol boundary for the Agent Runtime model gateway."""

from .client import IsolatedModelGatewayClient
from .protocol import (
    PRODUCTION_READY,
    GatewayProtocolError,
    decode_payload,
    encode_frame,
)
from .server import FakeModelGatewayServer, LinuxPeerCredentialVerifier

__all__ = [
    "FakeModelGatewayServer",
    "GatewayProtocolError",
    "IsolatedModelGatewayClient",
    "LinuxPeerCredentialVerifier",
    "PRODUCTION_READY",
    "decode_payload",
    "encode_frame",
]
