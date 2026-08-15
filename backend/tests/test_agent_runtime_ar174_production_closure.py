from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from types import SimpleNamespace

from services.agent.runtime.catalog.production import (
    ProductionToolBinding, build_production_catalog, build_production_toolset,
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


def test_production_catalog_rejects_extra_provider_bindings() -> None:
    read = ExecutorRegistry()
    sandbox = ExecutorRegistry()
    specialist = ExecutorRegistry()
    with pytest.raises(ValueError, match="MISMATCH|INCOMPLETE|BINDING_SET_MISMATCH"):
        build_production_catalog(
            read_registry=read, sandbox_registry=sandbox,
            specialist_registry=specialist,
            bindings={"not-a-runtime-tool": ProductionToolBinding(
                provider_revision="provider-v1", readiness_hash="a" * 64,
                ready=True,
            )},
        )


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


def test_disabled_production_toolset_keeps_only_safe_local_tools() -> None:
    from services.agent.runtime.agents import AgentDefinition
    from services.agent.runtime.catalog.production import build_production_toolset

    catalog = _catalog("safe_tool")
    receipt = type("Receipt", (), {
        "catalog": catalog,
        "production_revision": "p" * 64,
        "provider_revisions": {"safe_tool": "provider-v1"},
    })()
    agent = AgentDefinition(
        canonical_key="test", revision="v1", prompt_revision="p1",
        requested_tool_groups=frozenset({"test"}),
        channel_restrictions=frozenset({"web"}),
    )
    toolset = build_production_toolset(
        agent=agent, receipt=receipt, scope="user", channel="web",
        entitled_groups=frozenset({"test"}),
        authorized_names=frozenset({"safe_tool"}), gate_state="disabled",
    )
    assert [item.canonical_name for item in toolset.definitions] == ["safe_tool"]


def test_generated_sql_catalog_matches_descriptor_seed() -> None:
    from services.agent.runtime.catalog.production_seed import build_seed_snapshot

    sql = (Path(__file__).parents[1] / "migrations" /
           "227_02_agent_runtime_production_catalog_seed.sql").read_text()
    match = re.search(r"catalog_doc JSONB := \$seed\$(.*?)\$seed\$::JSONB", sql)
    assert match is not None
    stored = json.loads(match.group(1))
    assert stored == build_seed_snapshot().receipt.document()
    assert "Do not edit facts by hand" in sql
    assert "'schema_hash','cd1a463c" not in sql


@pytest.mark.asyncio
async def test_tenant_scoped_provider_resolves_from_attempt_scope() -> None:
    from services.agent.runtime.domain import RuntimeScope, ScopeKind
    from services.agent.runtime.executors.provider_adapters import (
        TenantProviderBinding, TenantScopedProvider,
    )
    from services.agent.runtime.executors.specialist_contracts import (
        ProviderReceipt, ProviderState,
    )

    scope = RuntimeScope(
        kind=ScopeKind.USER, scope_id="user-1", user_id="user-1", org_id=None,
    )
    calls = []

    class Provider:
        async def submit(self, attempt, request, *, idempotency_key):
            calls.append((attempt.scope, request, idempotency_key))
            return ProviderReceipt(
                state=ProviderState.COMPLETED, provider="scoped",
                request_hash="a" * 64,
            )

        async def reconcile(self, attempt, receipt):
            raise AssertionError("not used")

        async def cancel(self, attempt, receipt):
            raise AssertionError("not used")

    class Resolver:
        async def resolve(self, resolved_scope, tool_name):
            assert resolved_scope == scope
            assert tool_name == "scoped_tool"
            return TenantProviderBinding(
                provider=Provider(), provider_revision="provider-v1",
                readiness_hash="a" * 64, credential_handle="credential-handle",
                ready=True,
            )

    attempt = SimpleNamespace(scope=scope)
    result = await TenantScopedProvider(
        Resolver(), "scoped_tool",
    ).submit(attempt, {"value": 1}, idempotency_key="k")
    assert result.provider == "scoped"
    assert calls == [(scope, {"value": 1}, "k")]


@pytest.mark.asyncio
async def test_postgres_tenant_provider_resolver_passes_exact_scope_to_rpc() -> None:
    from services.agent.runtime.domain import RuntimeScope, ScopeKind
    from services.agent.runtime.production_services import (
        PostgresTenantProviderResolver,
    )

    class Response:
        data = {
            "outcome": "found", "provider_revision": "provider-v1",
            "credential_handle": "credential:org-1:kie",
            "readiness_hash": "a" * 64,
            "service_wiring_ready": True, "credential_available": True,
            "capability_enabled": True, "probe_passed": True, "ready": True,
        }

    class Query:
        async def execute(self):
            return Response()

    class Database:
        def __init__(self):
            self.calls = []

        def rpc(self, name, params):
            self.calls.append((name, params))
            return Query()

    class Provider:
        async def submit(self, attempt, request, *, idempotency_key):
            raise AssertionError("not used")

    database = Database()
    resolver = PostgresTenantProviderResolver(
        database, catalog_revision="catalog-v1",
        builders={"generate_image": lambda scope, tool, handle: Provider()},
    )
    scope = RuntimeScope(
        kind=ScopeKind.CHANNEL, scope_id="channel:c-1",
        user_id="user-1", org_id="org-1",
    )
    binding = await resolver.resolve(scope, "generate_image")
    assert binding.provider_revision == "provider-v1"
    assert database.calls == [(
        "resolve_agent_runtime_tenant_provider_binding", {
            "p_catalog_revision": "catalog-v1",
            "p_tool_name": "generate_image",
            "p_scope_kind": "channel",
            "p_scope_id": "channel:c-1",
            "p_org_id": "org-1",
        },
    )]


@pytest.mark.asyncio
async def test_postgres_tenant_provider_resolver_fails_closed_on_readiness() -> None:
    from services.agent.runtime.domain import RuntimeScope, ScopeKind
    from services.agent.runtime.production_services import (
        PostgresTenantProviderResolver,
    )

    class Response:
        data = {
            "outcome": "found", "provider_revision": "provider-v1",
            "readiness_hash": "a" * 64, "ready": False,
            "service_wiring_ready": True, "credential_available": False,
            "capability_enabled": True, "probe_passed": True,
        }

    class Query:
        async def execute(self):
            return Response()

    class Database:
        def rpc(self, name, params):
            return Query()

    resolver = PostgresTenantProviderResolver(
        Database(), catalog_revision="catalog-v1", builders={},
    )
    with pytest.raises(RuntimeError, match="CREDENTIAL_UNAVAILABLE"):
        await resolver.resolve(
            RuntimeScope(ScopeKind.USER, "user:u-1", "u-1", "org-1"),
            "generate_image",
        )


def test_production_service_bundle_requires_explicit_ports_and_resolver() -> None:
    from services.agent.runtime.production_services import (
        ProductionServicePorts, ReadinessResult,
        build_production_service_bundle,
    )

    ports = ProductionServicePorts(
        erp_dispatcher=object(), erp_search=object(), transport=object(),
        media_task=object(), artifact=object(), workspace=object(),
        scheduler=object(), child_run=object(),
    )
    readiness = ReadinessResult(
        service_wiring_ready=True, tenant_binding_ready=False,
        credential_available=False, capability_enabled=False,
        probe_passed=False, error_code="PROVIDER_NOT_READY",
    )
    with pytest.raises(RuntimeError, match="provider_resolver"):
        build_production_service_bundle(
            ports=ports, provider_resolver=None, readiness=readiness,
        )


def test_production_service_bundle_does_not_promote_tenant_readiness() -> None:
    from services.agent.runtime.production_services import (
        ProductionServicePorts, ReadinessResult,
        build_production_service_bundle,
    )

    class Resolver:
        async def resolve(self, scope, tool_name):
            raise AssertionError("not used")

    ports = ProductionServicePorts(
        erp_dispatcher=object(), erp_search=object(), transport=object(),
        media_task=object(), artifact=object(), workspace=object(),
        scheduler=object(), child_run=object(),
    )
    readiness = ReadinessResult(
        service_wiring_ready=True, tenant_binding_ready=False,
        credential_available=False, capability_enabled=False,
        probe_passed=False, error_code="PROVIDER_NOT_READY",
    )
    bundle = build_production_service_bundle(
        ports=ports, provider_resolver=Resolver(), readiness=readiness,
    )
    assert bundle.readiness.ready is False
    with pytest.raises(RuntimeError, match="PROVIDER_NOT_READY"):
        bundle.require_ready()


@pytest.mark.asyncio
async def test_fact_bound_workspace_rejects_missing_run_context() -> None:
    from services.agent.runtime.production_services import FactBoundWorkspacePort

    facts = object()

    class Service:
        def __init__(self):
            self.facts = facts

        async def delete(self, *args, **kwargs):
            raise AssertionError("scope must be checked first")

    port = FactBoundWorkspacePort(service=Service(), facts=facts)
    with pytest.raises(RuntimeError, match="WORKSPACE_RUN_CONTEXT_REQUIRED"):
        await port.delete("resource", "file.txt", "object", attempt=SimpleNamespace(
            scope=SimpleNamespace(scope_id="user:u-1"), run_id=None,
        ))


@pytest.mark.asyncio
async def test_fact_bound_artifact_requires_lineage_and_verified_object_store(tmp_path) -> None:
    from services.agent.runtime.executors.materializer import ArtifactMaterializer
    from services.agent.runtime.executors.resource_contracts import (
        ContentAddressedArtifactService,
    )
    from services.agent.runtime.production_services import FactBoundArtifactPort

    class Facts:
        async def link_artifact(self, **params):
            return {"outcome": "linked"}

        async def checkpoint_materialization(self, **params):
            return {"outcome": "checkpointed"}

    class Objects:
        async def put_verified(self, key, content, *, content_hash):
            return {"key": key, "content_hash": content_hash, "verified": True}

    root = tmp_path / "root"
    staging = tmp_path / "staging"
    root.mkdir()
    (root / "input.txt").write_bytes(b"artifact")
    facts = Facts()
    service = ContentAddressedArtifactService(
        root=root, staging=staging, materializer=ArtifactMaterializer(),
        facts=facts, objects=Objects(),
    )
    port = FactBoundArtifactPort(service=service, facts=facts)
    attempt = SimpleNamespace(
        scope=SimpleNamespace(scope_id="user:u-1"), run_id="run-1",
        action_id="action-1", attempt_id="attempt-1",
    )
    result = await port.prepare(attempt, {"path": "input.txt", "artifact_id": "artifact-1"})
    assert result["content_hash"]


def test_fact_bound_artifact_rejects_missing_object_store(tmp_path) -> None:
    from services.agent.runtime.executors.materializer import ArtifactMaterializer
    from services.agent.runtime.executors.resource_contracts import (
        ContentAddressedArtifactService,
    )
    from services.agent.runtime.production_services import FactBoundArtifactPort

    facts = object()
    service = ContentAddressedArtifactService(
        root=tmp_path / "root", staging=tmp_path / "staging",
        materializer=ArtifactMaterializer(), facts=facts,
    )
    with pytest.raises(RuntimeError, match="ARTIFACT_OBJECT_STORE_WIRING_NOT_READY"):
        FactBoundArtifactPort(service=service, facts=facts)


@pytest.mark.asyncio
async def test_fact_bound_child_run_preserves_parent_scope_and_owner() -> None:
    from services.agent.runtime.production_services import FactBoundChildRunPort

    facts = object()

    class Service:
        def __init__(self):
            self.repository = facts
            self.seen = None

        async def create(self, attempt, request):
            self.seen = (attempt, request)
            return {"outcome": "already_exists", "state": "accepted"}

    service = Service()
    port = FactBoundChildRunPort(service=service, facts=facts)
    attempt = SimpleNamespace(
        scope=SimpleNamespace(scope_id="channel:c-1"), run_id="run-1",
    )
    result = await port.create(attempt, {"child_ordinal": 1})
    assert result["outcome"] == "already_exists"
    assert service.seen == (attempt, {"child_ordinal": 1})
