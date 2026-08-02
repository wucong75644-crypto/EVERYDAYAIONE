from __future__ import annotations

import pytest

from services.agent.runtime.catalog.production import (
    ProductionToolBinding, build_production_catalog,
)
from services.agent.runtime.catalog.registry import RuntimeToolCatalog
from services.agent.runtime.catalog.types import RuntimeToolDefinition
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.rollout import RolloutSubject, resolve_rollout
from services.agent.runtime.shadow import compare_runtime_facts


def _catalog(name: str) -> RuntimeToolCatalog:
    catalog = RuntimeToolCatalog()
    catalog.register(RuntimeToolDefinition(
        canonical_name=name, tool_group="test", schema={"type": "object"},
        safety_level="safe", executor_type=f"executor:{name}",
        executor_revision=1, capability_requirements=frozenset({"test"}),
        side_effect="none", authorization_requirement="none",
        retry_semantics="retry_safe", reconcile_semantics="unsupported",
        cancel_semantics="unsupported", result_schema_revision=1,
    ))
    return catalog


def test_rollout_never_treats_personal_scope_as_global() -> None:
    subjects = {
        ("user", "u1"): RolloutSubject(
            kind="user", subject_id="u1", channels=frozenset({"web"}),
            capabilities=frozenset({"runtime_ingress"}), enabled=True,
        ),
    }
    assert resolve_rollout(org_id=None, user_id="u1", channel="web", subjects=subjects) == (True, "enabled")
    assert resolve_rollout(org_id=None, user_id="u2", channel="web", subjects=subjects)[0] is False


def test_shadow_is_pure_and_classifies_each_mismatch() -> None:
    result = compare_runtime_facts(
        definition_hash="a", expected_definition_hash="b", toolset_hash="x",
        expected_toolset_hash="x", policy_hash="p", expected_policy_hash="q",
        arguments_hash="i", expected_arguments_hash="i", executor_type="e",
        expected_executor_type="f", projection={"state": "a"},
        expected_projection={"state": "b"},
    )
    assert {item.category for item in result} == {"definition", "policy", "executor", "projection"}
    assert all(item.details["model_calls"] == 0 for item in result)


def test_production_catalog_requires_all_executor_bindings() -> None:
    read = ExecutorRegistry()
    sandbox = ExecutorRegistry()
    specialist = ExecutorRegistry()
    # The count guard is reached before any provider can be mistaken for a
    # complete production catalog.
    with pytest.raises(ValueError, match="MISMATCH|INCOMPLETE"):
        build_production_catalog(
            read_registry=read, sandbox_registry=sandbox,
            specialist_registry=specialist, bindings={},
        )


def test_binding_readiness_hash_is_strict() -> None:
    with pytest.raises(ValueError, match="READINESS_HASH"):
        ProductionToolBinding(provider_revision="p1", readiness_hash="bad")


def test_production_composition_covers_exactly_42_executor_backed_tools() -> None:
    from services.agent.runtime.catalog.registry import RuntimeToolCatalog
    from services.agent.runtime.executors.sandbox_job import (
        SANDBOX_JOB_DESCRIPTOR, SandboxJobExecutor,
    )
    from services.agent.runtime.executors.real_base import RuntimeReadResources
    from services.agent.runtime.production_composition import (
        ProductionSpecialistPorts, build_production_read_registry,
        build_production_specialist_registry,
    )

    read = build_production_read_registry(RuntimeReadResources(database=object()))
    sandbox = ExecutorRegistry([(SANDBOX_JOB_DESCRIPTOR, SandboxJobExecutor())])
    specialist = build_production_specialist_registry(
        ProductionSpecialistPorts(
            transport=object(), erp_dispatcher=object(), erp_search=object(),
            artifact=object(), media_task=object(), resource_mutation=object(),
            child_run=object(),
        ),
        facts=object(),
    )
    names = {
        tool
        for registry in (read, sandbox, specialist)
        for descriptor in registry.descriptors()
        for tool in descriptor.action_kinds
    }
    bindings = {
        name: ProductionToolBinding(
            provider_revision="provider-v1",
            secret_binding=None if name.startswith("get_") else f"secret:{name}",
            readiness_hash="a" * 64,
            ready=True,
        )
        for name in names
    }
    from services.agent.runtime.catalog.production import build_production_catalog
    receipt = build_production_catalog(
        read_registry=read, sandbox_registry=sandbox,
        specialist_registry=specialist, bindings=bindings,
    )
    assert len(receipt.catalog.definitions()) == 42
    assert receipt.production_revision != receipt.catalog.revision
