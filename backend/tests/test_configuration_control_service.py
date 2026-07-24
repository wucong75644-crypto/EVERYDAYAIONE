"""Configuration control orchestration and Registry startup verification."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.configuration.control_service import (
    ConfigurationControlError,
    ConfigurationControlService,
    verify_configuration_registry,
)
from services.configuration.definitions import (
    BUNDLE_DEFINITIONS,
    CONFIG_DEFINITIONS,
    CONFIG_REGISTRY,
)
from services.configuration.envelope import SecretEnvelope


class FakeDB:
    def __init__(self, results: dict[str, object] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(
        self,
        name: str,
        params: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        arguments = params or {}
        self.calls.append((name, arguments))
        result = self.results.get(name, {"key": "key", "version": 1})
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(
            execute=lambda: SimpleNamespace(data=result)
        )


def _registry_rows() -> list[dict[str, str]]:
    return [
        {
            "definition_version": CONFIG_REGISTRY.version,
            "config_key": key,
            "contract_hash": definition.contract_hash(),
        }
        for key, definition in CONFIG_DEFINITIONS.items()
    ]


def _bundle_registry_rows() -> list[dict[str, str]]:
    return [
        {
            "definition_version": CONFIG_REGISTRY.version,
            "bundle_name": name,
            "contract_hash": bundle.contract_hash(),
        }
        for name, bundle in BUNDLE_DEFINITIONS.items()
    ]


def _service(
    db: FakeDB | None = None,
) -> tuple[ConfigurationControlService, FakeDB, MagicMock]:
    fake_db = db or FakeDB()
    material = MagicMock()
    material.encrypt_payload.return_value = SecretEnvelope(
        payload_ciphertext="ciphertext",
        wrapped_dek="wrapped",
        kek_version="v1",
        payload_version=1,
    )
    return ConfigurationControlService(fake_db, material), fake_db, material


def test_registry_verification_requires_exact_version_key_and_hash() -> None:
    verify_configuration_registry(FakeDB({
        "get_configuration_registry_contract": _registry_rows(),
        "get_configuration_bundle_registry_contract": _bundle_registry_rows(),
    }))

    drift = _registry_rows()
    drift[0] = {**drift[0], "contract_hash": "0" * 64}
    with pytest.raises(
        ConfigurationControlError,
        match="CONFIG_REGISTRY_DRIFT",
    ):
        verify_configuration_registry(FakeDB({
            "get_configuration_registry_contract": drift,
            "get_configuration_bundle_registry_contract": (
                _bundle_registry_rows()
            ),
        }))


def test_bundle_registry_drift_fails_startup_closed() -> None:
    drift = _bundle_registry_rows()
    drift[0] = {**drift[0], "contract_hash": "0" * 64}

    with pytest.raises(
        ConfigurationControlError,
        match="CONFIG_REGISTRY_DRIFT",
    ):
        verify_configuration_registry(FakeDB({
            "get_configuration_registry_contract": _registry_rows(),
            "get_configuration_bundle_registry_contract": drift,
        }))


def test_registry_unavailable_fails_startup_closed() -> None:
    with pytest.raises(
        ConfigurationControlError,
        match="CONFIG_REGISTRY_UNAVAILABLE",
    ):
        verify_configuration_registry(FakeDB({
            "get_configuration_registry_contract": RuntimeError("offline"),
            "get_configuration_bundle_registry_contract": (
                _bundle_registry_rows()
            ),
        }))


def test_secret_write_encrypts_next_version_without_sending_plaintext() -> None:
    service, db, material = _service()
    payload = {"access_token": "access", "refresh_token": "refresh"}

    service.set_organization(
        org_id="00000000-0000-0000-0000-000000000010",
        key="erp.token_pair",
        value=payload,
        expected_version=4,
    )

    material.encrypt_payload.assert_called_once_with(
        scope_kind="organization",
        scope_id="00000000-0000-0000-0000-000000000010",
        secret_name="erp.token_pair",
        payload_version=5,
        payload=payload,
    )
    name, params = db.calls[-1]
    assert name == "set_org_configuration"
    assert params["p_value_json"] is None
    assert params["p_secret_envelope"] == {
        "payload_ciphertext": "ciphertext",
        "wrapped_dek": "wrapped",
        "kek_version": "v1",
    }
    assert "access" not in str(params)
    assert "refresh" not in str(params)


def test_user_secret_write_sends_actor_id_for_database_recheck() -> None:
    service, db, _ = _service()
    user_id = "00000000-0000-0000-0000-000000000001"

    service.set_user(
        user_id=user_id,
        key="ai.google.api_key",
        value={"api_key": "key"},
        expected_version=0,
    )

    name, params = db.calls[-1]
    assert name == "set_user_configuration"
    assert params["p_user_id"] == user_id


def test_plain_write_uses_value_json_without_envelope() -> None:
    service, db, material = _service()

    service.set_organization(
        org_id="00000000-0000-0000-0000-000000000010",
        key="erp.warehouse_ids",
        value=["1", "2"],
        expected_version=0,
    )

    _, params = db.calls[-1]
    assert params["p_value_json"] == ["1", "2"]
    assert params["p_secret_envelope"] is None
    material.encrypt_payload.assert_not_called()


@pytest.mark.parametrize(
    ("method", "kwargs", "code"),
    (
        (
            "set_user",
            {
                "user_id": "00000000-0000-0000-0000-000000000001",
                "key": "erp.token_pair",
                "value": {
                    "access_token": "access",
                    "refresh_token": "refresh",
                },
                "expected_version": 0,
            },
            "CONFIG_SCOPE_FORBIDDEN",
        ),
        (
            "set_platform",
            {
                "key": "unknown",
                "value": "value",
                "expected_version": 0,
            },
            "CONFIG_KEY_UNKNOWN",
        ),
        (
            "set_organization",
            {
                "org_id": "00000000-0000-0000-0000-000000000010",
                "key": "erp.token_pair",
                "value": {"access_token": "missing-refresh"},
                "expected_version": 0,
            },
            "CONFIG_VALUE_INVALID",
        ),
        (
            "set_organization",
            {
                "org_id": "00000000-0000-0000-0000-000000000010",
                "key": "erp.warehouse_ids",
                "value": ["1", "1"],
                "expected_version": 0,
            },
            "CONFIG_VALUE_INVALID",
        ),
    ),
)
def test_invalid_values_and_scopes_fail_before_rpc(
    method: str,
    kwargs: dict[str, object],
    code: str,
) -> None:
    service, db, _ = _service()

    with pytest.raises(ConfigurationControlError, match=code):
        getattr(service, method)(**kwargs)

    assert db.calls == []


def test_database_errors_map_to_stable_configuration_errors() -> None:
    service, _, _ = _service(FakeDB({
        "delete_platform_configuration": RuntimeError(
            "CONFIG_VERSION_CONFLICT"
        ),
    }))

    with pytest.raises(ConfigurationControlError) as captured:
        service.delete_platform(key="ai.google.api_key", expected_version=2)

    assert captured.value.code == "CONFIG_VERSION_CONFLICT"
    assert captured.value.status_code == 409


def test_status_requires_list_shape() -> None:
    service, _, _ = _service(FakeDB({
        "list_user_configuration_status": {"invalid": True},
    }))

    with pytest.raises(
        ConfigurationControlError,
        match="CONFIG_RESPONSE_INVALID",
    ):
        service.list_user_status()
