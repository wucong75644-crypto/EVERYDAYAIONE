from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.agent.runtime.context import ProviderContextPlan
from services.agent.runtime.application.model_loop import ModelLoopDriver
from services.agent.runtime.infrastructure.model.configured_adapter import (
    RuntimeConfiguredAdapterFactory,
    build_runtime_configured_adapter_factory,
)
from services.circuit_breaker import reset_all
from services.configuration.resolver import ConfigurationResolutionError
from services.agent.runtime.ports.model import (
    ModelExecutionBinding,
    ModelInputReceipt,
    ModelRequestOptions,
    ModelStepRequest,
)
from services.agent.runtime.ports.model_attempt import (
    ModelAttemptOutcome,
    ModelAttemptReceipt,
)


def _request(*, bound: bool = True) -> ModelStepRequest:
    plan = ProviderContextPlan.build(
        messages=[{"role": "user", "content": "safe"}], tools=[],
        context_epoch_id="epoch", model_step=1, stable_prefix_blocks=0,
    )
    return ModelStepRequest(
        model_step_id="step", model_id="qwen3.5-plus",
        request_hash="a" * 64,
        input_receipt=ModelInputReceipt(
            receipt_id="receipt", receipt_hash="b" * 64,
            context_plan_hash=plan.plan_hash,
        ),
        context_plan=plan, model_revision="model-revision",
        prompt_revision="prompt-revision",
        tool_catalog_revision="catalog-revision",
        options=ModelRequestOptions(timeout_seconds=45), org_id="org-a",
        execution_binding=(
            ModelExecutionBinding(
                run_id="run-a", attempt_id="attempt-a", worker_id="worker-a",
                execution_token="token-a", attempt_state_version=8,
            ) if bound else None
        ),
    )


class _Resolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def runtime_model(self, name, params):
        self.calls.append((name, dict(params)))
        return SimpleNamespace(values={
            "ai.dashscope.api_key": {"api_key": "secret-material"},
        })


@pytest.mark.asyncio
async def test_factory_reuses_governed_bundle_and_existing_adapter() -> None:
    resolver = _Resolver()
    calls: list[tuple[object, dict[str, object]]] = []

    def existing_factory(model_id, **kwargs):
        calls.append((model_id, kwargs))
        return object()

    factory = RuntimeConfiguredAdapterFactory(
        resolver, adapter_factory=existing_factory,
    )

    await factory(_request())

    assert resolver.calls == [(
        "ai.provider.dashscope",
        {
            "p_run_id": "run-a", "p_attempt_id": "attempt-a",
            "p_worker_id": "worker-a",
            "p_execution_token": "token-a",
            "p_expected_attempt_version": 8,
            "p_request_hash": "a" * 64,
            "p_bundle_name": "ai.provider.dashscope",
        },
    )]
    assert calls == [("qwen3.5-plus", {
        "stream_timeout": 45, "org_id": "org-a",
        "api_key_override": "secret-material",
    })]


@pytest.mark.asyncio
async def test_factory_rejects_unbound_request_before_config_read() -> None:
    resolver = _Resolver()
    factory = RuntimeConfiguredAdapterFactory(
        resolver, adapter_factory=lambda *_args, **_kwargs: object(),
    )

    with pytest.raises(
        RuntimeError, match="RUNTIME_MODEL_EXECUTION_BINDING_REQUIRED",
    ):
        await factory(_request(bound=False))

    assert resolver.calls == []


@pytest.mark.asyncio
async def test_factory_preserves_configuration_authority_error_code() -> None:
    class Resolver(_Resolver):
        async def runtime_model(self, _name, _params):
            raise ConfigurationResolutionError(
                "CONFIG_BUNDLE_AUTHORITY_DENIED",
            )

    factory = RuntimeConfiguredAdapterFactory(
        Resolver(), adapter_factory=lambda *_args, **_kwargs: object(),
    )

    with pytest.raises(
        RuntimeError,
        match="RUNTIME_MODEL_CONFIGURATION_UNAVAILABLE:CONFIG_BUNDLE_AUTHORITY_DENIED",
    ):
        await factory(_request())


@pytest.mark.asyncio
async def test_runtime_factory_never_reads_common_application_settings() -> None:
    reset_all()
    factory = build_runtime_configured_adapter_factory(_Resolver())
    try:
        with patch(
            "services.adapters.factory.get_settings",
            side_effect=PermissionError("application dotenv blocked"),
        ), patch(
            "core.config.get_settings",
            side_effect=PermissionError("application dotenv blocked"),
        ):
            adapter = await factory(_request())
        assert adapter.provider.value == "dashscope"
        assert adapter._base_url == (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
    finally:
        reset_all()


@pytest.mark.asyncio
async def test_direct_model_dispatch_attaches_claimed_run_binding() -> None:
    class Attempts:
        async def start_dispatch(self, **_values):
            return ModelAttemptReceipt(
                outcome=ModelAttemptOutcome.DISPATCHING,
                state_version=8,
            )

    driver = ModelLoopDriver(
        runtime_repository=object(), attempt_repository=Attempts(),
        action_repository=object(), recovery_repository=object(),
        model=object(), call_factory=object(), reconciler=object(),
    )

    request, version = await driver._start_dispatch(
        request=_request(bound=False), attempt_id="attempt-a",
        attempt_version=7, run_id="run-a",
        run_execution_token="token-a", worker_id="worker-a",
    )

    assert version == 8
    assert request.execution_binding == ModelExecutionBinding(
        run_id="run-a", attempt_id="attempt-a", worker_id="worker-a",
        execution_token="token-a", attempt_state_version=8,
    )
