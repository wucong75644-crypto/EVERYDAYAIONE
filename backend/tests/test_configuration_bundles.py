"""Fixed Bundle RPC and Secret material tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.configuration.bundles import SecretBundleResolver
from services.configuration.envelope import (
    KekVersionMissingError,
    SecretMaterialError,
)
from services.configuration.resolver import ConfigurationResolutionError


ORG_ID = "10000000-0000-0000-0000-000000000001"


class FakeDB:
    def __init__(self, data: object) -> None:
        self.data = data
        self.calls: list[tuple[str, object]] = []

    def rpc(self, name: str, params: object = None) -> SimpleNamespace:
        self.calls.append((name, params))
        return SimpleNamespace(
            execute=lambda: SimpleNamespace(data=self.data)
        )


def _erp_response() -> dict[str, object]:
    def secret(key: str) -> dict[str, object]:
        return {
            "key": key,
            "required": True,
            "configured": True,
            "source": "organization",
            "scope_id": ORG_ID,
            "version": 1,
            "value_kind": "secret",
            "secret_ref": {
                "secret_name": key,
                "payload_ciphertext": f"cipher-{key}",
                "wrapped_dek": f"wrapped-{key}",
                "kek_version": "local-v1",
                "payload_version": 1,
            },
        }

    return {
        "bundle": "erp.runtime",
        "definition_version": "v1",
        "items": [
            secret("erp.app_credentials"),
            secret("erp.token_pair"),
            {
                "key": "erp.warehouse_ids",
                "required": False,
                "configured": True,
                "source": "organization",
                "scope_id": ORG_ID,
                "version": 2,
                "value_kind": "json",
                "value_json": ["w1", "w2"],
            },
        ],
    }


def test_erp_bundle_decrypts_with_database_selected_scope_and_version() -> None:
    db = FakeDB(_erp_response())
    material = MagicMock()
    material.decrypt_payload.side_effect = (
        {"app_key": "app", "app_secret": "secret"},
        {"access_token": "access", "refresh_token": "refresh"},
    )

    result = SecretBundleResolver(db, material).erp_runtime()

    assert db.calls == [("get_erp_runtime_bundle", None)]
    assert result.values["erp.app_credentials"] == {
        "app_key": "app",
        "app_secret": "secret",
    }
    assert result.values["erp.warehouse_ids"] == ["w1", "w2"]
    assert result.sources["erp.token_pair"] == "organization"
    assert result.versions["erp.warehouse_ids"] == 2
    first_call = material.decrypt_payload.call_args_list[0]
    assert first_call.kwargs["scope_kind"] == "organization"
    assert first_call.kwargs["scope_id"] == ORG_ID
    assert first_call.kwargs["secret_name"] == "erp.app_credentials"
    assert first_call.args[0].payload_version == 1


def test_decrypted_payload_schema_mismatch_fails_closed() -> None:
    material = MagicMock()
    material.decrypt_payload.side_effect = (
        {"app_key": "app", "unexpected": "secret"},
    )

    with pytest.raises(
        ConfigurationResolutionError,
        match="SECRET_MATERIAL_UNAVAILABLE",
    ):
        SecretBundleResolver(FakeDB(_erp_response()), material).erp_runtime()


def test_missing_kek_version_keeps_stable_error_code() -> None:
    material = MagicMock()
    material.decrypt_payload.side_effect = KekVersionMissingError(
        "KEK_VERSION_MISSING"
    )

    with pytest.raises(
        ConfigurationResolutionError,
        match="KEK_VERSION_MISSING",
    ):
        SecretBundleResolver(FakeDB(_erp_response()), material).erp_runtime()


def test_secret_material_failure_keeps_stable_unavailable_code() -> None:
    material = MagicMock()
    material.decrypt_payload.side_effect = SecretMaterialError(
        "cipher details must not leak"
    )

    with pytest.raises(
        ConfigurationResolutionError,
        match="^SECRET_MATERIAL_UNAVAILABLE$",
    ):
        SecretBundleResolver(FakeDB(_erp_response()), material).erp_runtime()


def test_optional_missing_is_preserved_as_none() -> None:
    data = _erp_response()
    data["items"][2] = {
        "key": "erp.warehouse_ids",
        "required": False,
        "configured": False,
    }
    material = MagicMock()
    material.decrypt_payload.side_effect = (
        {"app_key": "app", "app_secret": "secret"},
        {"access_token": "access", "refresh_token": "refresh"},
    )

    result = SecretBundleResolver(FakeDB(data), material).erp_runtime()

    assert result.values["erp.warehouse_ids"] is None
    assert result.sources["erp.warehouse_ids"] is None
    assert result.versions["erp.warehouse_ids"] == 0


def test_database_error_is_mapped_without_leaking_message() -> None:
    class FailingDB:
        def rpc(self, _name: str):
            raise RuntimeError(
                "CONFIG_BUNDLE_AUTHORITY_DENIED token=must-not-leak"
            )

    with pytest.raises(ConfigurationResolutionError) as captured:
        SecretBundleResolver(FailingDB(), MagicMock()).ai_google()

    assert str(captured.value) == "CONFIG_BUNDLE_AUTHORITY_DENIED"
    assert "must-not-leak" not in str(captured.value)


def test_unknown_database_error_uses_generic_secret_free_code() -> None:
    class FailingDB:
        def rpc(self, _name: str):
            raise RuntimeError("driver password=must-not-leak")

    with pytest.raises(ConfigurationResolutionError) as captured:
        SecretBundleResolver(FailingDB(), MagicMock()).ai_google()

    assert str(captured.value) == "CONFIG_BUNDLE_UNAVAILABLE"
    assert "must-not-leak" not in str(captured.value)


@pytest.mark.parametrize(
    ("method", "rpc_name"),
    (
        ("ai_dashscope", "get_ai_dashscope_bundle"),
        ("ai_openrouter", "get_ai_openrouter_bundle"),
        ("ai_kie", "get_ai_kie_bundle"),
        ("ai_google", "get_ai_google_bundle"),
        ("erp_runtime", "get_erp_runtime_bundle"),
        ("wecom_bot", "get_wecom_bot_bundle"),
        ("wecom_oauth_public", "get_wecom_oauth_public_bundle"),
        ("wecom_oauth_exchange", "get_wecom_oauth_exchange_bundle"),
        ("wecom_contact", "get_wecom_contact_bundle"),
        ("kuaimai_thinktank", "get_kuaimai_thinktank_bundle"),
        ("kuaimai_viperp", "get_kuaimai_viperp_bundle"),
    ),
)
def test_public_methods_call_only_their_fixed_rpc(
    method: str,
    rpc_name: str,
) -> None:
    effective = MagicMock()
    effective.parse.side_effect = ConfigurationResolutionError("stop")
    db = FakeDB({})

    with pytest.raises(ConfigurationResolutionError, match="stop"):
        getattr(SecretBundleResolver(db, MagicMock(), effective), method)()

    assert db.calls == [(rpc_name, None)]
