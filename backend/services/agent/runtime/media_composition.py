"""Production media-only Runtime composition.

The graph is constructible without enabling it. Provider access is only
possible when callers explicitly supply a transport and enable the capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.agent.runtime.executors.provider_adapters import KieMediaProvider
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.specialist_registry import (
    MEDIA_TOOLS, SPECIALIST_FAMILIES, SPECIALIST_SAFETY, specialist_descriptor,
)
from services.agent.runtime.executors.family_executors import EXECUTOR_BY_FAMILY
from services.agent.runtime.media_task_port import build_runtime_media_task_port


@dataclass(frozen=True, kw_only=True)
class RuntimeMediaComposition:
    registry: ExecutorRegistry
    enabled: bool
    production_ready: bool


def build_runtime_media_composition(
    *, database: Any, transport: Any | None, enabled: bool = False,
) -> RuntimeMediaComposition:
    """Build media Executors; default construction is fail-closed."""
    registry = ExecutorRegistry()
    if not enabled:
        return RuntimeMediaComposition(
            registry=registry, enabled=False, production_ready=False,
        )
    if database is None or transport is None:
        raise RuntimeError("RUNTIME_MEDIA_COMPOSITION_WIRING_REQUIRED")
    task_port = build_runtime_media_task_port(database)
    for tool in sorted(MEDIA_TOOLS):
        descriptor = specialist_descriptor(tool)
        provider = KieMediaProvider(
            transport, kind=tool.removeprefix("generate_"), task_port=task_port,
        )
        registry.register(
            descriptor,
            EXECUTOR_BY_FAMILY[SPECIALIST_FAMILIES[tool]](
                action_kind=tool, executor_type=descriptor.executor_type,
                revision=descriptor.revision, provider=provider,
                async_submit=True,
            ),
            safety_level=SPECIALIST_SAFETY[tool],
        )
    return RuntimeMediaComposition(
        registry=registry, enabled=True, production_ready=True,
    )


__all__ = ["RuntimeMediaComposition", "build_runtime_media_composition"]
