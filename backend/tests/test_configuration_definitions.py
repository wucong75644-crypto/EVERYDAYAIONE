"""Canonical configuration registry contract tests."""

from __future__ import annotations

import hashlib
import json

import pytest

from services.configuration.definitions import (
    BUNDLE_DEFINITIONS,
    CONFIG_DEFINITIONS,
    CONFIG_REGISTRY,
    DEFINITION_VERSION,
    get_config_definition,
)


def test_registry_has_stable_version_and_unique_keys() -> None:
    assert DEFINITION_VERSION == "v1"
    assert len(CONFIG_DEFINITIONS) == 16
    assert tuple(CONFIG_DEFINITIONS) == tuple(
        definition.key for definition in CONFIG_DEFINITIONS.values()
    )


def test_every_contract_hash_matches_canonical_json() -> None:
    for definition in CONFIG_DEFINITIONS.values():
        contract_json = definition.contract_json()
        assert json.dumps(
            json.loads(contract_json),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ) == contract_json
        assert definition.contract_hash() == hashlib.sha256(
            contract_json.encode("utf-8")
        ).hexdigest()


def test_ai_keys_support_all_scopes_and_include_dashscope() -> None:
    ai_keys = {
        "ai.dashscope.api_key",
        "ai.openrouter.api_key",
        "ai.kie.api_key",
        "ai.google.api_key",
    }
    assert ai_keys <= set(CONFIG_DEFINITIONS)
    for key in ai_keys:
        definition = get_config_definition(key)
        assert definition.allowed_scopes == (
            "platform",
            "organization",
            "user",
        )
        assert definition.fallback_policy == "org_then_platform"
        assert definition.user_override == "org_policy"


def test_erp_token_pair_is_one_secret_payload() -> None:
    definition = get_config_definition("erp.token_pair")
    assert definition.value_kind == "secret"
    assert definition.validation["required"] == (
        "access_token",
        "refresh_token",
    )
    assert definition.bundles == ("erp.runtime",)
    assert CONFIG_REGISTRY.get_bundle("erp.runtime").config_keys == (
        "erp.app_credentials",
        "erp.token_pair",
        "erp.warehouse_ids",
    )
    assert CONFIG_REGISTRY.get_bundle("erp.runtime").optional_keys == (
        "erp.warehouse_ids",
    )


def test_wecom_oauth_public_bundle_never_contains_secret_material() -> None:
    public_bundle = CONFIG_REGISTRY.get_bundle("wecom.oauth.public")
    exchange_bundle = CONFIG_REGISTRY.get_bundle("wecom.oauth.exchange")

    assert public_bundle.required_keys == (
        "wecom.corp_id",
        "wecom.oauth_agent_id",
    )
    assert "wecom.oauth_agent_secret" not in public_bundle.config_keys
    assert exchange_bundle.required_keys == (
        "wecom.corp_id",
        "wecom.oauth_agent_secret",
    )
    assert get_config_definition("wecom.oauth_agent_id").value_kind == "string"
    assert (
        get_config_definition("wecom.oauth_agent_secret").value_kind
        == "secret"
    )


def test_wecom_callback_bundle_reuses_enterprise_agent_credentials() -> None:
    bundle = CONFIG_REGISTRY.get_bundle("wecom.callback")
    assert bundle.required_keys == (
        "wecom.corp_id",
        "wecom.callback_credentials",
        "wecom.oauth_agent_id",
        "wecom.oauth_agent_secret",
    )
    assert bundle.allowed_consumers == ("worker_org",)


def test_wecom_app_bundle_reuses_only_existing_enterprise_agent_credentials() -> None:
    bundle = CONFIG_REGISTRY.get_bundle("wecom.app")

    assert bundle.required_keys == (
        "wecom.corp_id",
        "wecom.oauth_agent_id",
        "wecom.oauth_agent_secret",
    )
    assert bundle.optional_keys == ()
    assert bundle.allowed_consumers == ("wecom_runtime",)
    for key in bundle.required_keys:
        assert "wecom.app" in get_config_definition(key).bundles


def test_non_ai_integrations_are_organization_only() -> None:
    for key, definition in CONFIG_DEFINITIONS.items():
        if key.startswith("ai."):
            continue
        assert definition.allowed_scopes == ("organization",)
        assert definition.fallback_policy == "none"
        assert definition.user_override == "deny"


def test_unknown_key_fails_closed_and_registry_is_read_only() -> None:
    with pytest.raises(KeyError):
        get_config_definition("unknown.key")
    with pytest.raises(TypeError):
        CONFIG_DEFINITIONS["unknown.key"] = get_config_definition(  # type: ignore[index]
            "erp.token_pair"
        )
    with pytest.raises(KeyError):
        CONFIG_REGISTRY.get_bundle("unknown.bundle")
    assert set(BUNDLE_DEFINITIONS) >= {
        "erp.runtime",
        "wecom.bot",
        "wecom.oauth.public",
        "kuaimai_external.thinktank",
    }
