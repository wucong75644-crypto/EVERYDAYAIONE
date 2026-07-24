"""Canonical configuration definitions shared by migrations and runtime."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any, Literal, Mapping


DEFINITION_VERSION = "v1"

ScopeKind = Literal["platform", "organization", "user"]
ValueKind = Literal["string", "integer", "boolean", "json", "secret"]
FallbackPolicy = Literal["none", "platform", "org_then_platform"]
UserOverride = Literal["allow", "deny", "org_policy"]
BundleConsumer = Literal[
    "runtime_actor",
    "runtime_org",
    "runtime_oauth",
    "runtime_org_admin",
    "wecom_runtime",
    "worker_org",
]


@dataclass(frozen=True)
class ConfigDefinition:
    """Immutable contract for one stable configuration key."""

    key: str
    value_kind: ValueKind
    allowed_scopes: tuple[ScopeKind, ...]
    fallback_policy: FallbackPolicy
    user_override: UserOverride
    validation: Mapping[str, Any]
    bundles: tuple[str, ...]
    secret_name: str | None = None

    def __post_init__(self) -> None:
        frozen_validation = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in self.validation.items()
        }
        object.__setattr__(
            self,
            "validation",
            MappingProxyType(frozen_validation),
        )

    def contract(self) -> dict[str, Any]:
        """Return the JSON-safe database projection."""
        return {
            "allowed_scopes": list(self.allowed_scopes),
            "bundles": list(self.bundles),
            "fallback_policy": self.fallback_policy,
            "key": self.key,
            "secret_name": self.secret_name,
            "user_override": self.user_override,
            "validation": {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in self.validation.items()
            },
            "value_kind": self.value_kind,
        }

    def contract_json(self) -> str:
        """Return canonical JSON used for the immutable contract hash."""
        return json.dumps(
            self.contract(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    def contract_hash(self) -> str:
        """Return the SHA-256 hash persisted with the database projection."""
        return hashlib.sha256(self.contract_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BundleDefinition:
    """Fixed required/optional keys exposed to named runtime consumers."""

    name: str
    required_keys: tuple[str, ...]
    optional_keys: tuple[str, ...]
    allowed_consumers: tuple[BundleConsumer, ...]

    @property
    def config_keys(self) -> tuple[str, ...]:
        """Return all keys in stable required-then-optional order."""
        return self.required_keys + self.optional_keys

    def contract(self) -> dict[str, object]:
        """Return the JSON-safe database projection."""
        return {
            "allowed_consumers": list(self.allowed_consumers),
            "name": self.name,
            "optional_keys": list(self.optional_keys),
            "required_keys": list(self.required_keys),
        }

    def contract_json(self) -> str:
        """Return canonical JSON used for the immutable contract hash."""
        return json.dumps(
            self.contract(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    def contract_hash(self) -> str:
        """Return the SHA-256 hash persisted with the bundle projection."""
        return hashlib.sha256(self.contract_json().encode("utf-8")).hexdigest()


class ConfigDefinitionRegistry:
    """Read-only definition and Bundle index for one contract version."""

    def __init__(
        self,
        version: str,
        definitions: tuple[ConfigDefinition, ...],
        bundles: tuple[BundleDefinition, ...],
    ) -> None:
        definitions_by_key = {
            definition.key: definition for definition in definitions
        }
        if len(definitions_by_key) != len(definitions):
            raise ValueError("duplicate configuration definition key")
        if any(not definition.bundles for definition in definitions):
            raise ValueError("configuration definition requires a bundle")
        if any(
            (definition.value_kind == "secret")
            is (definition.secret_name is None)
            for definition in definitions
        ):
            raise ValueError("secret definition and secret_name mismatch")

        bundles_by_name = {bundle.name: bundle for bundle in bundles}
        if len(bundles_by_name) != len(bundles):
            raise ValueError("duplicate bundle definition name")
        if any(
            not bundle.required_keys
            or not bundle.allowed_consumers
            or len(set(bundle.config_keys)) != len(bundle.config_keys)
            for bundle in bundles
        ):
            raise ValueError("invalid bundle definition")
        referenced_keys = {
            key for bundle in bundles for key in bundle.config_keys
        }
        if referenced_keys != set(definitions_by_key):
            raise ValueError("bundle and configuration keys mismatch")
        for definition in definitions:
            if set(definition.bundles) != {
                bundle.name
                for bundle in bundles
                if definition.key in bundle.config_keys
            }:
                raise ValueError("bundle membership mismatch")

        self.version = version
        self.definitions: Mapping[str, ConfigDefinition] = MappingProxyType(
            definitions_by_key
        )
        self.bundles: Mapping[str, BundleDefinition] = MappingProxyType(
            bundles_by_name
        )

    def get(self, key: str) -> ConfigDefinition:
        """Return a definition by stable key, raising KeyError when unknown."""
        return self.definitions[key]

    def get_bundle(self, name: str) -> BundleDefinition:
        """Return a fixed Bundle, raising KeyError when unknown."""
        return self.bundles[name]


def _secret(
    key: str,
    *,
    scopes: tuple[ScopeKind, ...],
    fallback: FallbackPolicy,
    user_override: UserOverride,
    secret_name: str,
    payload_fields: tuple[str, ...],
    bundles: tuple[str, ...],
) -> ConfigDefinition:
    return ConfigDefinition(
        key=key,
        value_kind="secret",
        allowed_scopes=scopes,
        fallback_policy=fallback,
        user_override=user_override,
        secret_name=secret_name,
        validation={
            "payload_fields": list(payload_fields),
            "required": list(payload_fields),
        },
        bundles=bundles,
    )


_AI_SCOPES: tuple[ScopeKind, ...] = ("platform", "organization", "user")
_ORG_SCOPE: tuple[ScopeKind, ...] = ("organization",)

_DEFINITIONS = (
    _secret(
        "ai.dashscope.api_key",
        scopes=_AI_SCOPES,
        fallback="org_then_platform",
        user_override="org_policy",
        secret_name="ai.dashscope_api_key",
        payload_fields=("api_key",),
        bundles=("ai.provider.dashscope",),
    ),
    _secret(
        "ai.openrouter.api_key",
        scopes=_AI_SCOPES,
        fallback="org_then_platform",
        user_override="org_policy",
        secret_name="ai.openrouter_api_key",
        payload_fields=("api_key",),
        bundles=("ai.provider.openrouter",),
    ),
    _secret(
        "ai.kie.api_key",
        scopes=_AI_SCOPES,
        fallback="org_then_platform",
        user_override="org_policy",
        secret_name="ai.kie_api_key",
        payload_fields=("api_key",),
        bundles=("ai.provider.kie",),
    ),
    _secret(
        "ai.google.api_key",
        scopes=_AI_SCOPES,
        fallback="org_then_platform",
        user_override="org_policy",
        secret_name="ai.google_api_key",
        payload_fields=("api_key",),
        bundles=("ai.provider.google",),
    ),
    _secret(
        "erp.app_credentials",
        scopes=_ORG_SCOPE,
        fallback="none",
        user_override="deny",
        secret_name="erp.app_credentials",
        payload_fields=("app_key", "app_secret"),
        bundles=("erp.runtime",),
    ),
    _secret(
        "erp.token_pair",
        scopes=_ORG_SCOPE,
        fallback="none",
        user_override="deny",
        secret_name="erp.token_pair",
        payload_fields=("access_token", "refresh_token"),
        bundles=("erp.runtime",),
    ),
    ConfigDefinition(
        key="erp.warehouse_ids",
        value_kind="json",
        allowed_scopes=_ORG_SCOPE,
        fallback_policy="none",
        user_override="deny",
        validation={"item_type": "string", "type": "array", "unique": True},
        bundles=("erp.runtime",),
    ),
    ConfigDefinition(
        key="wecom.corp_id",
        value_kind="string",
        allowed_scopes=_ORG_SCOPE,
        fallback_policy="none",
        user_override="deny",
        validation={"max_length": 100, "min_length": 1},
        bundles=(
            "wecom.bot",
            "wecom.contact",
            "wecom.oauth.public",
            "wecom.oauth.exchange",
        ),
    ),
    _secret(
        "wecom.bot_credentials",
        scopes=_ORG_SCOPE,
        fallback="none",
        user_override="deny",
        secret_name="wecom.bot_credentials",
        payload_fields=("bot_id", "bot_secret"),
        bundles=("wecom.bot",),
    ),
    ConfigDefinition(
        key="wecom.oauth_agent_id",
        value_kind="string",
        allowed_scopes=_ORG_SCOPE,
        fallback_policy="none",
        user_override="deny",
        validation={"max_length": 100, "min_length": 1},
        bundles=("wecom.oauth.public",),
    ),
    _secret(
        "wecom.oauth_agent_secret",
        scopes=_ORG_SCOPE,
        fallback="none",
        user_override="deny",
        secret_name="wecom.oauth_agent_secret",
        payload_fields=("agent_secret",),
        bundles=("wecom.contact", "wecom.oauth.exchange"),
    ),
    _secret(
        "kuaimai_external.thinktank.cookie",
        scopes=_ORG_SCOPE,
        fallback="none",
        user_override="deny",
        secret_name="kuaimai_external.thinktank_cookie",
        payload_fields=("censeid_cookie", "cookie_full"),
        bundles=("kuaimai_external.thinktank",),
    ),
    ConfigDefinition(
        key="kuaimai_external.thinktank.company_id",
        value_kind="string",
        allowed_scopes=_ORG_SCOPE,
        fallback_policy="none",
        user_override="deny",
        validation={"max_length": 100, "min_length": 1},
        bundles=("kuaimai_external.thinktank",),
    ),
    _secret(
        "kuaimai_external.viperp.cookie",
        scopes=_ORG_SCOPE,
        fallback="none",
        user_override="deny",
        secret_name="kuaimai_external.viperp_cookie",
        payload_fields=("censeid_cookie", "cookie_full"),
        bundles=("kuaimai_external.viperp",),
    ),
    ConfigDefinition(
        key="kuaimai_external.viperp.company_id",
        value_kind="string",
        allowed_scopes=_ORG_SCOPE,
        fallback_policy="none",
        user_override="deny",
        validation={"max_length": 100, "min_length": 1},
        bundles=("kuaimai_external.viperp",),
    ),
)

_BUNDLES = (
    BundleDefinition(
        name="ai.provider.dashscope",
        required_keys=("ai.dashscope.api_key",),
        optional_keys=(),
        allowed_consumers=("runtime_actor",),
    ),
    BundleDefinition(
        name="ai.provider.openrouter",
        required_keys=("ai.openrouter.api_key",),
        optional_keys=(),
        allowed_consumers=("runtime_actor",),
    ),
    BundleDefinition(
        name="ai.provider.kie",
        required_keys=("ai.kie.api_key",),
        optional_keys=(),
        allowed_consumers=("runtime_actor",),
    ),
    BundleDefinition(
        name="ai.provider.google",
        required_keys=("ai.google.api_key",),
        optional_keys=(),
        allowed_consumers=("runtime_actor",),
    ),
    BundleDefinition(
        name="erp.runtime",
        required_keys=("erp.app_credentials", "erp.token_pair"),
        optional_keys=("erp.warehouse_ids",),
        allowed_consumers=("runtime_org", "worker_org"),
    ),
    BundleDefinition(
        name="wecom.bot",
        required_keys=("wecom.corp_id", "wecom.bot_credentials"),
        optional_keys=(),
        allowed_consumers=("worker_org",),
    ),
    BundleDefinition(
        name="wecom.oauth.public",
        required_keys=("wecom.corp_id", "wecom.oauth_agent_id"),
        optional_keys=(),
        allowed_consumers=("runtime_oauth",),
    ),
    BundleDefinition(
        name="wecom.oauth.exchange",
        required_keys=("wecom.corp_id", "wecom.oauth_agent_secret"),
        optional_keys=(),
        allowed_consumers=("runtime_oauth",),
    ),
    BundleDefinition(
        name="wecom.contact",
        required_keys=("wecom.corp_id", "wecom.oauth_agent_secret"),
        optional_keys=(),
        allowed_consumers=("wecom_runtime",),
    ),
    BundleDefinition(
        name="kuaimai_external.thinktank",
        required_keys=(
            "kuaimai_external.thinktank.cookie",
            "kuaimai_external.thinktank.company_id",
        ),
        optional_keys=(),
        allowed_consumers=("runtime_org_admin", "worker_org"),
    ),
    BundleDefinition(
        name="kuaimai_external.viperp",
        required_keys=(
            "kuaimai_external.viperp.cookie",
            "kuaimai_external.viperp.company_id",
        ),
        optional_keys=(),
        allowed_consumers=("runtime_org_admin", "worker_org"),
    ),
)

CONFIG_REGISTRY = ConfigDefinitionRegistry(
    DEFINITION_VERSION,
    _DEFINITIONS,
    _BUNDLES,
)
CONFIG_DEFINITIONS = CONFIG_REGISTRY.definitions
BUNDLE_DEFINITIONS = CONFIG_REGISTRY.bundles


def get_config_definition(key: str) -> ConfigDefinition:
    """Return a definition by stable key, raising KeyError when unknown."""
    return CONFIG_REGISTRY.get(key)
