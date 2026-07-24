"""Effective configuration response parsing tests."""

from __future__ import annotations

import pytest

from services.configuration.resolver import (
    ConfigurationResolutionError,
    EffectiveConfigResolver,
)


ORG_ID = "10000000-0000-0000-0000-000000000001"


def _secret_item(
    key: str,
    *,
    source: str = "organization",
    scope_id: str | None = ORG_ID,
    version: int = 2,
) -> dict[str, object]:
    secret_names = {
        "erp.app_credentials": "erp.app_credentials",
        "erp.token_pair": "erp.token_pair",
    }
    return {
        "key": key,
        "required": True,
        "configured": True,
        "source": source,
        "scope_id": scope_id,
        "version": version,
        "value_kind": "secret",
        "secret_ref": {
            "secret_name": secret_names[key],
            "payload_ciphertext": f"cipher-{key}",
            "wrapped_dek": f"wrapped-{key}",
            "kek_version": "local-v1",
            "payload_version": version,
        },
    }


def _erp_response() -> dict[str, object]:
    return {
        "bundle": "erp.runtime",
        "definition_version": "v1",
        "items": [
            _secret_item("erp.app_credentials"),
            _secret_item("erp.token_pair"),
            {
                "key": "erp.warehouse_ids",
                "required": False,
                "configured": False,
            },
        ],
    }


def test_parse_valid_bundle_preserves_secret_refs_and_optional_missing() -> None:
    result = EffectiveConfigResolver().parse("erp.runtime", _erp_response())

    assert tuple(result.items) == (
        "erp.app_credentials",
        "erp.token_pair",
        "erp.warehouse_ids",
    )
    app = result.items["erp.app_credentials"]
    assert app.source == "organization"
    assert app.secret_ref is not None
    assert app.secret_ref.secret_name == "erp.app_credentials"
    warehouse = result.items["erp.warehouse_ids"]
    assert warehouse.configured is False
    assert warehouse.version == 0


def test_parse_valid_public_bundle_contains_only_ordinary_values() -> None:
    data = {
        "bundle": "wecom.oauth.public",
        "definition_version": "v1",
        "items": [
            {
                "key": "wecom.corp_id",
                "required": True,
                "configured": True,
                "source": "organization",
                "scope_id": ORG_ID,
                "version": 1,
                "value_kind": "string",
                "value_json": "corp-1",
            },
            {
                "key": "wecom.oauth_agent_id",
                "required": True,
                "configured": True,
                "source": "organization",
                "scope_id": ORG_ID,
                "version": 1,
                "value_kind": "string",
                "value_json": "agent-1",
            },
        ],
    }

    result = EffectiveConfigResolver().parse("wecom.oauth.public", data)

    assert result.items["wecom.corp_id"].value == "corp-1"
    assert result.items["wecom.oauth_agent_id"].secret_ref is None


def test_platform_secret_source_requires_null_scope_id() -> None:
    data = {
        "bundle": "ai.provider.dashscope",
        "definition_version": "v1",
        "items": [{
            "key": "ai.dashscope.api_key",
            "required": True,
            "configured": True,
            "source": "platform",
            "scope_id": None,
            "version": 1,
            "value_kind": "secret",
            "secret_ref": {
                "secret_name": "ai.dashscope_api_key",
                "payload_ciphertext": "cipher",
                "wrapped_dek": "wrapped",
                "kek_version": "local-v1",
                "payload_version": 1,
            },
        }],
    }

    result = EffectiveConfigResolver().parse(
        "ai.provider.dashscope",
        data,
    )

    assert result.items["ai.dashscope.api_key"].source == "platform"
    assert result.items["ai.dashscope.api_key"].secret_ref.scope_id is None


@pytest.mark.parametrize(
    "mutate",
    (
        lambda data: data.update(bundle="erp.other"),
        lambda data: data.update(definition_version="v2"),
        lambda data: data["items"].reverse(),
        lambda data: data["items"][0].update(scope_id="not-a-uuid"),
        lambda data: data["items"][0]["secret_ref"].update(payload_version=3),
        lambda data: data["items"][0]["secret_ref"].update(extra="forbidden"),
        lambda data: data["items"][2].update(required=True),
    ),
)
def test_parse_rejects_drift_scope_and_envelope_mismatch(mutate) -> None:
    data = _erp_response()
    mutate(data)

    with pytest.raises(
        ConfigurationResolutionError,
        match="CONFIG_BUNDLE_RESPONSE_INVALID",
    ):
        EffectiveConfigResolver().parse("erp.runtime", data)


def test_required_missing_fails_closed() -> None:
    data = _erp_response()
    data["items"][0] = {
        "key": "erp.app_credentials",
        "required": True,
        "configured": False,
    }

    with pytest.raises(
        ConfigurationResolutionError,
        match="CONFIG_BUNDLE_INCOMPLETE",
    ):
        EffectiveConfigResolver().parse("erp.runtime", data)
