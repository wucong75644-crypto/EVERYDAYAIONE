"""Run-bound adapter construction through the existing configuration plane."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from services.agent.runtime.ports.model import ModelStepRequest
from services.configuration.bundles import AsyncSecretBundleResolver
from services.configuration.resolver import ConfigurationResolutionError


_PROVIDER_BUNDLES = {
    "dashscope": ("ai.provider.dashscope", "ai.dashscope.api_key"),
    "openrouter": ("ai.provider.openrouter", "ai.openrouter.api_key"),
    "kie": ("ai.provider.kie", "ai.kie.api_key"),
    "google": ("ai.provider.google", "ai.google.api_key"),
}


class RuntimeConfiguredAdapterFactory:
    """Resolve one claimed Run's existing AI Bundle and build its adapter."""

    def __init__(
        self,
        resolver: AsyncSecretBundleResolver,
        *,
        adapter_factory: Callable[..., Any],
    ) -> None:
        self._resolver = resolver
        self._adapter_factory = adapter_factory

    async def __call__(self, request: ModelStepRequest) -> Any:
        binding = request.execution_binding
        if binding is None:
            raise RuntimeError("RUNTIME_MODEL_EXECUTION_BINDING_REQUIRED")
        provider = _provider_for(request.model_id)
        bundle_spec = _PROVIDER_BUNDLES.get(provider)
        if bundle_spec is None:
            raise RuntimeError("RUNTIME_MODEL_PROVIDER_UNSUPPORTED")
        bundle_name, config_key = bundle_spec
        try:
            bundle = await self._resolver.runtime_model(bundle_name, {
                "p_run_id": binding.run_id,
                "p_attempt_id": binding.attempt_id,
                "p_worker_id": binding.worker_id,
                "p_execution_token": binding.execution_token,
                "p_expected_attempt_version": binding.attempt_state_version,
                "p_request_hash": request.request_hash,
                "p_bundle_name": bundle_name,
            })
        except ConfigurationResolutionError as error:
            code = str(error).strip() or "CONFIG_BUNDLE_UNAVAILABLE"
            raise RuntimeError(
                f"RUNTIME_MODEL_CONFIGURATION_UNAVAILABLE:{code}"
            ) from None
        api_key = _api_key(bundle.values.get(config_key))
        return self._adapter_factory(
            request.model_id,
            stream_timeout=request.options.timeout_seconds,
            org_id=request.org_id,
            api_key_override=api_key,
        )


def _provider_for(model_id: str) -> str:
    from services.adapters.factory import get_model_config

    config = get_model_config(model_id)
    if config is None:
        raise RuntimeError("RUNTIME_MODEL_PROVIDER_UNSUPPORTED")
    return config.provider.value


def _api_key(value: object) -> str:
    if not isinstance(value, Mapping):
        raise RuntimeError("RUNTIME_MODEL_API_KEY_MISSING")
    api_key = value.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise RuntimeError("RUNTIME_MODEL_API_KEY_MISSING")
    return api_key


__all__ = ["RuntimeConfiguredAdapterFactory"]
