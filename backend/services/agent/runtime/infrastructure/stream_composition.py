"""Production composition helpers for the optional Runtime stream bus."""

from __future__ import annotations

import os
from typing import Any

from services.agent.runtime.infrastructure.stream_publisher import (
    RedisRuntimeStreamPublisher,
    RuntimeWebSocketStreamObserver,
)
from services.agent.runtime.ports.model import ModelResponseStreamObserver
from services.agent.runtime.ports.stream import (
    RuntimeStreamPublisher,
    RuntimeStreamTarget,
)


def build_runtime_stream_publisher(
    settings: Any, *, worker_id: str,
) -> RuntimeStreamPublisher | None:
    del settings
    if os.getenv("AGENT_RUNTIME_STREAM_ENABLED", "false").lower() not in {
        "1", "true", "yes", "on",
    }:
        return None
    return RedisRuntimeStreamPublisher(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        db=int(os.getenv("REDIS_DB", "0")),
        ssl=os.getenv("REDIS_SSL", "false").lower() in {
            "1", "true", "yes", "on",
        },
        worker_id=worker_id,
    )


def build_stream_observer_builder(
    publisher: RuntimeStreamPublisher | None,
):
    if publisher is None:
        return None

    def builder(
        target: RuntimeStreamTarget, model_id: str,
    ) -> ModelResponseStreamObserver:
        return RuntimeWebSocketStreamObserver(
            publisher=publisher, target=target, model_id=model_id,
        )

    return builder


def build_runtime_stream_components(settings: Any, *, worker_id: str):
    publisher = build_runtime_stream_publisher(settings, worker_id=worker_id)
    return publisher, build_stream_observer_builder(publisher)
