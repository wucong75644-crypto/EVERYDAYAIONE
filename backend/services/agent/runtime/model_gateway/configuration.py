"""Gateway-only encrypted configuration projection and Secret consumer."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, TypeVar

from services.configuration.definitions import CONFIG_REGISTRY
from services.configuration.envelope import (
    KekVersionMissingError,
    LocalKEKProvider,
    SecretMaterialError,
)
from services.configuration.material_service import SecretMaterialService
from services.configuration.resolver import (
    ConfigurationResolutionError,
    EffectiveConfigResolver,
)


_BUNDLES = {
    "dashscope": ("ai.provider.dashscope", "ai.dashscope.api_key"),
    "openrouter": ("ai.provider.openrouter", "ai.openrouter.api_key"),
    "kie": ("ai.provider.kie", "ai.kie.api_key"),
    "google": ("ai.provider.google", "ai.google.api_key"),
}
T = TypeVar("T")
SecretConsumer = Callable[[str], AsyncIterator[T]]


class GatewayConfigurationError(RuntimeError):
    """Stable pre-dispatch failure that never retains configuration material."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"GatewayConfigurationError(code={self.code!r})"


class GatewaySecretBundleConsumer:
    """Decrypt one claimed bundle only for one async Provider consumer."""

    def __init__(self, material_service: SecretMaterialService) -> None:
        self._material_service = material_service
        self._resolver = EffectiveConfigResolver()

    def __repr__(self) -> str:
        return "GatewaySecretBundleConsumer(<redacted>)"

    def __getstate__(self) -> Mapping[str, object]:
        raise TypeError("GATEWAY_SECRET_CONSUMER_NOT_SERIALIZABLE")

    def __reduce__(self) -> object:
        raise TypeError("GATEWAY_SECRET_CONSUMER_NOT_SERIALIZABLE")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("GATEWAY_SECRET_CONSUMER_NOT_SERIALIZABLE")

    def __copy__(self) -> "GatewaySecretBundleConsumer":
        raise TypeError("GATEWAY_SECRET_CONSUMER_NOT_COPYABLE")

    def __deepcopy__(self, _memo: object) -> "GatewaySecretBundleConsumer":
        raise TypeError("GATEWAY_SECRET_CONSUMER_NOT_COPYABLE")

    async def consume(
        self,
        encrypted_bundle: object,
        *,
        provider: str,
        consumer: SecretConsumer[T],
    ) -> AsyncIterator[T]:
        material: str | None = None
        stream: AsyncIterator[T] | None = None
        try:
            material = self._resolve_material(encrypted_bundle, provider)
            stream = consumer(material)
            async for item in stream:
                yield item
        finally:
            if stream is not None:
                await stream.aclose()
            material = None

    def _resolve_material(self, encrypted_bundle: object, provider: str) -> str:
        expected = _BUNDLES.get(provider)
        if expected is None:
            raise GatewayConfigurationError("GATEWAY_PROVIDER_UNSUPPORTED")
        bundle_name, key = expected
        try:
            effective = self._resolver.parse(bundle_name, encrypted_bundle)
            item = effective.items[key]
            reference = item.secret_ref
            if not item.configured or reference is None:
                raise GatewayConfigurationError("GATEWAY_CONFIGURATION_INVALID")
            payload = self._material_service.decrypt_payload(
                reference.envelope,
                scope_kind=reference.source,
                scope_id=reference.scope_id,
                secret_name=reference.secret_name,
            )
            definition = CONFIG_REGISTRY.get(key)
            required = set(definition.validation.get("required", ()))
            if set(payload) != required:
                raise GatewayConfigurationError("GATEWAY_CONFIGURATION_INVALID")
            material = payload.get("api_key")
            if not isinstance(material, str) or not material:
                raise GatewayConfigurationError("GATEWAY_CONFIGURATION_INVALID")
            return material
        except GatewayConfigurationError:
            raise
        except KekVersionMissingError:
            raise GatewayConfigurationError("GATEWAY_KEK_UNAVAILABLE") from None
        except SecretMaterialError:
            raise GatewayConfigurationError("GATEWAY_SECRET_DECRYPT_FAILED") from None
        except (ConfigurationResolutionError, KeyError, TypeError, ValueError):
            raise GatewayConfigurationError("GATEWAY_CONFIGURATION_INVALID") from None


def validate_claim_projection(
    request: Mapping[str, Any], claim: Mapping[str, object],
) -> None:
    receipt = claim.get("input_receipt")
    if not isinstance(receipt, Mapping):
        raise GatewayConfigurationError("GATEWAY_CONFIGURATION_INVALID")
    input_value = request["input"]
    digest = hashlib.sha256(json.dumps(
        {"messages": input_value["messages"], "tools": input_value["tools"]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    valid = (
        hmac.compare_digest(str(receipt.get("request_hash") or ""), request["request_hash"])
        and hmac.compare_digest(str(receipt.get("prefix_hash") or ""), digest)
        and hmac.compare_digest(input_value["context_receipt_hash"], digest)
        and receipt.get("message_count") == len(input_value["messages"])
        and receipt.get("tool_count") == len(input_value["tools"])
    )
    if not valid:
        raise GatewayConfigurationError("GATEWAY_CONFIGURATION_INVALID")


def build_gateway_secret_consumer(
    environ: Mapping[str, str] | None = None,
) -> GatewaySecretBundleConsumer:
    """Construct the KEK boundary without loading application Settings."""
    try:
        kek = LocalKEKProvider.from_environment(environ)
    except ValueError:
        raise GatewayConfigurationError("GATEWAY_KEK_UNAVAILABLE") from None
    return GatewaySecretBundleConsumer(SecretMaterialService(kek))


__all__ = [
    "GatewayConfigurationError",
    "GatewaySecretBundleConsumer",
    "build_gateway_secret_consumer",
    "validate_claim_projection",
]
