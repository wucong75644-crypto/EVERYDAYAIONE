"""Explicit self-contained Runtime graph for disposable non-production tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from services.agent.runtime.credential_broker import (
    BackendCredential,
    CredentialBroker,
    InMemoryCredentialAuditSink,
)
from services.agent.runtime.domain import RuntimeScope
from services.agent.runtime.erp_adapter import MockErpProvider, RuntimeErpAdapter
from services.agent.runtime.executors.materializer import ArtifactMaterializer
from services.agent.runtime.executors.resource_contracts import (
    ContentAddressedArtifactService,
    WorkspaceResourceService,
)
from services.agent.runtime.executors.provider_adapters import TenantProviderBinding
from services.agent.runtime.executors.resource_support import ChildRunService
from services.agent.runtime.media_adapter import MockMediaProvider, RuntimeMediaAdapter
from services.agent.runtime.nonproduction_backends import (
    LocalNonProductionCredentialBackend,
    LocalNonProductionObjectStore,
)
from services.agent.runtime.provider_facts import MockProviderSubmissionFacts
from services.agent.runtime.runtime_assembly import (
    RuntimeProductionAssembly,
    build_runtime_production_assembly,
)
from services.agent.runtime.scheduler_cas import (
    MockTenantScopedSchedulerCasStore,
    RuntimeSchedulerCasBridge,
)


class LocalNonProductionResourceFacts:
    """Secret-free local facts port used only by the disposable profile."""

    def __init__(self) -> None:
        self.resources: list[tuple[str, dict[str, object]]] = []
        self.artifacts: list[dict[str, object]] = []

    async def mutate_resource(self, operation: str, **params: object) -> Mapping[str, object]:
        self.resources.append((operation, dict(params)))
        return {"outcome": "bound", "operation": operation}

    async def link_artifact(self, **params: object) -> Mapping[str, object]:
        self.artifacts.append(dict(params))
        return {"outcome": "linked"}

    async def checkpoint_materialization(self, **_params: object) -> Mapping[str, object]:
        return {"outcome": "checkpointed"}


class LocalNonProductionChildRunRepository:
    """Parent-bound child-run facts for isolated lifecycle tests."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, int], dict[str, object]] = {}

    async def create_child_run(self, **params: object) -> Mapping[str, object]:
        key = (str(params["p_parent_run_id"]), str(params["p_parent_action_id"]), int(params["p_child_ordinal"]))
        existing = key in self._rows
        row = self._rows.setdefault(key, {
            "outcome": "created", "child_run_id": f"local-child-{len(self._rows) + 1}",
            "status": "queued", "state_version": 1,
        })
        return dict(row) | ({"outcome": "already_exists"} if existing else {})

    async def read_child_run(self, **params: object) -> Mapping[str, object]:
        child_id = params.get("child_run_id")
        row = next((row for key, row in self._rows.items() if (
            (child_id is not None and row["child_run_id"] == child_id)
            or (child_id is None and key[:2] == (
                str(params["parent_run_id"]), str(params["parent_action_id"]),
            ))
        )), None)
        if row is None:
            return {"outcome": "not_found"}
        return {"outcome": "readback", **row}

    async def complete_child_run(self, **params: object) -> Mapping[str, object]:
        row = next(row for row in self._rows.values() if row["child_run_id"] == params["p_child_run_id"])
        row.update(status="completed", state_version=int(row["state_version"]) + 1)
        return {"outcome": "completed", **row}

    async def cancel_child_run(self, **params: object) -> Mapping[str, object]:
        row = next((row for key, row in self._rows.items() if key[1] == str(
            params["p_parent_action_id"],
        )), None)
        if row is None:
            return {"outcome": "not_found"}
        row.update(status="cancelled", state_version=int(row["state_version"]) + 1)
        proof = hashlib.sha256(str(params["p_parent_action_id"]).encode()).hexdigest()
        return {
            "outcome": "confirmed", "intent_id": str(params["p_parent_action_id"]),
            "terminal_kind": "cancelled", "proof_hash": proof, **row,
        }


