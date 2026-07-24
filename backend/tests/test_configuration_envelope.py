"""Envelope encryption provider and Secret material behavior tests."""

from __future__ import annotations

import base64
from dataclasses import replace
import json
from pathlib import Path

import pytest

from services.configuration.envelope import (
    KekVersionMissingError,
    LocalKEKProvider,
    SecretMaterialError,
)
from services.configuration.material_service import SecretMaterialService


ORG_ID = "00000000-0000-0000-0000-000000000010"
ROOT = Path(__file__).resolve().parents[2]
KEK_TEMPLATE = ROOT / "deploy/env-templates/kek.env.template"


def _key(seed: int) -> bytes:
    return bytes((seed + offset) % 256 for offset in range(32))


def _provider(
    current_version: str = "v2",
) -> LocalKEKProvider:
    return LocalKEKProvider(
        current_version=current_version,
        keyring={"v1": _key(1), "v2": _key(2)},
    )


def test_secret_payload_round_trip_is_randomized_and_scope_bound() -> None:
    service = SecretMaterialService(_provider())
    arguments = {
        "scope_kind": "organization",
        "scope_id": ORG_ID,
        "secret_name": "erp.token_pair",
        "payload_version": 3,
        "payload": {"access_token": "access", "refresh_token": "refresh"},
    }

    first = service.encrypt_payload(**arguments)
    second = service.encrypt_payload(**arguments)

    assert first.payload_ciphertext != second.payload_ciphertext
    assert first.wrapped_dek != second.wrapped_dek
    assert first.kek_version == "v2"
    assert service.decrypt_payload(
        first,
        scope_kind="organization",
        scope_id=ORG_ID,
        secret_name="erp.token_pair",
    ) == arguments["payload"]


@pytest.mark.parametrize(
    ("scope_kind", "scope_id", "secret_name", "payload_version"),
    (
        ("user", ORG_ID, "erp.token_pair", 3),
        ("organization", ORG_ID, "erp.other", 3),
        ("organization", ORG_ID, "erp.token_pair", 4),
    ),
)
def test_payload_aad_rejects_context_substitution(
    scope_kind: str,
    scope_id: str,
    secret_name: str,
    payload_version: int,
) -> None:
    service = SecretMaterialService(_provider())
    envelope = service.encrypt_payload(
        scope_kind="organization",
        scope_id=ORG_ID,
        secret_name="erp.token_pair",
        payload_version=3,
        payload={"access_token": "access", "refresh_token": "refresh"},
    )

    with pytest.raises(
        SecretMaterialError,
        match="SECRET_MATERIAL_UNAVAILABLE",
    ):
        service.decrypt_payload(
            replace(envelope, payload_version=payload_version),
            scope_kind=scope_kind,  # type: ignore[arg-type]
            scope_id=scope_id,
            secret_name=secret_name,
        )


def test_previous_kek_can_decrypt_after_current_version_rotates() -> None:
    old_service = SecretMaterialService(_provider("v1"))
    envelope = old_service.encrypt_payload(
        scope_kind="platform",
        scope_id=None,
        secret_name="ai.google_api_key",
        payload_version=1,
        payload={"api_key": "test-key"},
    )

    rotated_service = SecretMaterialService(_provider("v2"))

    assert rotated_service.decrypt_payload(
        envelope,
        scope_kind="platform",
        scope_id=None,
        secret_name="ai.google_api_key",
    ) == {"api_key": "test-key"}


def test_missing_kek_version_and_tampered_material_fail_closed() -> None:
    provider = _provider()
    service = SecretMaterialService(provider)
    envelope = service.encrypt_payload(
        scope_kind="organization",
        scope_id=ORG_ID,
        secret_name="wecom.bot_credentials",
        payload_version=1,
        payload={"bot_id": "id", "bot_secret": "secret"},
    )

    with pytest.raises(KekVersionMissingError, match="KEK_VERSION_MISSING"):
        service.decrypt_payload(
            replace(envelope, kek_version="missing"),
            scope_kind="organization",
            scope_id=ORG_ID,
            secret_name="wecom.bot_credentials",
        )
    with pytest.raises(
        SecretMaterialError,
        match="SECRET_MATERIAL_UNAVAILABLE",
    ):
        service.decrypt_payload(
            replace(envelope, wrapped_dek="invalid"),
            scope_kind="organization",
            scope_id=ORG_ID,
            secret_name="wecom.bot_credentials",
        )


def test_environment_keyring_accepts_current_and_previous_keys() -> None:
    environment = {
        "CONFIG_KEK_CURRENT_VERSION": "v2",
        "CONFIG_KEK_KEYRING_JSON": json.dumps({
            "v1": base64.b64encode(_key(1)).decode("ascii"),
            "v2": base64.b64encode(_key(2)).decode("ascii"),
        }),
    }

    provider = LocalKEKProvider.from_environment(environment)

    assert provider.current_version == "v2"


@pytest.mark.parametrize(
    "environment",
    (
        {},
        {
            "CONFIG_KEK_CURRENT_VERSION": "v1",
            "CONFIG_KEK_KEYRING_JSON": "not-json",
        },
        {
            "CONFIG_KEK_CURRENT_VERSION": "v2",
            "CONFIG_KEK_KEYRING_JSON": json.dumps({
                "v1": base64.b64encode(_key(1)).decode("ascii"),
            }),
        },
        {
            "CONFIG_KEK_CURRENT_VERSION": "v1",
            "CONFIG_KEK_KEYRING_JSON": '{"v1":"dG9vLXNob3J0"}',
        },
    ),
)
def test_invalid_environment_keyrings_fail_at_initialization(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="CONFIG_KEK_"):
        LocalKEKProvider.from_environment(environment)


@pytest.mark.parametrize(
    ("scope_kind", "scope_id", "payload_version", "payload"),
    (
        ("platform", ORG_ID, 1, {"api_key": "key"}),
        ("organization", None, 1, {"api_key": "key"}),
        ("organization", "not-a-uuid", 1, {"api_key": "key"}),
        ("organization", ORG_ID, 0, {"api_key": "key"}),
        ("organization", ORG_ID, 1, {}),
        ("organization", ORG_ID, 1, {"value": float("nan")}),
    ),
)
def test_invalid_payload_context_fails_before_encryption(
    scope_kind: str,
    scope_id: str | None,
    payload_version: int,
    payload: dict[str, object],
) -> None:
    service = SecretMaterialService(_provider())
    with pytest.raises(ValueError):
        service.encrypt_payload(
            scope_kind=scope_kind,  # type: ignore[arg-type]
            scope_id=scope_id,
            secret_name="ai.google_api_key",
            payload_version=payload_version,
            payload=payload,
        )


def test_secret_name_rejects_surrounding_whitespace() -> None:
    service = SecretMaterialService(_provider())
    with pytest.raises(ValueError, match="CONFIG_VALUE_INVALID"):
        service.encrypt_payload(
            scope_kind="organization",
            scope_id=ORG_ID,
            secret_name=" erp.token_pair ",
            payload_version=1,
            payload={"access_token": "access", "refresh_token": "refresh"},
        )


def test_kek_template_contains_only_placeholders_and_is_gitignored() -> None:
    template = KEK_TEMPLATE.read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "CONFIG_KEK_CURRENT_VERSION=v1" in template
    assert "<base64-encoded-32-byte-kek>" in template
    assert ".env.*" in gitignore
