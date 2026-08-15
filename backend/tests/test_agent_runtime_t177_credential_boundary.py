"""T17.7 credential boundary contracts for Runtime-owned provider calls."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.agent.runtime.credential_broker import (
    BackendCredential, CredentialBroker, InMemoryCredentialAuditSink,
    InMemoryCredentialBackend,
)
from services.agent.runtime.domain import ModelStepId, RuntimeScope, ScopeKind
from services.agent.runtime.infrastructure.model import (
    ExistingProviderModelAdapter, compute_request_hash, resolve_model_revision,
)
from services.agent.runtime.ports import (
    ModelInputReceipt, ModelRequestOptions, ModelStepRequest,
)
from services.agent.runtime.context import ProviderContextPlan


def _scope() -> RuntimeScope:
    return RuntimeScope(
        kind=ScopeKind.USER, scope_id="user:u-1", user_id="u-1", org_id="org-a",
    )


def _request(*, lease: object | None = None) -> ModelStepRequest:
    model_id = "qwen3.5-plus"
    plan = ProviderContextPlan.build(
        messages=[{"role": "user", "content": "fixture"}], tools=[],
        context_epoch_id="epoch-1", model_step=1, stable_prefix_blocks=0,
    )
    options = ModelRequestOptions()
    revision = resolve_model_revision(model_id)
    request_hash = compute_request_hash(
        model_id=model_id, model_revision=revision,
        prompt_revision="prompt-1", tool_catalog_revision="tools-1",
        input_receipt_hash="input-1", context_plan_hash=plan.plan_hash,
        options=options,
    )
    return ModelStepRequest(
        model_step_id=ModelStepId("step-1"), model_id=model_id,
        request_hash=request_hash,
        input_receipt=ModelInputReceipt(
            receipt_id="receipt-1", receipt_hash="input-1",
            context_plan_hash=plan.plan_hash,
        ),
        context_plan=plan, model_revision=revision,
        prompt_revision="prompt-1", tool_catalog_revision="tools-1",
        options=options, org_id="org-a", credential_lease=lease,
        credential_scope=_scope(), credential_purpose="model.invoke",
    )


@pytest.mark.asyncio
async def test_default_model_builder_consumes_only_a_bound_lease(monkeypatch) -> None:
    material = "fixture-secret-must-not-escape"
    backend = InMemoryCredentialBackend()
    backend.put(BackendCredential(
        tenant_id="org-a", handle="test:model", provider="dashscope",
        revision=resolve_model_revision("qwen3.5-plus"), purpose="model.invoke",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        _material=material,
    ))
    broker = CredentialBroker(backend, InMemoryCredentialAuditSink())
    lease = await broker.resolve(
        scope=_scope(), credential_handle="test:model", provider="dashscope",
        revision=resolve_model_revision("qwen3.5-plus"), purpose="model.invoke",
    )
    captured: dict[str, object] = {}

    def builder(*_args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("services.adapters.factory.create_chat_adapter", builder)
    adapter = ExistingProviderModelAdapter(db=object())
    created = await adapter._create_adapter(_request(lease=lease))

    assert created is not None
    assert captured["api_key_override"] == material
    assert material not in repr(_request(lease=lease))


@pytest.mark.asyncio
async def test_default_model_builder_fails_closed_without_lease() -> None:
    adapter = ExistingProviderModelAdapter(db=object())
    with pytest.raises(ValueError, match="RUNTIME_CREDENTIAL_LEASE_REQUIRED"):
        await adapter._create_adapter(_request())


def test_model_request_has_no_raw_api_key_field() -> None:
    assert "provider_api_key" not in {item.name for item in fields(ModelStepRequest)}


def test_runtime_credential_sources_are_not_imported_by_provider_boundary() -> None:
    root = Path(__file__).parents[1] / "services/agent/runtime"
    sources = (
        root / "production_model.py",
        root / "infrastructure/model/adapter.py",
        root / "production_composition.py",
        root / "production_services.py",
        root / "composition.py",
    )
    forbidden = (
        "AsyncSecretBundleResolver", "get_settings()", "provider_api_key",
        "get_oss_service", "OrgConfigResolver", "RedisClient",
    )
    for source in sources:
        text = source.read_text()
        assert not any(item in text for item in forbidden), source


def test_credential_available_requires_a_broker() -> None:
    from services.agent.runtime.production_services import (
        ProductionServicePorts, ReadinessResult, build_production_service_bundle,
    )

    ports = ProductionServicePorts(
        erp_dispatcher=object(), erp_search=object(), transport=object(),
        media_task=object(), artifact=object(), workspace=object(),
        scheduler=object(), child_run=object(),
    )
    readiness = ReadinessResult(
        service_wiring_ready=True, tenant_binding_ready=True,
        credential_available=True, capability_enabled=True, probe_passed=True,
    )
    with pytest.raises(RuntimeError, match="CREDENTIAL_BROKER_REQUIRED"):
        build_production_service_bundle(
            ports=ports, provider_resolver=object(), readiness=readiness,
        )


def test_legacy_artifact_and_provider_singletons_are_not_in_runtime_assembly() -> None:
    source = (
        Path(__file__).parents[1]
        / "services/agent/runtime/production_composition.py"
    ).read_text()
    for forbidden in ("get_oss_service", "get_erp_credentials"):
        assert forbidden not in source
