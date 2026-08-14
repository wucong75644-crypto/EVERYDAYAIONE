"""Fail-closed production assembly for Runtime-owned KIE media."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.agent.runtime.executors.family_executors import EXECUTOR_BY_FAMILY
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.specialist_registry import (
    MEDIA_TOOLS, SPECIALIST_FAMILIES, SPECIALIST_SAFETY,
    specialist_descriptor,
)
from services.agent.runtime.media_task_port import build_runtime_media_task_port
from services.agent.runtime.provider_facts import PostgresProviderSubmissionFacts
from services.agent.runtime.providers.kie_media import RuntimeKieMediaProvider


@dataclass(frozen=True, kw_only=True)
class RuntimeMediaComposition:
    registry: ExecutorRegistry
    enabled: bool
    production_ready: bool
    error_code: str | None = None


def build_runtime_media_composition(
    *, database: Any, transport: Any | None,
    credentials: Any | None = None, specialist_facts: object | None = None,
    enabled: bool = False, provider_probe_passed: bool = False,
    production_ready: bool = False,
) -> RuntimeMediaComposition:
    """Register media only when flag, wiring and probe facts are explicit."""
    registry = ExecutorRegistry()
    registry.specialist_facts = specialist_facts
    if not enabled:
        return RuntimeMediaComposition(
            registry=registry, enabled=False, production_ready=False,
            error_code="RUNTIME_MEDIA_DISABLED",
        )
    if not provider_probe_passed:
        return RuntimeMediaComposition(
            registry=registry, enabled=False, production_ready=False,
            error_code="RUNTIME_MEDIA_PROVIDER_NOT_READY",
        )
    if database is None or transport is None or credentials is None:
        raise RuntimeError("RUNTIME_MEDIA_COMPOSITION_WIRING_REQUIRED")
    task_port = build_runtime_media_task_port(database)
    provider_facts = PostgresProviderSubmissionFacts(database)
    for tool in sorted(MEDIA_TOOLS):
        descriptor = specialist_descriptor(tool)
        provider = RuntimeKieMediaProvider(
            transport, task_port=task_port, credentials=credentials,
            kind=tool.removeprefix("generate_"),
            production_ready=production_ready,
            recovery_ready=provider_probe_passed,
            facts=provider_facts,
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
        registry=registry, enabled=True, production_ready=production_ready,
        error_code=(None if production_ready
                    else "PRODUCTION_READINESS_DISABLED"),
    )


__all__ = ["RuntimeMediaComposition", "build_runtime_media_composition"]
