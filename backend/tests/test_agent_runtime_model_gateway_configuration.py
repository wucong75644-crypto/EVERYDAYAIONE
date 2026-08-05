from __future__ import annotations

import json
import pickle

import pytest

from services.configuration.definitions import CONFIG_REGISTRY
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService
from services.agent.runtime.model_gateway.configuration import (
    GatewayConfigurationError,
    GatewaySecretBundleConsumer,
)


ORG_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"
SECRET = "gateway-secret-marker-9c64"


def _material(seed: int = 7) -> SecretMaterialService:
    return SecretMaterialService(LocalKEKProvider(
        current_version="v1",
        keyring={"v1": bytes([seed]) * 32},
    ))


def _bundle(
    source: str = "organization", *, service: SecretMaterialService | None = None,
) -> dict[str, object]:
    scope_id = {
        "organization": ORG_ID,
        "user": USER_ID,
        "platform": None,
    }[source]
    material = service or _material()
    envelope = material.encrypt_payload(
        scope_kind=source,
        scope_id=scope_id,
        secret_name="ai.dashscope_api_key",
        payload_version=1,
        payload={"api_key": SECRET},
    )
    return {
        "bundle": "ai.provider.dashscope",
        "definition_version": CONFIG_REGISTRY.version,
        "items": [{
            "key": "ai.dashscope.api_key",
            "required": True,
            "configured": True,
            "source": source,
            "scope_id": scope_id,
            "version": 1,
            "value_kind": "secret",
            "secret_ref": {
                "secret_name": "ai.dashscope_api_key",
                "payload_ciphertext": envelope.payload_ciphertext,
                "wrapped_dek": envelope.wrapped_dek,
                "kek_version": envelope.kek_version,
                "payload_version": envelope.payload_version,
            },
        }],
    }


async def _consume(
    consumer: GatewaySecretBundleConsumer,
    bundle: object,
    *,
    provider: str = "dashscope",
) -> tuple[list[dict[str, str]], list[str]]:
    seen: list[str] = []

    async def use(material: str):
        seen.append(material)
        yield {"status": "used"}

    result = [item async for item in consumer.consume(
        bundle, provider=provider, consumer=use,
    )]
    return result, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ("organization", "user", "platform"))
async def test_existing_scope_fallback_projection_decrypts_only_in_consumer(
    source: str,
) -> None:
    consumer = GatewaySecretBundleConsumer(_material())

    result, seen = await _consume(consumer, _bundle(source))

    assert seen == [SECRET]
    assert result == [{"status": "used"}]
    public = json.dumps(result) + repr(consumer) + repr(result) + pickle.dumps(result).hex()
    assert SECRET not in public


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("wrong_key", "GATEWAY_SECRET_DECRYPT_FAILED"),
        ("wrong_version", "GATEWAY_KEK_UNAVAILABLE"),
        ("wrong_envelope", "GATEWAY_SECRET_DECRYPT_FAILED"),
        ("wrong_bundle", "GATEWAY_CONFIGURATION_INVALID"),
        ("wrong_provider", "GATEWAY_PROVIDER_UNSUPPORTED"),
    ),
)
async def test_invalid_secret_inputs_fail_closed_without_material_in_error(
    mutation: str, expected: str,
) -> None:
    decryptor = _material()
    encrypted_by = _material(8) if mutation == "wrong_key" else decryptor
    bundle = _bundle(service=encrypted_by)
    provider = "dashscope"
    if mutation == "wrong_version":
        bundle["items"][0]["secret_ref"]["kek_version"] = "missing"
    elif mutation == "wrong_envelope":
        bundle["items"][0]["secret_ref"]["payload_ciphertext"] = "invalid"
    elif mutation == "wrong_bundle":
        bundle["bundle"] = "ai.provider.google"
    elif mutation == "wrong_provider":
        provider = "unsupported"

    with pytest.raises(GatewayConfigurationError) as caught:
        await _consume(
            GatewaySecretBundleConsumer(decryptor), bundle, provider=provider,
        )

    assert caught.value.code == expected
    serialized = repr(caught.value) + str(caught.value) + pickle.dumps(caught.value).hex()
    assert SECRET not in serialized
