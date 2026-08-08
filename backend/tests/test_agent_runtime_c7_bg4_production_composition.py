"""C7-BG4 production composition and Gateway health contracts."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from core.db_scope import DatabaseAccessKind
from services.agent.runtime.composition import build_runtime, scoped
from services.agent.runtime.model_gateway.protocol import VERSION
from services.agent.runtime.production_factory import (
    _require_gateway_health, build_production_model_gateway_components,
)


def test_gateway_composition_flags_default_off_and_missing_health_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="RUNTIME_MODEL_GATEWAY_DISABLED"):
        build_production_model_gateway_components(object(), SimpleNamespace())
    settings = SimpleNamespace(
        agent_runtime_model_gateway_enabled=True,
        agent_runtime_model_gateway_socket="/tmp/model.sock",
        agent_runtime_model_gateway_health_socket="/tmp/missing-health.sock",
        agent_runtime_release_revision="release-1",
    )
    with pytest.raises(RuntimeError, match="HEALTH_UNAVAILABLE"):
        build_production_model_gateway_components(object(), settings)


def test_ready_gateway_factory_still_reports_bg5_production_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.agent.runtime.production_factory._require_gateway_health",
        lambda _path, _release: None,
    )
    settings = SimpleNamespace(
        agent_runtime_model_gateway_enabled=True,
        agent_runtime_model_gateway_socket="/tmp/model.sock",
        agent_runtime_model_gateway_health_socket="/tmp/model-health.sock",
        agent_runtime_release_revision="release-1",
    )
    components = build_production_model_gateway_components(
        scoped(object(), DatabaseAccessKind.AGENT_RUNTIME, "worker"), settings,
    )
    assert components.model.requires_gateway_dispatch is True
    assert components.model.production_ready is False
    assert "ExistingProviderModelAdapter" not in inspect.getsource(build_runtime)


def test_gateway_health_projection_is_strict_and_supports_fragmented_frames(
    monkeypatch,
) -> None:
    payload = json.dumps({
        "version": VERSION, "release": "release-1", "ready": True,
        "draining": False,
        "dependencies": {
            "db": "available", "kek": "available",
            "provider_registry": "available", "socket": "available",
        },
        "in_flight": 0, "heartbeat": 1.5,
    }).encode() + b"\n"

    class FakeHealthSocket:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, _timeout):
            pass

        def connect(self, path):
            assert path == "/tmp/health.sock"

        def sendall(self, value):
            assert value == b"health\n"

        def recv(self, _limit):
            return self.chunks.pop(0) if self.chunks else b""

    monkeypatch.setattr(
        "services.agent.runtime.production_factory.socket.socket",
        lambda *_args: FakeHealthSocket((payload[:20], payload[20:])),
    )
    _require_gateway_health("/tmp/health.sock", "release-1")
    monkeypatch.setattr(
        "services.agent.runtime.production_factory.socket.socket",
        lambda *_args: FakeHealthSocket((payload,)),
    )
    with pytest.raises(RuntimeError, match="HEALTH_NOT_READY"):
        _require_gateway_health("/tmp/health.sock", "wrong-release")
    old_payload = payload.replace(VERSION.encode(), b"agent-model-gateway.v1")
    monkeypatch.setattr(
        "services.agent.runtime.production_factory.socket.socket",
        lambda *_args: FakeHealthSocket((old_payload,)),
    )
    with pytest.raises(RuntimeError, match="HEALTH_NOT_READY"):
        _require_gateway_health("/tmp/health.sock", "release-1")