class LocalNonProductionProviderResolver:
    """Resolves only explicit mock providers through the CredentialBroker."""

    def __init__(self, broker: CredentialBroker, providers: Mapping[str, object]) -> None:
        self._broker = broker
        self._providers = dict(providers)
        self._revisions = {name: f"{name}-nonprod-v1" for name in self._providers}
        self._handles = {name: f"test:nonprod:{name}" for name in self._providers}

    async def resolve(self, scope: RuntimeScope, tool_name: str) -> TenantProviderBinding:
        provider = self._providers.get(tool_name)
        if provider is None:
            raise RuntimeError("TENANT_PROVIDER_BINDING_NOT_FOUND")
        revision = self._revisions[tool_name]
        lease = await self._broker.resolve(
            scope=scope, credential_handle=self._handles[tool_name],
            provider=tool_name, revision=revision, purpose=f"{tool_name}.nonprod",
        )
        await lease.use(
            scope=scope, provider=tool_name, revision=revision,
            purpose=f"{tool_name}.nonprod", consumer=lambda _material: None,
        )
        return TenantProviderBinding(
            provider=provider, provider_revision=revision,
            readiness_hash=hashlib.sha256(revision.encode()).hexdigest(),
            credential_handle=self._handles[tool_name], ready=True,
        )


@dataclass(frozen=True, kw_only=True)
class LocalNonProductionRuntimeProfile:
    assembly: RuntimeProductionAssembly
    credential_broker: CredentialBroker
    provider_facts: MockProviderSubmissionFacts
    resource_facts: LocalNonProductionResourceFacts
    object_store: LocalNonProductionObjectStore
    provider_resolver: LocalNonProductionProviderResolver


def build_local_nonproduction_runtime_profile(
    *, root: Path, staging: Path, tenant_id: str = "nonprod-tenant",
) -> LocalNonProductionRuntimeProfile:
    credential_backend = LocalNonProductionCredentialBackend()
    audit = InMemoryCredentialAuditSink()
    for provider in ("erp", "media"):
        credential_backend.put_test_record(BackendCredential(
            tenant_id=tenant_id, handle=f"test:nonprod:{provider}",
            provider=provider, revision=f"{provider}-nonprod-v1",
            purpose=f"{provider}.nonprod",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            _material="non-production-fixture",
        ))
    broker = CredentialBroker(credential_backend, audit)
    provider_facts = MockProviderSubmissionFacts()
    erp = RuntimeErpAdapter(
        facts=provider_facts, provider=MockErpProvider(),
        provider_revision="erp-nonprod-v1",
    )
    media = RuntimeMediaAdapter(
        facts=provider_facts, provider=MockMediaProvider(),
        provider_revision="media-nonprod-v1", kind="image",
    )
    resource_facts = LocalNonProductionResourceFacts()
    scheduler = RuntimeSchedulerCasBridge(
        facts=resource_facts, store=MockTenantScopedSchedulerCasStore(),
    )
    object_store = LocalNonProductionObjectStore(root / "objects", tenant_id=tenant_id)
    artifact = ContentAddressedArtifactService(
        root=root, staging=staging, materializer=ArtifactMaterializer(),
        facts=resource_facts, objects=object_store,
    )
    workspace = WorkspaceResourceService(
        root=root, staging=staging, objects=object_store, facts=resource_facts,
    )
    child_repository = LocalNonProductionChildRunRepository()
    child_run = ChildRunService(repository=child_repository)
    resolver = LocalNonProductionProviderResolver(
        broker, {"erp": erp, "media": media},
    )
    assembly = build_runtime_production_assembly(
        credential_broker=broker, provider_facts=provider_facts,
        erp=erp, media=media, scheduler=scheduler, artifact=artifact,
        workspace=workspace, child_run=child_run, provider_resolver=resolver,
    )
    return LocalNonProductionRuntimeProfile(
        assembly=assembly, credential_broker=broker,
        provider_facts=provider_facts, resource_facts=resource_facts,
        object_store=object_store, provider_resolver=resolver,
    )


__all__ = [
    "LocalNonProductionProviderResolver", "LocalNonProductionResourceFacts",
    "LocalNonProductionRuntimeProfile", "build_local_nonproduction_runtime_profile",
]
