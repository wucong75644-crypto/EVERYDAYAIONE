"""Configuration registry verification and scoped management orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.exceptions import AppException
from services.configuration.definitions import (
    BUNDLE_DEFINITIONS,
    CONFIG_DEFINITIONS,
    CONFIG_REGISTRY,
    ConfigDefinition,
    ScopeKind,
)
from services.configuration.material_service import SecretMaterialService


class ConfigurationControlError(AppException):
    """Stable configuration management error."""

    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code=code, message=code, status_code=status_code)


def verify_configuration_registry(db: Any) -> None:
    """Fail startup when the active database projection differs from code."""
    definition_rows = _read_registry_contract(
        db,
        "get_configuration_registry_contract",
    )
    bundle_rows = _read_registry_contract(
        db,
        "get_configuration_bundle_registry_contract",
    )
    _assert_registry_contract(
        definition_rows,
        "config_key",
        {
            key: (CONFIG_REGISTRY.version, definition.contract_hash())
            for key, definition in CONFIG_DEFINITIONS.items()
        },
    )
    _assert_registry_contract(
        bundle_rows,
        "bundle_name",
        {
            name: (CONFIG_REGISTRY.version, bundle.contract_hash())
            for name, bundle in BUNDLE_DEFINITIONS.items()
        },
    )


def _read_registry_contract(db: Any, rpc_name: str) -> list[object]:
    try:
        response = db.rpc(rpc_name).execute()
    except Exception as error:
        raise ConfigurationControlError(
            "CONFIG_REGISTRY_UNAVAILABLE",
            503,
        ) from error
    if not isinstance(response.data, list):
        raise ConfigurationControlError("CONFIG_REGISTRY_DRIFT", 503)
    return response.data


def _assert_registry_contract(
    rows: list[object],
    name_field: str,
    expected_contract: dict[str, tuple[str, str]],
) -> None:
    database_contract = {
        row.get(name_field): (
            row.get("definition_version"),
            row.get("contract_hash"),
        )
        for row in rows
        if isinstance(row, dict)
    }
    if database_contract != expected_contract:
        raise ConfigurationControlError("CONFIG_REGISTRY_DRIFT", 503)


class ConfigurationControlService:
    """Validate values, create envelopes, and call narrow scoped RPCs."""

    def __init__(
        self,
        db: Any,
        material_service: SecretMaterialService,
    ) -> None:
        self._db = db
        self._material_service = material_service

    def set_platform(
        self,
        *,
        key: str,
        value: object,
        expected_version: int,
    ) -> dict[str, object]:
        return self._set(
            rpc_name="set_platform_configuration",
            scope_kind="platform",
            scope_id=None,
            key=key,
            value=value,
            expected_version=expected_version,
            extra_params={},
        )

    def set_organization(
        self,
        *,
        org_id: str,
        key: str,
        value: object,
        expected_version: int,
    ) -> dict[str, object]:
        return self._set(
            rpc_name="set_org_configuration",
            scope_kind="organization",
            scope_id=org_id,
            key=key,
            value=value,
            expected_version=expected_version,
            extra_params={"p_org_id": org_id},
        )

    def set_user(
        self,
        *,
        user_id: str,
        key: str,
        value: object,
        expected_version: int,
    ) -> dict[str, object]:
        return self._set(
            rpc_name="set_user_configuration",
            scope_kind="user",
            scope_id=user_id,
            key=key,
            value=value,
            expected_version=expected_version,
            extra_params={"p_user_id": user_id},
        )

    def delete_platform(
        self,
        *,
        key: str,
        expected_version: int,
    ) -> dict[str, object]:
        return self._delete(
            "delete_platform_configuration",
            key,
            expected_version,
            {},
        )

    def delete_organization(
        self,
        *,
        org_id: str,
        key: str,
        expected_version: int,
    ) -> dict[str, object]:
        return self._delete(
            "delete_org_configuration",
            key,
            expected_version,
            {"p_org_id": org_id},
        )

    def delete_user(
        self,
        *,
        key: str,
        expected_version: int,
    ) -> dict[str, object]:
        return self._delete(
            "delete_user_configuration",
            key,
            expected_version,
            {},
        )

    def list_platform_status(self) -> list[dict[str, object]]:
        return self._list_status("list_platform_configuration_status", {})

    def list_organization_status(
        self,
        *,
        org_id: str,
    ) -> list[dict[str, object]]:
        return self._list_status(
            "list_org_configuration_status",
            {"p_org_id": org_id},
        )

    def list_user_status(self) -> list[dict[str, object]]:
        return self._list_status("list_user_configuration_status", {})

    def _set(
        self,
        *,
        rpc_name: str,
        scope_kind: ScopeKind,
        scope_id: str | None,
        key: str,
        value: object,
        expected_version: int,
        extra_params: dict[str, object],
    ) -> dict[str, object]:
        definition = self._definition_for_scope(key, scope_kind)
        self._validate_expected_version(expected_version)
        value_json: object | None = None
        secret_envelope: dict[str, object] | None = None
        if definition.value_kind == "secret":
            payload = self._validate_secret_payload(definition, value)
            envelope = self._material_service.encrypt_payload(
                scope_kind=scope_kind,
                scope_id=scope_id,
                secret_name=definition.secret_name or "",
                payload_version=expected_version + 1,
                payload=payload,
            )
            secret_envelope = {
                "payload_ciphertext": envelope.payload_ciphertext,
                "wrapped_dek": envelope.wrapped_dek,
                "kek_version": envelope.kek_version,
            }
        else:
            value_json = self._validate_plain_value(definition, value)

        params = {
            **extra_params,
            "p_definition_version": CONFIG_REGISTRY.version,
            "p_config_key": key,
            "p_value_json": value_json,
            "p_secret_envelope": secret_envelope,
            "p_expected_version": expected_version,
        }
        return self._rpc_object(rpc_name, params)

    def _delete(
        self,
        rpc_name: str,
        key: str,
        expected_version: int,
        extra_params: dict[str, object],
    ) -> dict[str, object]:
        self._validate_expected_version(expected_version)
        return self._rpc_object(rpc_name, {
            **extra_params,
            "p_config_key": key,
            "p_expected_version": expected_version,
        })

    def _list_status(
        self,
        rpc_name: str,
        params: dict[str, object],
    ) -> list[dict[str, object]]:
        data = self._rpc(rpc_name, params)
        if not isinstance(data, list) or not all(
            isinstance(item, dict) for item in data
        ):
            raise ConfigurationControlError(
                "CONFIG_RESPONSE_INVALID",
                503,
            )
        return data

    def _rpc_object(
        self,
        rpc_name: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        data = self._rpc(rpc_name, params)
        if not isinstance(data, dict):
            raise ConfigurationControlError(
                "CONFIG_RESPONSE_INVALID",
                503,
            )
        return data

    def _rpc(self, rpc_name: str, params: dict[str, object]) -> object:
        try:
            return self._db.rpc(rpc_name, params).execute().data
        except Exception as error:
            self._raise_database_error(error)
            raise

    @staticmethod
    def _definition_for_scope(
        key: str,
        scope_kind: ScopeKind,
    ) -> ConfigDefinition:
        try:
            definition = CONFIG_REGISTRY.get(key)
        except KeyError as error:
            raise ConfigurationControlError("CONFIG_KEY_UNKNOWN", 400) from error
        if scope_kind not in definition.allowed_scopes:
            raise ConfigurationControlError(
                "CONFIG_SCOPE_FORBIDDEN",
                403,
            )
        return definition

    @staticmethod
    def _validate_expected_version(expected_version: int) -> None:
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise ConfigurationControlError("CONFIG_VALUE_INVALID", 400)

    @staticmethod
    def _validate_secret_payload(
        definition: ConfigDefinition,
        value: object,
    ) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ConfigurationControlError("CONFIG_VALUE_INVALID", 400)
        required = set(definition.validation.get("required", ()))
        if set(value) != required or any(
            not isinstance(item, str) or not item
            for item in value.values()
        ):
            raise ConfigurationControlError("CONFIG_VALUE_INVALID", 400)
        return value

    @staticmethod
    def _validate_plain_value(
        definition: ConfigDefinition,
        value: object,
    ) -> object:
        validation = definition.validation
        if definition.value_kind == "string":
            if not isinstance(value, str):
                raise ConfigurationControlError("CONFIG_VALUE_INVALID", 400)
            minimum = int(validation.get("min_length", 0))
            maximum = int(validation.get("max_length", 2**31 - 1))
            if not minimum <= len(value) <= maximum:
                raise ConfigurationControlError("CONFIG_VALUE_INVALID", 400)
            return value
        if definition.value_kind == "json":
            if not isinstance(value, Sequence) or isinstance(
                value,
                (str, bytes),
            ):
                raise ConfigurationControlError("CONFIG_VALUE_INVALID", 400)
            items = list(value)
            if validation.get("item_type") == "string" and not all(
                isinstance(item, str) and item for item in items
            ):
                raise ConfigurationControlError("CONFIG_VALUE_INVALID", 400)
            if validation.get("unique") and len(items) != len(set(items)):
                raise ConfigurationControlError("CONFIG_VALUE_INVALID", 400)
            return items
        raise ConfigurationControlError("CONFIG_VALUE_INVALID", 400)

    @staticmethod
    def _raise_database_error(error: Exception) -> None:
        message = str(error)
        mappings = (
            ("CONFIG_VERSION_CONFLICT", 409),
            ("CONFIG_SCOPE_FORBIDDEN", 403),
            ("CONFIG_PLATFORM_AUTHORITY_DENIED", 403),
            ("CONFIG_USER_AUTHORITY_DENIED", 403),
            ("GOVERNANCE_", 403),
            ("CONFIG_KEY_UNKNOWN", 400),
            ("CONFIG_VALUE_INVALID", 400),
        )
        for code, status in mappings:
            if code in message:
                raise ConfigurationControlError(code, status) from error
