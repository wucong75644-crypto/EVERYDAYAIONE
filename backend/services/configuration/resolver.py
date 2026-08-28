"""Strict parsing of database-selected effective configuration facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from services.configuration.definitions import (
    CONFIG_REGISTRY,
    BundleDefinition,
    ConfigDefinition,
    ScopeKind,
)
from services.configuration.envelope import SecretEnvelope


class ConfigurationResolutionError(RuntimeError):
    """Stable failure for malformed, incomplete, or unavailable Bundles."""


@dataclass(frozen=True)
class SecretReference:
    """Scope-bound encrypted material returned by one fixed database facade."""

    key: str
    source: ScopeKind
    scope_id: str | None
    secret_name: str
    envelope: SecretEnvelope


@dataclass(frozen=True)
class EffectiveConfigItem:
    """One selected ordinary value, SecretRef, or missing optional item."""

    key: str
    required: bool
    configured: bool
    source: ScopeKind | None
    version: int
    value: object | None = None
    secret_ref: SecretReference | None = None


@dataclass(frozen=True)
class EffectiveConfigBundle:
    """Validated effective facts before Secret material decryption."""

    name: str
    items: Mapping[str, EffectiveConfigItem]


class EffectiveConfigResolver:
    """Validate the exact response contract of a fixed Bundle facade."""

    def parse(
        self,
        bundle_name: str,
        data: object,
    ) -> EffectiveConfigBundle:
        try:
            bundle = CONFIG_REGISTRY.get_bundle(bundle_name)
        except KeyError as error:
            raise ConfigurationResolutionError(
                "CONFIG_BUNDLE_UNKNOWN"
            ) from error
        if (
            not isinstance(data, dict)
            or data.get("bundle") != bundle_name
            or data.get("definition_version") != CONFIG_REGISTRY.version
            or not isinstance(data.get("items"), list)
        ):
            raise ConfigurationResolutionError("CONFIG_BUNDLE_RESPONSE_INVALID")
        raw_items = data["items"]
        response_keys = [
            item.get("key") for item in raw_items if isinstance(item, dict)
        ]
        if response_keys != list(bundle.config_keys):
            raise ConfigurationResolutionError("CONFIG_BUNDLE_RESPONSE_INVALID")
        items = {
            key: self._parse_item(
                CONFIG_REGISTRY.get(key),
                key in bundle.required_keys,
                raw,
            )
            for key, raw in zip(bundle.config_keys, raw_items, strict=True)
        }
        return EffectiveConfigBundle(
            name=bundle.name,
            items=MappingProxyType(items),
        )

    def _parse_item(
        self,
        definition: ConfigDefinition,
        required: bool,
        raw: object,
    ) -> EffectiveConfigItem:
        if (
            not isinstance(raw, dict)
            or raw.get("key") != definition.key
            or raw.get("required") is not required
            or not isinstance(raw.get("configured"), bool)
        ):
            raise ConfigurationResolutionError("CONFIG_BUNDLE_RESPONSE_INVALID")
        if not raw["configured"]:
            if required:
                raise ConfigurationResolutionError("CONFIG_BUNDLE_INCOMPLETE")
            return EffectiveConfigItem(
                key=definition.key,
                required=False,
                configured=False,
                source=None,
                version=0,
            )
        source = self._parse_source(raw.get("source"), raw.get("scope_id"))
        version = raw.get("version")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version < 1
            or raw.get("value_kind") != definition.value_kind
        ):
            raise ConfigurationResolutionError("CONFIG_BUNDLE_RESPONSE_INVALID")
        if definition.value_kind == "secret":
            secret_ref = self._parse_secret_ref(
                definition,
                source,
                raw.get("scope_id"),
                version,
                raw.get("secret_ref"),
            )
            return EffectiveConfigItem(
                definition.key,
                required,
                True,
                source,
                version,
                secret_ref=secret_ref,
            )
        value = self._validate_plain_value(definition, raw.get("value_json"))
        return EffectiveConfigItem(
            definition.key,
            required,
            True,
            source,
            version,
            value=value,
        )

    @staticmethod
    def _parse_source(source: object, scope_id: object) -> ScopeKind:
        if source == "platform":
            if scope_id is not None:
                raise ConfigurationResolutionError(
                    "CONFIG_BUNDLE_RESPONSE_INVALID"
                )
            return "platform"
        if source not in ("organization", "user") or not isinstance(
            scope_id,
            str,
        ):
            raise ConfigurationResolutionError("CONFIG_BUNDLE_RESPONSE_INVALID")
        try:
            UUID(scope_id)
        except ValueError as error:
            raise ConfigurationResolutionError(
                "CONFIG_BUNDLE_RESPONSE_INVALID"
            ) from error
        return source

    @staticmethod
    def _parse_secret_ref(
        definition: ConfigDefinition,
        source: ScopeKind,
        scope_id: object,
        version: int,
        raw: object,
    ) -> SecretReference:
        expected_fields = {
            "secret_name",
            "payload_ciphertext",
            "wrapped_dek",
            "kek_version",
            "payload_version",
        }
        if (
            not isinstance(raw, dict)
            or set(raw) != expected_fields
            or raw.get("secret_name") != definition.secret_name
            or raw.get("payload_version") != version
            or any(
                not isinstance(raw.get(field), str) or not raw.get(field)
                for field in (
                    "payload_ciphertext",
                    "wrapped_dek",
                    "kek_version",
                )
            )
        ):
            raise ConfigurationResolutionError("CONFIG_BUNDLE_RESPONSE_INVALID")
        return SecretReference(
            key=definition.key,
            source=source,
            scope_id=scope_id if isinstance(scope_id, str) else None,
            secret_name=definition.secret_name or "",
            envelope=SecretEnvelope(
                payload_ciphertext=raw["payload_ciphertext"],
                wrapped_dek=raw["wrapped_dek"],
                kek_version=raw["kek_version"],
                payload_version=version,
            ),
        )

    @staticmethod
    def _validate_plain_value(
        definition: ConfigDefinition,
        value: object,
    ) -> object:
        validation = definition.validation
        if definition.value_kind == "string":
            minimum = int(validation.get("min_length", 0))
            maximum = int(validation.get("max_length", 2**31 - 1))
            if not isinstance(value, str) or not minimum <= len(value) <= maximum:
                raise ConfigurationResolutionError(
                    "CONFIG_BUNDLE_RESPONSE_INVALID"
                )
            return value
        if definition.value_kind == "json":
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                raise ConfigurationResolutionError(
                    "CONFIG_BUNDLE_RESPONSE_INVALID"
                )
            items = list(value)
            if validation.get("item_type") == "string" and not all(
                isinstance(item, str) and item for item in items
            ):
                raise ConfigurationResolutionError(
                    "CONFIG_BUNDLE_RESPONSE_INVALID"
                )
            if validation.get("unique") and len(items) != len(set(items)):
                raise ConfigurationResolutionError(
                    "CONFIG_BUNDLE_RESPONSE_INVALID"
                )
            return items
        raise ConfigurationResolutionError("CONFIG_BUNDLE_RESPONSE_INVALID")
