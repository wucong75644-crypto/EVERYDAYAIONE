"""T17.7 credential boundary contracts for Runtime-owned provider calls."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from services.agent.runtime.domain import ModelStepId
from services.agent.runtime.infrastructure.model import (
    ExistingProviderModelAdapter, compute_request_hash, resolve_model_revision,
)
from services.agent.runtime.ports import (
    ModelInputReceipt, ModelRequestOptions, ModelStepRequest,
)
from services.agent.runtime.context import ProviderContextPlan


def _request() -> ModelStepRequest:
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
        options=options, org_id="org-a",
    )


@pytest.mark.asyncio
async def test_runtime_provider_builder_requires_configured_factory() -> None:
    adapter = ExistingProviderModelAdapter(db=object())
    with pytest.raises(ValueError, match="RUNTIME_MODEL_ADAPTER_FACTORY_REQUIRED"):
        await adapter._create_adapter(_request())


def test_model_request_has_no_raw_api_key_field() -> None:
    names = {item.name for item in fields(ModelStepRequest)}
    assert not names & {
        "provider_api_key", "credential_lease", "credential_scope",
        "credential_purpose", "credential_handle", "credential_material",
    }


def test_runtime_credential_sources_are_not_imported_by_provider_boundary() -> None:
    root = Path(__file__).parents[1] / "services/agent/runtime"
    sources = (
        root / "production_model.py",
        root / "infrastructure/model/adapter.py",
        root / "production_composition.py",
        root / "composition.py",
    )
    forbidden = (
        "get_settings()", "provider_api_key",
        "get_oss_service", "OrgConfigResolver", "RedisClient",
    )
    for source in sources:
        text = source.read_text()
        assert not any(item in text for item in forbidden), source
    adapter_source = (root / "infrastructure/model/adapter.py").read_text()
    assert "create_chat_adapter" not in adapter_source
    strict_sources = (
        root / "production_model.py", root / "production_factory.py",
    )
    strict_forbidden = (
        "CredentialBroker", "CredentialLease", "LocalKEKProvider",
        "create_chat_adapter",
    )
    for source in strict_sources:
        text = source.read_text()
        assert not any(item in text for item in strict_forbidden), source


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
