"""Non-production 42-tool Catalog/Registry/Safety consistency gate."""

from __future__ import annotations

from services.agent.runtime.catalog import RuntimeToolCatalog
from services.agent.runtime.catalog.nonproduction import build_nonproduction_specialist_catalog
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.specialist_registry import SPECIALIST_TOOLS


EXPECTED_SPECIALIST_COUNT = 23
EXPECTED_RUNTIME_TOOL_COUNT = 42


def build_nonproduction_full_catalog(
    read_registry: ExecutorRegistry,
    specialist_registry: ExecutorRegistry,
    sandbox_registry: ExecutorRegistry,
) -> RuntimeToolCatalog:
    """Merge isolated registries without changing production composition."""
    if len(SPECIALIST_TOOLS) != EXPECTED_SPECIALIST_COUNT:
        raise ValueError("SPECIALIST_TOOL_COUNT_MISMATCH")
    catalogs = (
        RuntimeToolCatalog.from_executor_registry(read_registry),
        build_nonproduction_specialist_catalog(specialist_registry),
        RuntimeToolCatalog.from_executor_registry(sandbox_registry),
    )
    result = RuntimeToolCatalog()
    for catalog in catalogs:
        for definition in catalog.definitions():
            result.register(definition)
    assert_nonproduction_catalog_consistency(result, specialist_registry)
    return result


def assert_nonproduction_catalog_consistency(
    catalog: RuntimeToolCatalog, specialist_registry: ExecutorRegistry,
) -> None:
    names = {definition.canonical_name for definition in catalog.definitions()}
    descriptors = {
        name for descriptor in specialist_registry.descriptors()
        for name in descriptor.action_kinds
    }
    if not SPECIALIST_TOOLS.issubset(descriptors):
        raise ValueError("SPECIALIST_REGISTRY_INCOMPLETE")
    if len(names) != EXPECTED_RUNTIME_TOOL_COUNT:
        raise ValueError("RUNTIME_CATALOG_COUNT_MISMATCH")
    if not SPECIALIST_TOOLS.issubset(names):
        raise ValueError("RUNTIME_CATALOG_SPECIALISTS_MISSING")
    for definition in catalog.definitions():
        descriptor = specialist_registry.resolve(definition.canonical_name) if definition.canonical_name in SPECIALIST_TOOLS else None
        if descriptor is not None:
            if descriptor[0].executor_type != definition.executor_type or descriptor[0].revision != definition.executor_revision:
                raise ValueError("RUNTIME_CATALOG_DESCRIPTOR_MISMATCH")
            if specialist_registry.safety_level(definition.canonical_name) is None:
                raise ValueError("RUNTIME_SAFETY_MISSING")
    from config.tool_safety import get_safety_level
    for name in names:
        if get_safety_level(name).value != catalog.resolve(name).safety_level:
            raise ValueError("RUNTIME_SAFETY_REGISTRY_MISMATCH")
