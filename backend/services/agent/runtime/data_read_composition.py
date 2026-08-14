"""Explicitly injected Runtime data-read composition."""

from __future__ import annotations

from typing import Any

from services.agent.runtime.executors.data_adapters import (
    RuntimeFetchAllPagesAdapter, RuntimeFileAnalyzeAdapter, RuntimeLocalDataAdapter,
)
from services.agent.runtime.executors.family_executors import EXECUTOR_BY_FAMILY
from services.agent.runtime.executors.provider_adapters import ArtifactPort, LocalArtifactProvider
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.specialist_registry import SPECIALIST_SAFETY, specialist_descriptor
from services.agent.runtime.runtime_assembly import CapabilityReadiness, CapabilityReadinessState
from services.agent.runtime.executors.resource_manifest import PostgresRuntimeResourceManifestResolver


def build_runtime_data_read_registry(
    *, local_data: ArtifactPort | None = None,
    file_analyze: ArtifactPort | None = None,
    fetch_all_pages: ArtifactPort | None = None,
) -> ExecutorRegistry:
    """Register only explicitly supplied, non-producing data adapters."""
    ports = {
        "local_data": local_data,
        "file_analyze": file_analyze,
        "fetch_all_pages": fetch_all_pages,
    }
    registry = ExecutorRegistry()
    for tool, port in ports.items():
        if port is None:
            continue
        descriptor = specialist_descriptor(tool)
        registry.register(
            descriptor,
            EXECUTOR_BY_FAMILY["artifact_job"](
                action_kind=tool, executor_type=descriptor.executor_type,
                revision=descriptor.revision,
                provider=LocalArtifactProvider(port=port, operation=tool),
                async_submit=False,
            ),
            safety_level=SPECIALIST_SAFETY[tool],
        )
    return registry


__all__ = ["build_runtime_data_adapters", "build_runtime_data_read_registry"]


def data_readiness(ready: bool) -> CapabilityReadiness:
    return CapabilityReadiness(
        state=CapabilityReadinessState.READY if ready else CapabilityReadinessState.UNAVAILABLE,
        error_code=None if ready else "RUNTIME_DATA_READ_WIRING_NOT_READY",
    )


def build_runtime_data_adapters(
    database: Any, *, worker_id: str, erp_dispatcher_factory: Any,
) -> dict[str, object]:
    return {
        "local_data": RuntimeLocalDataAdapter(database=database),
        "file_analyze": RuntimeFileAnalyzeAdapter(
            database=database,
            manifest_resolver=PostgresRuntimeResourceManifestResolver(
                database, worker_id=worker_id,
            ),
        ),
        "fetch_all_pages": RuntimeFetchAllPagesAdapter(
            dispatcher_factory=erp_dispatcher_factory,
        ),
    }
